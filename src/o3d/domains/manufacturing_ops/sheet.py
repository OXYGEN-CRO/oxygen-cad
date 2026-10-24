"""Trusted canonical native sheet release with independent DXF and STEP checks."""
from __future__ import annotations

import io
import json
import math

from .cam import geos, polygon


def audit_dxf(text, flat, tolerance=0.01):
    import ezdxf
    from ezdxf.path import make_path
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    doc = ezdxf.read(io.StringIO(text))
    if doc.units != 4:
        raise ValueError("Flat pattern DXF must explicitly declare millimeters")
    if doc.audit().has_errors:
        raise ValueError("Flat pattern DXF failed ezdxf audit")
    layers = {}
    bend_lines = 0
    for entity in doc.modelspace():
        layer = entity.dxf.layer
        if layer in {"BEND_UP", "BEND_DOWN"}:
            bend_lines += 1
        if layer not in {"OUTLINE", "CUT"}:
            continue
        if entity.dxftype() not in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE"}:
            raise ValueError("Unsupported cutting entity in flat-pattern DXF")
        path = make_path(entity)
        coordinates = [(v.x, v.y) for v in path.flattening(distance=tolerance/4)]
        if len(coordinates) >= 2:
            layers.setdefault(layer, []).append(LineString(coordinates))
    if not layers.get("OUTLINE"):
        raise ValueError("DXF contains no outline geometry")
    outlines = unary_union(list(polygonize(unary_union(layers["OUTLINE"]))))
    cuts = unary_union(list(polygonize(unary_union(layers.get("CUT", [])))))
    if outlines.is_empty:
        raise ValueError("DXF outer contours do not close")
    expected = unary_union([polygon(loop) for loop in flat["outline"]])
    expected_cuts = unary_union([polygon(loop) for loop in flat.get("cutouts", [])])
    actual = outlines.difference(cuts)
    target = expected.difference(expected_cuts)
    hausdorff = actual.boundary.hausdorff_distance(target.boundary)
    if hausdorff > tolerance or actual.symmetric_difference(target).area > tolerance*target.length:
        raise ValueError("Reimported DXF contour differs from the native developed pattern")
    if bend_lines != len(flat.get("bends", [])):
        raise ValueError("DXF bend-line count differs from the native bend data")
    return {"reader": "ezdxf "+ezdxf.__version__, "units": "mm", "closed_outer_loops": len(list(polygonize(unary_union(layers["OUTLINE"])))),
            "area_mm2": actual.area, "bounds_mm": list(actual.bounds), "bend_lines": bend_lines,
            "max_contour_deviation_mm": hausdorff, "tolerance_mm": tolerance,
            "layers": sorted({e.dxf.layer for e in doc.modelspace()})}


def release_session(session, destination, body=None):
    params = {"body": body} if body else {}
    flat = session.execute("sheet.flat_pattern", params)
    if flat.get("perforations"):
        raise ValueError("Sheet perforations have an upstream metadata/B-rep mismatch; manufacturing release is blocked")
    if flat.get("pockets") or flat.get("bores"):
        raise ValueError("Mixed sheet machining needs a separately qualified release; flat cutting alone is incomplete")
    dxf = session.execute("sheet.export_dxf", params)["dxf"]
    bend_export = session.execute("sheet.export_bend_table", {**params, "format": "json"})
    bend_table = json.loads(bend_export["text"])
    for bend in flat.get("bends", []):
        expected = math.radians(abs(bend["angle_deg"]))*(bend["radius"]+flat["k_factor"]*flat["thickness"])
        if abs(expected-bend["allowance"]) > 1e-6:
            raise ValueError("Native bend allowance disagrees with the stated neutral-axis model")
    audit = audit_dxf(dxf, flat)
    destination.mkdir(parents=True, exist_ok=True)
    (destination/"flat-pattern.dxf").write_text(dxf)
    (destination/"bend-table.json").write_text(bend_export["text"])
    csv_export = session.execute("sheet.export_bend_table", {**params, "format": "csv"})
    (destination/"bend-table.csv").write_text(csv_export["text"])
    (destination/"flat-pattern.json").write_text(json.dumps(flat, indent=2)+"\n")
    # Independent B-rep reader measures the actual exported folded solid, not metadata only.
    step = destination/"folded.step"
    session.execute("file.export", {"path": str(step.resolve())})
    import build123d as bd
    shape = bd.import_step(step)
    if not shape.is_valid or not shape.solids():
        raise ValueError("Folded sheet STEP failed independent solid validation")
    measured = session.execute("inspect.measure", {})["total"]["volume_mm3"]
    if abs(shape.volume-measured) > max(0.01, measured*0.002):
        raise ValueError("Sheet STEP volume differs from native manufacturing metadata")
    return {"flat_pattern": flat, "bend_table": bend_table, "dxf_audit": audit,
            "folded_step_volume_mm3": shape.volume, "native_volume_mm3": measured,
            "bend_allowance_model": "abs(angle_rad) * (inside_radius + k_factor * thickness)",
            "physical_bend_validated": False,
            "limits": ["No material-specific bend calibration", "No punching/perforation metadata release", "No forming/tooling collision simulation"]}
