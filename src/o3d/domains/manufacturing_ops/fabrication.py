"""Deterministic bounded polygon packing and kerf-offset 2D laser contours."""
from __future__ import annotations

import io
import math

from .cam import geos, parts, polygon
from .util import cancelled


def nest(spec, cancel=None):
    shapely, _, _, _, box, _ = geos()
    from shapely.affinity import rotate, translate
    if not spec.parts:
        raise ValueError("Nesting needs at least one part")
    if sum(p.quantity for p in spec.parts) > 500:
        raise ValueError("This nesting engine is limited to 500 instances")
    usable = box(spec.margin_mm, spec.margin_mm, spec.sheet_width_mm-spec.margin_mm, spec.sheet_height_mm-spec.margin_mm)
    if usable.is_empty or spec.sheet_width_mm <= 2*spec.margin_mm or spec.sheet_height_mm <= 2*spec.margin_mm:
        raise ValueError("Sheet margins leave no usable material")
    items = []
    for part in spec.parts:
        if part.material != spec.material or abs(part.thickness_mm-spec.thickness_mm) > 1e-8:
            raise ValueError("A nest contains exactly one material/thickness group")
        shape = polygon(part.boundary, part.holes)
        for i in range(part.quantity):
            items.append((part, i, shape))
    items.sort(key=lambda p: (-p[2].area, p[0].id, p[1]))
    sheets = []
    placements = []
    clearance = spec.gap_mm+spec.kerf_mm
    for part, instance, original in items:
        if cancelled(cancel):
            raise RuntimeError("Manufacturing cancelled")
        variants = []
        for angle in sorted(set(part.rotations_deg)):
            oriented = rotate(original, angle, origin=(0, 0))
            xmin, ymin, _, _ = oriented.bounds
            variants.append((angle, translate(oriented, -xmin, -ymin)))
        chosen = None
        for sheet_index in range(len(sheets)+1):
            if sheet_index == len(sheets):
                if len(sheets) >= spec.max_sheets:
                    break
                occupied = []
            else:
                occupied = sheets[sheet_index]
            xs = {spec.margin_mm+spec.kerf_mm/2}
            ys = {spec.margin_mm+spec.kerf_mm/2}
            for other in occupied:
                x0, y0, x1, y1 = other.bounds
                xs.update([x0, x1+clearance])
                ys.update([y0, y1+clearance])
            candidates = []
            for y in sorted(ys):
                for x in sorted(xs):
                    for angle, oriented in variants:
                        candidate = translate(oriented, x, y)
                        if not usable.covers(candidate.buffer(spec.kerf_mm/2, quad_segs=24)):
                            continue
                        if any(candidate.distance(other) < clearance-1e-7 or candidate.intersection(other).area > 1e-9 for other in occupied):
                            continue
                        candidates.append((y, x, angle, candidate))
            if candidates:
                chosen = (sheet_index, min(candidates, key=lambda c: c[:3]))
                if sheet_index == len(sheets):
                    sheets.append([])
                break
        if chosen is None:
            raise ValueError(f"Part {part.id} does not fit within the allowed sheets/rotations/margins")
        sheet_index, (_, _, angle, shape) = chosen
        sheets[sheet_index].append(shape)
        placements.append({"part_id": part.id, "instance": instance, "sheet": sheet_index,
                           "rotation_deg": angle, "boundary": list(shape.exterior.coords),
                           "holes": [list(h.coords) for h in shape.interiors], "area_mm2": shape.area,
                           "bounds": list(shape.bounds)})
    # Independent post-placement collision/margin pass, not merely a packing-success flag.
    min_gap = None
    for sheet_index, occupied in enumerate(sheets):
        for i, shape in enumerate(occupied):
            if not usable.covers(shape.buffer(spec.kerf_mm/2, quad_segs=24)):
                raise ValueError("Packed contour violates sheet margin")
            for other in occupied[i+1:]:
                distance = shape.distance(other)
                min_gap = distance if min_gap is None else min(min_gap, distance)
                if shape.intersection(other).area > 1e-8 or distance < clearance-1e-7:
                    raise ValueError("Packed contours overlap or violate kerf/gap")
    material_area = sum(p[2].area for p in items)
    return {"algorithm": "deterministic bottom-left candidate packing / GEOS "+shapely.geos_version_string,
            "optimized": False, "sheet_count": len(sheets), "placements": placements,
            "material_area_mm2": material_area,
            "utilization": material_area/(len(sheets)*spec.sheet_width_mm*spec.sheet_height_mm),
            "minimum_part_gap_mm": min_gap, "verified_nonoverlap": True,
            "sheet_width_mm": spec.sheet_width_mm, "sheet_height_mm": spec.sheet_height_mm,
            "kerf_mm": spec.kerf_mm, "gap_mm": spec.gap_mm,
            "limits": ["Greedy packing, no global optimum", "Explicit allowed rotations encode grain constraints", "No common-line cutting"]}


def dxf(result, sheet_index):
    try:
        import ezdxf
    except ImportError as error:
        raise RuntimeError("DXF output requires ezdxf") from error
    doc = ezdxf.new("R2010", setup=False)
    doc.units = 4
    for name, color in [("CUT_OUTER", 7), ("CUT_INNER", 1), ("SHEET", 8)]:
        doc.layers.new(name, dxfattribs={"color": color})
    model = doc.modelspace()
    model.add_lwpolyline([(0, 0), (result["sheet_width_mm"], 0), (result["sheet_width_mm"], result["sheet_height_mm"]), (0, result["sheet_height_mm"])], close=True, dxfattribs={"layer": "SHEET"})
    for placement in result["placements"]:
        if placement["sheet"] != sheet_index:
            continue
        model.add_lwpolyline(placement["boundary"][:-1], close=True, dxfattribs={"layer": "CUT_OUTER"})
        for hole in placement["holes"]:
            model.add_lwpolyline(hole[:-1], close=True, dxfattribs={"layer": "CUT_INNER"})
    text = io.StringIO()
    doc.write(text)
    # Re-read output before exposing an artifact. Bad writer/configuration errors are real failures.
    readback = ezdxf.read(io.StringIO(text.getvalue()))
    if readback.units != 4 or readback.audit().has_errors:
        raise ValueError("Nested DXF failed independent reload/unit audit")
    return text.getvalue()


def cut_nc(result, sheet_index, setup, tool, machine, power_s):
    if machine.process != "cutting" or machine.post != "grbl_laser_v1" or tool.kind != "laser":
        raise ValueError("Cutting requires a GRBL laser machine and laser tool")
    if not isinstance(power_s, (int, float)) or not 0 < power_s <= 1000:
        raise ValueError("Specify GRBL laser power_s in (0,1000]; controller $30 must match 1000")
    if tool.feed_mm_min > machine.max_feed_mm_min:
        raise ValueError("Laser feed exceeds machine limit")
    if setup.wcs_rotation_z_deg:
        raise ValueError("Nested laser release currently requires zero WCS rotation")
    lines = ["(Oxygen GRBL laser v1 / requires $32=1 and $30=1000)", "G21 G90 G17 G94", f"G{setup.work_offset}", "M5"]
    contours = []
    for placement in result["placements"]:
        if placement["sheet"] != sheet_index:
            continue
        compensated = polygon(placement["boundary"], placement["holes"]).buffer(result["kerf_mm"]/2, quad_segs=48)
        if compensated.geom_type != "Polygon":
            raise ValueError("Kerf offset changed part topology; inspect the design")
        # Inner loops first so the piece remains attached until its perimeter is cut.
        contours += [("inner", list(h.coords)) for h in compensated.interiors]
        contours.append(("outer", list(compensated.exterior.coords)))
    if not contours:
        raise ValueError("No cutting contours on this sheet")
    distance = 0.0
    for kind, coords in contours:
        x, y = coords[0]
        for cx, cy in coords:
            point = (cx+setup.wcs_origin[0], cy+setup.wcs_origin[1], setup.stock.maximum[2]+setup.wcs_origin[2])
            if any(v < lo or v > hi for v, lo, hi in zip(point, machine.travel_min, machine.travel_max)):
                raise ValueError("Nested laser path exceeds machine travel")
        lines += [f"({kind} contour)", "M5", f"G0 X{x:.5f} Y{y:.5f}", f"M4 S{power_s:.3f}"]
        for (ax, ay), (x, y) in zip(coords, coords[1:]):
            distance += math.dist((ax, ay), (x, y))
            lines.append(f"G1 X{x:.5f} Y{y:.5f} F{tool.feed_mm_min:.3f}")
        lines.append("M5")
    lines += ["M5", "M2"]
    return "\n".join(lines)+"\n", {"cut_contours": len(contours), "cut_length_mm": distance,
                                      "kerf_compensation_mm": result["kerf_mm"]/2,
                                      "cut_order": "holes before perimeter", "tabs": False,
                                      "controller_qualified": False, "fixture_collision_checked": False}
