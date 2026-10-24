"""Offline, traceable probe/inspection imports and rigid point alignment."""
from __future__ import annotations

import csv
import hashlib
import io
import math


def read_measurements(payload):
    if "csv" in payload:
        text = payload["csv"]
        if not isinstance(text, str) or len(text) > 1000000:
            raise ValueError("Measurement CSV must be at most 1 MB")
        records = list(csv.DictReader(io.StringIO(text)))
        source_hash = hashlib.sha256(text.encode()).hexdigest()
    else:
        records = payload.get("measurements", [])
        import json
        source_hash = hashlib.sha256(json.dumps(records, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if not isinstance(records, list) or not 1 <= len(records) <= 10000:
        raise ValueError("Provide between 1 and 10,000 measurement records")
    return records, source_hash


def inspect(payload):
    records, source_hash = read_measurements(payload)
    results = []
    seen = set()
    for record in records:
        if set(record) != {"id", "nominal", "measured", "lower_tolerance", "upper_tolerance", "unit"}:
            raise ValueError("Inspection fields: id,nominal,measured,lower_tolerance,upper_tolerance,unit")
        if not isinstance(record["id"], str) or not record["id"] or record["id"] in seen:
            raise ValueError("Inspection IDs must be nonempty and unique")
        seen.add(record["id"])
        values = {name: float(record[name]) for name in ["nominal", "measured", "lower_tolerance", "upper_tolerance"]}
        if any(not math.isfinite(v) for v in values.values()) or values["lower_tolerance"] > values["upper_tolerance"]:
            raise ValueError("Inspection values must be finite, with ordered signed tolerance limits")
        if record["unit"] not in {"mm", "deg"}:
            raise ValueError("Inspection supports explicit mm or deg units")
        deviation = values["measured"]-values["nominal"]
        results.append({**record, **values, "deviation": deviation,
                        "status": "pass" if values["lower_tolerance"] <= deviation <= values["upper_tolerance"] else "fail"})
    return {"source_sha256": source_hash, "results": results,
            "pass_count": sum(r["status"] == "pass" for r in results),
            "fail_count": sum(r["status"] == "fail" for r in results),
            "live_connector": False, "model_association": "caller-supplied stable feature IDs"}


def align(payload, tolerance):
    import numpy as np
    records, source_hash = read_measurements(payload)
    nominal, measured, ids = [], [], []
    for record in records:
        if set(record) == {"id", "nominal", "measured"}:
            a, b = record["nominal"], record["measured"]
        elif set(record) == {"id", "nominal_x", "nominal_y", "nominal_z", "measured_x", "measured_y", "measured_z"}:
            a = [float(record[f"nominal_{axis}"]) for axis in "xyz"]
            b = [float(record[f"measured_{axis}"]) for axis in "xyz"]
        else:
            raise ValueError("Alignment records require id,nominal:[x,y,z],measured:[x,y,z], or corresponding CSV columns")
        if not isinstance(a, (tuple, list)) or not isinstance(b, (tuple, list)) or len(a) != 3 or len(b) != 3:
            raise ValueError("Alignment points must be XYZ triples")
        nominal.append(a)
        measured.append(b)
        ids.append(record["id"])
    if len(nominal) < 3 or len(set(ids)) != len(ids):
        raise ValueError("Alignment requires at least three uniquely identified point pairs")
    a, b = np.asarray(nominal, dtype=float), np.asarray(measured, dtype=float)
    if not np.isfinite(a).all() or not np.isfinite(b).all() or np.linalg.matrix_rank(a-a.mean(axis=0)) < 2:
        raise ValueError("Alignment requires finite, non-collinear nominal points")
    translation = (b-a).mean(axis=0)
    translation_errors = np.linalg.norm((a+translation)-b, axis=1)
    u, _, vt = np.linalg.svd((a-a.mean(axis=0)).T@(b-b.mean(axis=0)))
    rotation = vt.T@u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T@u.T
    rigid_translation = b.mean(axis=0)-rotation@a.mean(axis=0)
    aligned = (rotation@a.T).T+rigid_translation
    errors = np.linalg.norm(aligned-b, axis=1)
    angle = math.degrees(math.acos(float(np.clip((np.trace(rotation)-1)/2, -1, 1))))
    return {"source_sha256": source_hash, "point_ids": ids, "translation_mm": translation.tolist(),
            "translation_max_residual_mm": float(translation_errors.max()),
            "translation_within_tolerance": bool(translation_errors.max() <= tolerance),
            "rigid_rotation": rotation.tolist(), "rigid_translation_mm": rigid_translation.tolist(),
            "rigid_rotation_deg": angle, "rigid_max_residual_mm": float(errors.max()),
            "rigid_within_tolerance": bool(errors.max() <= tolerance),
            "tolerance_mm": tolerance, "point_residuals_mm": errors.tolist(),
            "live_connector": False, "automatic_rotational_feedback": False}


def probe_program(payload, setup, tool, machine):
    if machine.process != "milling" or machine.post != "linuxcnc_mill_v1" or tool.kind != "probe":
        raise ValueError("Probe plans require LinuxCNC milling and a probe tool")
    if setup.wcs_rotation_z_deg:
        raise ValueError("Probe program requires zero WCS rotation")
    points = payload.get("points", [])
    if not isinstance(points, list) or not 1 <= len(points) <= 100:
        raise ValueError("Provide 1..100 probe points {x,y,start_z,seek_z,feed_mm_min}")
    lines = ["(Oxygen offline LinuxCNC Z probe plan / no live machine connection)", "G21 G90 G94 G40 G49 G80", f"G{setup.work_offset}", f"T{tool.number} M6", f"G43 H{tool.number}", "M5", f"G0 Z{setup.clearance_z:.5f}"]
    for point in points:
        if set(point) != {"x", "y", "start_z", "seek_z", "feed_mm_min"}:
            raise ValueError("Probe point fields are x,y,start_z,seek_z,feed_mm_min")
        x, y, start, end, feed = (float(point[k]) for k in ["x", "y", "start_z", "seek_z", "feed_mm_min"])
        if any(not math.isfinite(v) for v in (x, y, start, end, feed)) or not setup.clearance_z >= start > end or not 0 < feed <= machine.max_feed_mm_min:
            raise ValueError("Invalid probe approach/seek/feed")
        for z in (setup.clearance_z, end):
            xyz = [x+setup.wcs_origin[0], y+setup.wcs_origin[1], z+setup.wcs_origin[2]]
            if any(v < lo or v > hi for v, lo, hi in zip(xyz, machine.travel_min, machine.travel_max)):
                raise ValueError("Probe plan exceeds machine travel")
        if setup.fixtures:
            raise ValueError("Probe fixture-envelope checking is not qualified; use an externally reviewed plan")
        lines += [f"G0 X{x:.5f} Y{y:.5f}", f"G0 Z{start:.5f}", f"G38.2 Z{end:.5f} F{feed:.3f}", f"G0 Z{setup.clearance_z:.5f}"]
    lines += ["M30"]
    return "\n".join(lines)+"\n"
