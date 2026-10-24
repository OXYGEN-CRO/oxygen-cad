"""Independent NC reader and bounded cylindrical-cutter sweep checks.

This is 2.5D stock verification and axis-aligned fixture envelope checking. It is
not full-machine simulation, engagement/load prediction or certification of NC.
"""
from __future__ import annotations

import math
import re

from .cam import geos, polygon


def machine_point(move, setup):
    angle = math.radians(setup.wcs_rotation_z_deg)
    x, y = move["x"], move["y"]
    return (x*math.cos(angle)-y*math.sin(angle)+setup.wcs_origin[0],
            x*math.sin(angle)+y*math.cos(angle)+setup.wcs_origin[1], move["z"]+setup.wcs_origin[2])


def verify_path(path, op, setup, tool, machine):
    _, _, LineString, Point, box, union = geos()
    violations = []
    cutting = []
    feed_length = rapid_length = 0.0
    duration = 0.0
    stock_xy = box(*setup.stock.minimum[:2], *setup.stock.maximum[:2])
    tool_r = tool.diameter/2
    previous = None
    for index, move in enumerate(path.moves):
        xyz = machine_point(move, setup)
        if any(v < lo-1e-7 or v > hi+1e-7 for v, lo, hi in zip(xyz, machine.travel_min, machine.travel_max)):
            violations.append({"kind": "axis_travel", "move": index, "machine_xyz": xyz})
        if previous is None:
            previous = move
            continue
        distance = math.dist([previous[a] for a in "xyz"], [move[a] for a in "xyz"])
        if move["motion"] == "feed":
            feed_length += distance
            duration += distance/move["feed"]*60
        else:
            rapid_length += distance
        lo_z, hi_z = sorted((previous["z"], move["z"]))
        xyline = LineString([(previous["x"], previous["y"]), (move["x"], move["y"])])
        if xyline.length < 1e-10:
            xyline = Point(move["x"], move["y"])
        sweep = xyline.buffer(tool_r, quad_segs=48)
        for fixture in setup.fixtures:
            fixture_xy = box(*fixture.minimum[:2], *fixture.maximum[:2])
            if hi_z+tool.gauge_length > fixture.minimum[2] and lo_z < fixture.maximum[2]:
                cutter_hit = sweep.intersects(fixture_xy) and hi_z+tool.flute_length > fixture.minimum[2]
                holder_hit = xyline.buffer(tool.holder_diameter/2).intersects(fixture_xy) and hi_z+tool.gauge_length > fixture.minimum[2] and lo_z+tool.flute_length < fixture.maximum[2]
                if cutter_hit or holder_hit:
                    violations.append({"kind": "fixture_collision", "move": index, "fixture_id": fixture.id,
                                       "envelope": "holder" if holder_hit else "cutter"})
        if machine.process == "milling":
            if move["motion"] == "rapid" and xyline.length > 1e-7 and lo_z < setup.stock.maximum[2]-setup.tolerance_mm and sweep.intersects(stock_xy):
                violations.append({"kind": "rapid_in_stock", "move": index})
            if move["motion"] == "feed" and lo_z+tool.flute_length < setup.stock.maximum[2]-setup.tolerance_mm and sweep.intersects(stock_xy):
                violations.append({"kind": "insufficient_flute_reach", "move": index})
            axial = abs(previous["x"]-move["x"])+abs(previous["y"]-move["y"]) < 1e-9
            if move["motion"] == "feed" and (hi_z <= op.bottom_z+setup.tolerance_mm or axial and lo_z <= op.bottom_z+setup.tolerance_mm):
                cutting.append(sweep)
        previous = move
    report = {"model": "2.5D flat cylindrical sweep + axis-aligned fixture envelopes" if machine.process == "milling" else "reference-point turning travel + fixture envelopes only",
              "violations": violations[:200], "violation_count": len(violations),
              "feed_distance_mm": feed_length, "rapid_distance_mm": rapid_length,
              "feed_time_seconds": duration, "rapid_time_included": False,
              "full_machine_simulation": False,
              "limits": ["No spindle/chuck/axis-body kinematics", "No cutting-force, workholding or controller qualification", "No physical machining result"]}
    if machine.process != "milling":
        report["stock_removal_verified"] = False
        return report
    removed = union(cutting) if cutting else Point().buffer(0)
    removed = removed.intersection(stock_xy)
    if op.strategy in {"pocket", "face"}:
        target = polygon(op.boundary, op.islands)
        reachable = target.buffer(-tool_r-op.allowance_mm, quad_segs=48).buffer(tool_r, quad_segs=48) if op.strategy == "pocket" else target
        remaining = reachable.difference(removed)
        gouge = removed.difference(target.buffer(setup.tolerance_mm))
        report.update(target_area_mm2=target.area, removed_floor_area_mm2=removed.area,
                      reachable_floor_area_mm2=reachable.area, remaining_reachable_area_mm2=remaining.area,
                      unreachable_corner_area_mm2=target.difference(reachable).area,
                      gouge_area_mm2=gouge.area)
        if gouge.area > setup.tolerance_mm**2:
            violations.append({"kind": "gouge", "area_mm2": gouge.area})
        # Residual is measured and reported, not silently classified as a finished floor.
        report["floor_coverage_complete"] = remaining.area <= setup.tolerance_mm**2
    elif op.strategy == "contour":
        keep = polygon(op.boundary)
        gouge = removed.intersection(keep.buffer(-setup.tolerance_mm))
        report.update(removed_floor_area_mm2=removed.area, gouge_area_mm2=gouge.area,
                      tabs_retained=bool(op.tab_centers))
        if gouge.area > setup.tolerance_mm**2:
            violations.append({"kind": "gouge", "area_mm2": gouge.area})
    elif op.strategy == "drill":
        report.update(removed_floor_area_mm2=removed.area, hole_count=len(op.holes), hole_diameter_mm=tool.diameter)
    report["violation_count"] = len(violations)
    report["violations"] = violations[:200]
    report["stock_removal_verified"] = True
    return report


def post(paths, setup, tools, machine):
    """Restricted, versioned LinuxCNC dialect. No controller-specific canned cycles."""
    if machine.post not in {"linuxcnc_mill_v1", "linuxcnc_lathe_v1"}:
        raise ValueError("This post supports LinuxCNC milling or reference-point turning only")
    turning = machine.process == "turning"
    lines = ["%", "(Oxygen bounded CAM / review and qualify on the declared controller)", "G21 G90 G94 G40 G49 G80", "G18 G8" if turning else "G17", f"G{setup.work_offset}"]
    current_tool = None
    for path in paths:
        tool = tools[path.tool_id]
        if tool.id != current_tool:
            lines += ["M5", f"T{tool.number} M6", f"G43 H{tool.number}", f"S{tool.spindle_rpm:.3f} M3"]
            current_tool = tool.id
        lines.append(f"(operation {path.operation_id}: {path.strategy})")
        if not turning:
            lines.append(f"G0 Z{setup.clearance_z:.5f}")
        for m in path.moves:
            # Rotate path into the programmed work-offset frame; offset translation belongs to G54..G59.
            angle = math.radians(setup.wcs_rotation_z_deg)
            x = m["x"]*math.cos(angle)-m["y"]*math.sin(angle)
            y = m["x"]*math.sin(angle)+m["y"]*math.cos(angle)
            code = f"G{0 if m['motion'] == 'rapid' else 1} X{x:.5f}"
            if not turning:
                code += f" Y{y:.5f}"
            code += f" Z{m['z']:.5f}"
            if m["motion"] == "feed":
                code += f" F{m['feed']:.3f}"
            lines.append(code)
    lines += ["M5", "M30", "%"]
    return "\n".join(lines)+"\n"


def backplot(code):
    """Separate small parser for our posted dialect; fails on unknown motion/units/modes."""
    position = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    records = []
    feed = None
    valid_g = {0, 1, 8, 17, 18, 21, 40, 43, 49, 54, 55, 56, 57, 58, 59, 80, 90, 94}
    for number, raw in enumerate(code.splitlines(), 1):
        line = re.sub(r"\([^)]*\)", "", raw).strip()
        if not line or line == "%":
            continue
        words = re.findall(r"([A-Z])([-+]?\d+(?:\.\d*)?)", line)
        if re.sub(r"([A-Z])([-+]?\d+(?:\.\d*)?)|\s+", "", line):
            raise ValueError(f"Unsupported NC syntax on line {number}")
        g_codes = [int(float(v)) for k, v in words if k == "G"]
        if any(float(v) != int(float(v)) or int(float(v)) not in valid_g for k, v in words if k == "G"):
            raise ValueError(f"Unsupported NC G-code on line {number}")
        if any(k not in "GMSTFHXYZ" for k, _ in words):
            raise ValueError("Unsupported NC address")
        if any(k == "M" and int(float(v)) not in {3, 5, 6, 30} for k, v in words):
            raise ValueError("Unsupported M-code")
        motion = next((g for g in g_codes if g in {0, 1}), None)
        for k, v in words:
            if k in position:
                position[k] = float(v)
            elif k == "F":
                feed = float(v)
        if motion is not None:
            if motion == 1 and (feed is None or feed <= 0):
                raise ValueError("Feed move has no positive feed")
            records.append({"motion": "rapid" if motion == 0 else "feed", **{k.lower(): v for k, v in position.items()}, "feed": feed, "line": number})
    return records


def verify_post(code, paths, setup, machine):
    parsed = backplot(code)
    expected = []
    angle = math.radians(setup.wcs_rotation_z_deg)
    for path in paths:
        if machine.process == "milling":
            expected.append(None)  # Explicit initial Z retract inserted by the post.
        for m in path.moves:
            expected.append((m["motion"], m["x"]*math.cos(angle)-m["y"]*math.sin(angle),
                             0 if machine.process == "turning" else m["x"]*math.sin(angle)+m["y"]*math.cos(angle), m["z"]))
    if len(parsed) != len(expected):
        raise ValueError("Posted motion count does not match generated paths")
    for actual, target in zip(parsed, expected):
        if target is not None and (actual["motion"] != target[0] or any(abs(actual[a]-v) > 1e-4 for a, v in zip("xyz", target[1:]))):
            raise ValueError("Independent posted-motion backplot differs from generated toolpath")
    return {"parser": "oxygen-restricted-linuxcnc-v1", "motion_count": len(parsed), "max_coordinate_rounding_mm": 0.00001,
            "matches_generated_path": True, "controller_qualified": False}
