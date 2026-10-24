"""Real, deliberately bounded CAM: GEOS planar offsets and reference-point OD turning.

No adaptive engagement, automatic model recognition, controller compensation, thread
cycles or simultaneous axes are inferred from these paths. All entry moves are axial
plunges with a center-cutting tool, explicitly selected by the programmer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .schema import Machine, Operation, Setup, Tool
from .util import cancelled

MAX_MOVES = 150000


def geos():
    try:
        import shapely
        from shapely.geometry import Polygon, LineString, Point, box
        from shapely.ops import unary_union
    except ImportError as error:
        raise RuntimeError("Planar CAM requires Shapely/GEOS (install the declared manufacturing dependency)") from error
    return shapely, Polygon, LineString, Point, box, unary_union


def polygon(boundary, holes=()):
    _, Polygon, *_ = geos()
    shape = Polygon(boundary, holes)
    if not shape.is_valid or shape.is_empty or shape.area < 1e-8:
        raise ValueError("Machining boundary must be a non-self-intersecting polygon with valid holes")
    return shape


def parts(shape):
    return list(shape.geoms) if hasattr(shape, "geoms") else ([] if shape.is_empty else [shape])


@dataclass
class Path:
    operation_id: str
    setup_id: str
    tool_id: str
    strategy: str
    moves: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, motion, x, y, z, feed=None, **extra):
        point = {"motion": motion, "x": float(x), "y": float(y), "z": float(z), **extra}
        if feed is not None:
            point["feed"] = float(feed)
        if self.moves and all(abs(self.moves[-1][axis]-point[axis]) < 1e-9 for axis in "xyz") and self.moves[-1]["motion"] == motion:
            return
        if len(self.moves) >= MAX_MOVES:
            raise ValueError("Toolpath exceeds the 150,000 move qualification limit")
        self.moves.append(point)

    def wire(self):
        return {"operation_id": self.operation_id, "setup_id": self.setup_id, "tool_id": self.tool_id,
                "strategy": self.strategy, "moves": self.moves, "notes": self.notes}


def depths(top, bottom, step):
    count = math.ceil((top-bottom)/step)
    if count > 2000:
        raise ValueError("More than 2000 depth passes are not supported")
    return [max(bottom, top-(i+1)*step) for i in range(count)]


def _cut_chain(path, coords, z, setup, tool, tab=None):
    if len(coords) < 2:
        return
    x, y = coords[0]
    path.add("rapid", x, y, setup.clearance_z)
    path.add("feed", x, y, z, tool.plunge_mm_min)
    for x, y in coords[1:]:
        path.add("feed", x, y, z, tool.feed_mm_min)
    path.add("rapid", x, y, setup.clearance_z)


def _tabbed_coords(ring, op, z):
    """Split all tab boundaries exactly along arclength; vertical transitions, no diagonal gouge."""
    length = ring.length
    cuts = {0.0, length}
    distance = 0.0
    vertices = list(ring.coords)
    for a, b in zip(vertices, vertices[1:]):
        distance += math.dist(a, b)
        cuts.add(distance)
    intervals = []
    for fraction in op.tab_centers:
        center = fraction*length
        for offset in (-length, 0, length):
            lo, hi = center+offset-op.tab_width_mm/2, center+offset+op.tab_width_mm/2
            lo, hi = max(0, lo), min(length, hi)
            if hi > lo:
                intervals.append((lo, hi))
                cuts.update((lo, hi))
    cuts = sorted(cuts)
    result = []
    for a, b in zip(cuts, cuts[1:]):
        middle = (a+b)/2
        zz = max(z, op.bottom_z+op.tab_height_mm) if any(lo <= middle <= hi for lo, hi in intervals) else z
        for t in (a, b):
            p = ring.interpolate(t)
            result.append((p.x, p.y, zz))
    return result


def milling(op: Operation, setup: Setup, tool: Tool, cancel=None):
    if tool.kind not in ({"drill"} if op.strategy == "drill" else {"flat_endmill"}):
        raise ValueError(f"{op.strategy} requires {'a drill' if op.strategy == 'drill' else 'a flat end mill'}")
    if op.top_z > setup.stock.maximum[2]+setup.tolerance_mm or op.bottom_z < setup.stock.minimum[2]-setup.tolerance_mm:
        raise ValueError("Machining depth is outside declared stock")
    path = Path(op.id, setup.id, tool.id, op.strategy)
    path.notes.append("Axial plunge entry; programmer must select a center-cutting tool and appropriate feeds/material")
    r = tool.diameter/2
    shapely, _, LineString, _, box, _ = geos()
    if op.strategy == "drill":
        for x, y in op.holes:
            if cancelled(cancel):
                raise RuntimeError("Manufacturing cancelled")
            if not (setup.stock.minimum[0]+r <= x <= setup.stock.maximum[0]-r and setup.stock.minimum[1]+r <= y <= setup.stock.maximum[1]-r):
                raise ValueError("Drill intersects the stock edge")
            path.add("rapid", x, y, setup.clearance_z)
            for z in depths(op.top_z, op.bottom_z, op.peck_mm):
                path.add("feed", x, y, z, tool.plunge_mm_min)
                path.add("rapid", x, y, setup.clearance_z)
        return path
    shape = polygon(op.boundary, op.islands)
    if op.strategy == "face":
        if op.islands:
            raise ValueError("Facing with islands requires pocket strategy")
        # Overrun the stock to remove edge cusps; subsequent sweep checks forbid protected stock gouges.
        allowed = shape.buffer(r, quad_segs=32)
        xmin, ymin, xmax, ymax = allowed.bounds
        count = max(1, math.ceil((ymax-ymin)/(tool.diameter*op.stepover)))
        if count > 5000:
            raise ValueError("Facing requires too many scan lines")
        for z in depths(op.top_z, op.bottom_z, op.stepdown):
            for row in range(count+1):
                y = ymin+(ymax-ymin)*row/count
                cross = allowed.intersection(LineString([(xmin-1, y), (xmax+1, y)]))
                for line in parts(cross):
                    if line.geom_type != "LineString":
                        continue
                    coords = list(line.coords)
                    _cut_chain(path, coords[::-1] if row % 2 else coords, z, setup, tool)
                if cancelled(cancel):
                    raise RuntimeError("Manufacturing cancelled")
    elif op.strategy == "pocket":
        distance = r+op.allowance_mm
        loops = []
        while True:
            center_region = shape.buffer(-distance, quad_segs=32)
            if center_region.is_empty:
                break
            for region in parts(center_region):
                if region.geom_type == "Polygon":
                    loops += [list(region.exterior.coords), *[list(h.coords) for h in region.interiors]]
            distance += tool.diameter*op.stepover
            if len(loops) > 10000:
                raise ValueError("Pocket requires too many offset loops")
        if not loops:
            raise ValueError("Tool does not fit pocket")
        for z in depths(op.top_z, op.bottom_z, op.stepdown):
            for coords in loops:
                if cancelled(cancel):
                    raise RuntimeError("Manufacturing cancelled")
                _cut_chain(path, coords, z, setup, tool)
    elif op.strategy == "contour":
        if op.islands:
            raise ValueError("External contour uses a single outside boundary; make hole operations separately")
        expanded = shape.buffer(r+op.allowance_mm, quad_segs=32)
        if expanded.geom_type != "Polygon":
            raise ValueError("Contour offset is disconnected")
        ring = LineString(expanded.exterior.coords)
        for z in depths(op.top_z, op.bottom_z, op.stepdown):
            coordinates = _tabbed_coords(ring, op, z)
            x, y, zz = coordinates[0]
            path.add("rapid", x, y, setup.clearance_z)
            path.add("feed", x, y, zz, tool.plunge_mm_min)
            for x, y, zz in coordinates[1:]:
                path.add("feed", x, y, zz, tool.feed_mm_min)
            path.add("rapid", x, y, setup.clearance_z)
    else:
        raise ValueError(f"Unsupported milling strategy: {op.strategy}")
    if not path.moves:
        raise ValueError("No toolpath could be generated")
    path.notes.append(f"GEOS {shapely.geos_version_string}; no adaptive engagement or rest-stock planning")
    return path


def turning(op: Operation, setup: Setup, tool: Tool, cancel=None):
    if tool.kind != "turning_insert" or setup.stock.kind != "cylinder":
        raise ValueError("Turning requires cylindrical stock and a turning reference-point insert")
    if setup.wcs_rotation_z_deg or any(abs(v) > 1e-9 for v in setup.wcs_origin):
        raise ValueError("Reference turning uses spindle X radius/Z axial with zero WCS transform")
    radius = (setup.stock.maximum[0]-setup.stock.minimum[0])/2
    if any(r >= radius or z < setup.stock.minimum[2] or z > setup.stock.maximum[2] for z, r in op.profile_zr):
        raise ValueError("Turning profile must be inside the cylindrical stock")
    path = Path(op.id, setup.id, tool.id, op.strategy)
    path.notes.append("LinuxCNC G8 radius mode; zero-nose reference-point tool. No insert-envelope/chuck collision qualification")
    clearance = radius+max(2, tool.diameter)
    zfront = setup.stock.maximum[2]+2
    if op.strategy == "turn_profile":
        # Monotone external profile: independent depth passes stop at the first larger-radius shoulder.
        for radial in depths(radius, min(r for _, r in op.profile_zr), op.stepdown):
            if cancelled(cancel):
                raise RuntimeError("Manufacturing cancelled")
            path.add("rapid", clearance, 0, zfront)
            path.add("rapid", radial, 0, zfront)
            for z, r in op.profile_zr:
                path.add("feed", max(radial, r+op.allowance_mm), 0, z, tool.feed_mm_min)
            path.add("rapid", clearance, 0, op.profile_zr[-1][0])
        # Finish one connected profile at declared reference-point allowance.
        path.add("rapid", clearance, 0, zfront)
        path.add("rapid", op.profile_zr[0][1]+op.allowance_mm, 0, zfront)
        for z, r in op.profile_zr:
            path.add("feed", r+op.allowance_mm, 0, z, tool.feed_mm_min)
        path.add("rapid", clearance, 0, op.profile_zr[-1][0])
    elif op.strategy == "turn_face":
        z = op.profile_zr[0][0]
        for axial in depths(setup.stock.maximum[2], z, op.stepdown):
            path.add("rapid", clearance, 0, axial)
            path.add("feed", op.profile_zr[0][1], 0, axial, tool.feed_mm_min)
            path.add("rapid", clearance, 0, axial)
    elif op.strategy in {"turn_groove", "part_off"}:
        if op.strategy == "part_off":
            raise ValueError("Part-off is not qualified: retained-part/chuck/insert support must be modeled")
        z, target_r = op.profile_zr[0]
        for radial in depths(radius, target_r, op.stepdown):
            path.add("rapid", clearance, 0, z)
            path.add("feed", radial, 0, z, tool.plunge_mm_min)
            path.add("rapid", clearance, 0, z)
        path.notes.append("Single axial reference-point groove only; groove width is not inferred from tool diameter")
    else:
        raise ValueError("Turning strategy not qualified")
    path.add("rapid", clearance, 0, zfront)
    return path


def generate(op, setup, tool, machine, cancel=None):
    if tool.feed_mm_min > machine.max_feed_mm_min or tool.plunge_mm_min > machine.max_feed_mm_min:
        raise ValueError("Tool cutting feed exceeds machine limit")
    if tool.spindle_rpm > machine.max_spindle_rpm:
        raise ValueError("Tool spindle speed exceeds machine limit")
    if machine.process == "milling":
        return milling(op, setup, tool, cancel)
    if machine.process == "turning":
        return turning(op, setup, tool, cancel)
    raise ValueError(f"{machine.process} operations use the nesting/cutting or slicing workflow")
