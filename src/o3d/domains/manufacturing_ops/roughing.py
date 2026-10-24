"""Conservative constant-Z 3D rough/rest clearing from the actual CAD mesh.

All material projected above a layer remains protected, so overhangs are not
machined from below. GEOS clips/offsets each layer, and actual predecessor sweeps
define remaining stock for rest operations. This is not adaptive engagement CAM.
"""
from __future__ import annotations

import math

from .cam import Path, depths, geos, milling, parts, polygon
from .util import cancelled


def project_above(triangles, z):
    """Clip triangles in 3D at the cutter floor, then union their XY projections."""
    _, Polygon, *_ = geos()
    from shapely.ops import unary_union
    regions = []
    for triangle in triangles:
        loop = [list(p) for p in triangle]
        clipped = []
        for a,b in zip(loop,loop[1:]+loop[:1]):
            a_in,b_in = a[2] >= z-1e-9,b[2] >= z-1e-9
            if a_in:
                clipped.append(a)
            if a_in != b_in:
                t = (z-a[2])/(b[2]-a[2])
                clipped.append([a[i]+t*(b[i]-a[i]) for i in range(3)])
        if len(clipped) >= 3:
            region = Polygon([p[:2] for p in clipped])
            if region.is_valid and region.area > 1e-10:
                regions.append(region)
    return unary_union(regions)


def swept_floor(paths, tools, z):
    _,_,LineString,Point,_,union = geos()
    sweeps = []
    for path in paths:
        tool = tools[path.tool_id]
        # Ball/turning/probing motion cannot be reused as flat cylindrical stock cuts.
        if tool.kind not in {"flat_endmill","drill"}:
            raise ValueError("Rest stock accepts only flat-cutter/drill predecessors")
        for a,b in zip(path.moves,path.moves[1:]):
            axial = abs(a["x"]-b["x"])+abs(a["y"]-b["y"]) < 1e-9
            reaches = min(a["z"],b["z"]) <= z+1e-7 if axial else max(a["z"],b["z"]) <= z+1e-7
            if b["motion"] == "feed" and reaches:
                line = LineString([(a["x"],a["y"]),(b["x"],b["y"])])
                if line.length < 1e-10:
                    line = Point(b["x"],b["y"])
                sweeps.append(line.buffer(tool.diameter/2,quad_segs=48))
    return union(sweeps)


def rough(mesh_path, op, setup, tool, machine, predecessor_paths, tools, cancel=None):
    if machine.process != "milling" or tool.kind != "flat_endmill":
        raise ValueError("3D layer roughing requires a flat end mill and milling setup")
    if setup.wcs_rotation_z_deg or any(abs(v) > 1e-8 for v in setup.wcs_origin):
        raise ValueError("Current mesh layer roughing requires design coordinates equal setup coordinates")
    if op.bottom_z < setup.stock.minimum[2] or op.top_z > setup.stock.maximum[2]:
        raise ValueError("Roughing depths exceed stock")
    if op.strategy == "rest_3d" and not predecessor_paths:
        raise ValueError("Rest machining requires saved predecessor operations and their actual cutter sweeps")
    if tool.spindle_rpm > machine.max_spindle_rpm or max(tool.feed_mm_min,tool.plunge_mm_min) > machine.max_feed_mm_min:
        raise ValueError("Roughing feed/spindle exceeds machine limits")
    import trimesh
    mesh = trimesh.load(mesh_path,force="mesh",process=True)
    if not mesh.is_watertight or not mesh.is_winding_consistent or len(mesh.faces) > 50000:
        raise ValueError("Layer roughing requires a closed mesh with at most 50,000 triangles")
    _,_,_,_,box,_ = geos()
    stock_xy = box(*setup.stock.minimum[:2],*setup.stock.maximum[:2])
    domain = polygon(op.boundary,op.islands) if op.boundary else stock_xy
    domain = domain.intersection(stock_xy)
    if domain.is_empty:
        raise ValueError("Roughing region does not intersect stock")
    combined = Path(op.id,setup.id,tool.id,op.strategy)
    reports = []
    last_z = op.top_z
    for z in depths(op.top_z,op.bottom_z,op.stepdown):
        if cancelled(cancel):
            raise RuntimeError("Layer roughing cancelled")
        keep = project_above(mesh.triangles,z).buffer(op.allowance_mm,quad_segs=48)
        removable = domain.difference(keep)
        prior_removed = swept_floor(predecessor_paths,tools,z).intersection(removable) if predecessor_paths else removable.difference(removable)
        residual_before = removable.difference(prior_removed)
        # Generate admissible full contour passes, then keep only passes whose
        # actual swept cutter reaches predecessor residual. Eroding the residual
        # itself would incorrectly reject corner stock narrower than a diameter.
        region = removable
        if not region.is_valid:
            raise ValueError("Roughing region is geometrically invalid")
        layer_paths = []
        for part in parts(region):
            if part.geom_type != "Polygon" or part.buffer(-tool.diameter/2).is_empty:
                continue
            layer = op.model_copy(update={"strategy":"pocket", "boundary":list(part.exterior.coords),
                "islands":[list(h.coords) for h in part.interiors], "top_z":last_z,"bottom_z":z,
                "stepdown":last_z-z,"allowance_mm":0})
            generated = milling(layer,setup,tool,cancel)
            if op.strategy == "rest_3d":
                selected = Path(op.id,setup.id,tool.id,op.strategy)
                group = []
                cutting = False
                for move in generated.moves:
                    group.append(move)
                    if move["motion"] == "feed":
                        cutting = True
                    elif cutting:
                        candidate = Path(op.id,setup.id,tool.id,op.strategy,moves=group)
                        if swept_floor([candidate],{tool.id:tool},z).intersection(residual_before).area > setup.tolerance_mm**2:
                            selected.moves.extend(group)
                        group,cutting = [],False
                generated = selected
            layer_paths.append(generated)
            for move in generated.moves:
                combined.add(move["motion"],move["x"],move["y"],move["z"],move.get("feed"))
        removed = swept_floor(layer_paths,{tool.id:tool},z).intersection(stock_xy)
        gouge = removed.intersection(keep.buffer(-setup.tolerance_mm))
        if gouge.area > setup.tolerance_mm**2:
            raise ValueError("Layer roughing cutter sweep intersects the protected model")
        reachable = removable.buffer(-tool.diameter/2,quad_segs=48).buffer(tool.diameter/2,quad_segs=48)
        remaining = reachable.difference(removed.union(prior_removed))
        reports.append({"z_mm":z,"layer_height_mm":last_z-z,"protected_model_area_mm2":keep.area,
                        "removable_area_mm2":removable.area,"predecessor_removed_area_mm2":prior_removed.area,
                        "new_removed_area_mm2":removed.difference(prior_removed).area,
                        "remaining_reachable_area_mm2":remaining.area,"sampled_gouge_area_mm2":gouge.area})
        last_z = z
    if not combined.moves:
        raise ValueError("No reachable rough/rest material remains for this tool")
    combined.notes += ["Constant-Z mesh projection clearing; protects all projected material above each cut", "Actual predecessor swept-cylinder layers for rest; no adaptive engagement/load control"]
    return combined,{"engine":"triangle half-space clipping + GEOS layer offsets/sweeps", "layers":reports,
                     "predecessor_operation_ids":[p.operation_id for p in predecessor_paths],
                     "removed_volume_layer_estimate_mm3":sum(r["new_removed_area_mm2"]*r["layer_height_mm"] for r in reports),
                     "gouge_area_mm2":sum(r["sampled_gouge_area_mm2"] for r in reports),
                     "adaptive_engagement":False, "continuous_3d_stock_verified":False,
                     "limits":["Conservative layer projection cannot cut under overhangs", "Layer-quantized stock volume estimate", "No force, workholding or adaptive-engagement qualification"]}
