"""Actual OpenCAMLib ball-cutter parallel finishing on a current-design STL."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import time

from .cam import Path as Toolpath, geos, parts, polygon
from .util import cancelled


def finish(mesh_path, destination, op, setup, tool, machine, cancel=None):
    if machine.process != "milling" or tool.kind != "ball_endmill":
        raise ValueError("Parallel surface finishing requires a ball end mill on a milling machine")
    if setup.wcs_rotation_z_deg or any(abs(v) > 1e-8 for v in setup.wcs_origin):
        raise ValueError("Current STL surface finishing requires design coordinates equal setup coordinates (zero WCS transform)")
    if tool.spindle_rpm > machine.max_spindle_rpm or max(tool.feed_mm_min, tool.plunge_mm_min) > machine.max_feed_mm_min:
        raise ValueError("Surface tool speed/feed exceeds machine limits")
    import numpy as np
    import trimesh
    mesh = trimesh.load(mesh_path, force="mesh", process=True)
    if not mesh.is_watertight or not mesh.is_winding_consistent or len(mesh.faces) > 100000:
        raise ValueError("Surface finishing requires a closed mesh with at most 100,000 triangles")
    boundary = polygon(op.boundary, op.islands)
    _, _, LineString, *_ = geos()
    xmin, ymin, xmax, ymax = boundary.bounds
    radius = tool.diameter/2
    # Flat-reference transverse scallop bound. Freeform scallop remains explicitly sampled.
    max_step = 2*math.sqrt(max(1e-9, 2*radius*setup.tolerance_mm-setup.tolerance_mm**2))
    spacing = min(tool.diameter*op.stepover, max_step)
    sampling = min(0.5, spacing/2)
    row_count = max(1, math.ceil((ymax-ymin)/spacing))
    if row_count > 2000:
        raise ValueError("Surface raster requires more than 2000 rows")
    chains, points = [], []
    for row in range(row_count+1):
        y = ymin+(ymax-ymin)*row/row_count
        for line in parts(boundary.intersection(LineString([(xmin,y),(xmax,y)]))):
            if line.geom_type != "LineString" or line.length < 1e-8:
                continue
            count = max(1, math.ceil(line.length/sampling))
            coordinates = [[line.interpolate(i/count, normalized=True).x, line.interpolate(i/count, normalized=True).y] for i in range(count+1)]
            if row % 2:
                coordinates.reverse()
            start = len(points)
            points.extend(coordinates)
            chains.append((start,len(points)))
    if not points or len(points) > 20000:
        raise ValueError("Surface finishing is limited to 20,000 sampled cutter locations")
    destination.mkdir(parents=True, exist_ok=True)
    request = destination/f"{op.id}-ocl-input.json"
    reply = destination/f"{op.id}-ocl-output.json"
    request.write_text(json.dumps({"triangles":mesh.triangles.tolist(), "diameter_mm":tool.diameter,
                                   "flute_length_mm":tool.flute_length, "floor_z":op.bottom_z,
                                   "xy":points}, allow_nan=False))
    python = os.environ.get("O3D_CAM_PYTHON", "/usr/bin/python3")
    log = destination/f"{op.id}-ocl.log"
    started = time.monotonic()
    with log.open("wb") as stream:
        try:
            process = subprocess.Popen([python,str(Path(__file__).with_name("ocl_worker.py")),str(request),str(reply)], stdout=stream,stderr=subprocess.STDOUT)
        except FileNotFoundError as error:
            raise RuntimeError("OpenCAMLib worker Python unavailable; configure host O3D_CAM_PYTHON") from error
        try:
            while process.poll() is None:
                if cancelled(cancel):
                    raise RuntimeError("OpenCAMLib finishing cancelled")
                if time.monotonic()-started > 120:
                    raise RuntimeError("OpenCAMLib finishing exceeded 120 seconds")
                time.sleep(0.05)
            if process.returncode:
                raise RuntimeError("OpenCAMLib failed; install python3-opencamlib for the host CAM Python: "+log.read_text(errors="replace")[-1500:])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    data = json.loads(reply.read_text())
    if len(data["points"]) != len(points) or not np.isfinite(data["points"]).all():
        raise ValueError("OpenCAMLib returned invalid cutter locations")
    path = Toolpath(op.id,setup.id,tool.id,op.strategy)
    for start,end in chains:
        x,y,z = data["points"][start]
        path.add("rapid",x,y,setup.clearance_z)
        path.add("feed",x,y,z+op.allowance_mm,tool.plunge_mm_min)
        for x,y,z in data["points"][start+1:end]:
            if z < setup.stock.minimum[2] or z > setup.stock.maximum[2]:
                raise ValueError("Surface cutter location is outside declared stock")
            path.add("feed",x,y,z+op.allowance_mm,tool.feed_mm_min)
        path.add("rapid",x,y,setup.clearance_z)
    # Independent Euclidean sphere-to-triangle distance, including segment midpoint
    # samples. No OpenCAMLib result is trusted solely because the library returned it.
    samples = []
    for a,b in zip(path.moves,path.moves[1:]):
        if b["motion"] == "feed" and a["motion"] == "feed":
            samples.extend([[a[k] for k in "xyz"],[(a[k]+b[k])/2 for k in "xyz"]])
    if len(samples) > 40000:
        raise ValueError("Independent finishing validation exceeds the sample limit")
    minimum_clearance = math.inf
    gouges = []
    for offset in range(0,len(samples),32):
        if cancelled(cancel):
            raise RuntimeError("Surface validation cancelled")
        centers = np.asarray(samples[offset:offset+32])+[0,0,radius]
        closest, distances, triangles = trimesh.proximity.closest_point_naive(mesh,centers)
        signed = np.einsum("ij,ij->i",centers-closest,mesh.face_normals[triangles])
        clearance = distances-radius
        minimum_clearance = min(minimum_clearance,float(clearance.min()))
        for local in np.where((clearance < -setup.tolerance_mm) | (signed < -setup.tolerance_mm))[0]:
            gouges.append({"sample":offset+int(local),"location_mm":samples[offset+local],"clearance_mm":float(clearance[local])})
    if gouges:
        raise ValueError(f"Independent ball-cutter surface validation found {len(gouges)} sampled gouges")
    path.notes += ["OpenCAMLib BallCutter BatchDropCutter", "Sampled sphere contact verification only; no shaft/model, continuous freeform scallop or engagement guarantee"]
    return path, {"engine":data["engine"], "algorithm":data["algorithm"], "binding_sha256":data["binding_sha256"],
                  "source":"https://github.com/aewallin/opencamlib", "license":"LGPL-2.1-or-later external worker",
                  "raster_spacing_mm":(ymax-ymin)/row_count, "along_path_sampling_mm":sampling,
                  "flat_reference_scallop_bound_mm":radius-math.sqrt(radius*radius-(spacing/2)**2),
                  "independent_sphere_samples":len(samples), "minimum_sampled_ball_clearance_mm":minimum_clearance,
                  "sampled_gouges":0, "continuous_surface_coverage_verified":False,
                  "remaining":"Adaptive/rest roughing, shaft collisions, continuous freeform scallop bounds and physical machine qualification"}
