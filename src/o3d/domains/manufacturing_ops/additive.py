"""CuraEngine FFF adapter. Executable/configuration paths are host configuration only.

Only real positive-extrusion output is accepted. A mesh export or process launch
does not constitute slicing. Proprietary resin/metal build formats are unsupported.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from .util import cancelled


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_identity():
    executable = os.environ.get("O3D_CURA_ENGINE") or shutil.which("CuraEngine")
    definitions = Path(os.environ.get("O3D_CURA_DEFINITIONS", "/usr/share/cura/resources/definitions"))
    return {"engine_sha256":digest(executable) if executable and Path(executable).is_file() else None,
            "definitions":{name:digest(definitions/name) if (definitions/name).is_file() else None
                           for name in ("fdmprinter.def.json","fdmextruder.def.json")}}


def _run(argv, cwd, cancel=None, timeout=180):
    """No shell, bounded log/file output, real cancellation and nonzero-exit failures."""
    log = cwd / "slicer.log"
    started = time.monotonic()
    with log.open("w+b") as stream:
        process = subprocess.Popen(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                if cancelled(cancel):
                    raise RuntimeError("Slicing cancelled")
                if time.monotonic()-started > timeout:
                    raise RuntimeError("CuraEngine exceeded slicing timeout")
                if log.stat().st_size > 10*1024*1024:
                    raise RuntimeError("CuraEngine exceeded diagnostic output limit")
                time.sleep(0.05)
            if process.returncode:
                raise RuntimeError(f"CuraEngine failed ({process.returncode}): "+log.read_text(errors="replace")[-2000:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return log


def parse_fff(code, bounds):
    position = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
    absolute_e = True
    absolute_xyz = True
    layers = {}
    extrusion_mm = 0.0
    positive_moves = 0
    segments = []
    for number, raw in enumerate(code.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        tokens = {key: float(value) for key, value in re.findall(r"([A-Z])([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)", line)}
        if line.startswith("M82"):
            absolute_e = True
        elif line.startswith("M83"):
            absolute_e = False
        elif re.match(r"G90(?:\s|$)", line):
            absolute_xyz = True
        elif re.match(r"G91(?:\s|$)", line):
            absolute_xyz = False
        elif re.match(r"G20(?:\s|$)", line):
            raise ValueError("FFF output unexpectedly uses inches")
        elif re.match(r"G92(?:\s|$)", line):
            for axis in position:
                if axis in tokens:
                    position[axis] = tokens[axis]
        elif re.match(r"G0?[01](?:\s|$)", line):
            old = dict(position)
            for axis in "XYZ":
                if axis in tokens:
                    position[axis] = tokens[axis]+(0 if absolute_xyz else position[axis])
            if "E" in tokens:
                position["E"] = tokens["E"]+(0 if absolute_e else position["E"])
            extruded = position["E"]-old["E"]
            if extruded > 1e-6 and any(abs(position[a]-old[a]) > 1e-7 for a in "XYZ"):
                for axis, lo, hi in zip("XYZ", bounds[0], bounds[1]):
                    if not lo-0.01 <= position[axis] <= hi+0.01:
                        raise ValueError(f"FFF extrusion exceeds {axis} build volume on line {number}")
                z = round(position["Z"], 5)
                layers[z] = layers.get(z, 0)+1
                positive_moves += 1
                extrusion_mm += extruded
                if len(segments) < 20000:
                    segments.append({"from": [old[a] for a in "XYZ"], "to": [position[a] for a in "XYZ"], "extrusion_mm": extruded})
    if positive_moves < 3 or not layers:
        raise ValueError("Slicer output has no usable deposited-extrusion paths")
    return {"positive_extrusion_moves": positive_moves, "extrusion_filament_mm": extrusion_mm,
            "layer_count": len(layers), "layers": [{"z_mm": z, "segments": layers[z]} for z in sorted(layers)],
            "preview_segments": segments, "preview_truncated": positive_moves > len(segments),
            "extrusion_within_build_volume": True, "units": "mm"}


def slice_mesh(mesh_path, destination, printer, machine, cancel=None):
    if machine.process != "fff" or machine.post != "cura_fff_v1":
        raise ValueError("FFF preparation requires a Cura FFF machine")
    executable = os.environ.get("O3D_CURA_ENGINE") or shutil.which("CuraEngine")
    if not executable or not Path(executable).is_file():
        raise RuntimeError("CuraEngine is unavailable; configure host O3D_CURA_ENGINE. No slicing was performed")
    definitions = Path(os.environ.get("O3D_CURA_DEFINITIONS", "/usr/share/cura/resources/definitions"))
    definition = definitions / "fdmprinter.def.json"
    extruder = definitions / "fdmextruder.def.json"
    if not definition.is_file() or not extruder.is_file():
        raise RuntimeError("Cura machine/extruder definitions are unavailable; configure host O3D_CURA_DEFINITIONS")
    import numpy as np
    import trimesh
    mesh = trimesh.load(mesh_path, force="mesh", process=True)
    if not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0:
        raise ValueError("FFF requires a closed, consistently oriented printable mesh")
    if len(mesh.faces) > 1000000:
        raise ValueError("FFF input exceeds one million triangles")
    mesh.apply_transform(trimesh.transformations.euler_matrix(*(np.radians(printer.orientation_deg)), axes="sxyz"))
    lo, hi = mesh.bounds
    width, depth, height = [b-a for a, b in zip(machine.travel_min, machine.travel_max)]
    if min(machine.travel_min) < 0 or any(abs(v) > 1e-8 for v in machine.travel_min):
        raise ValueError("Cura printer build volume uses a zero minimum and positive maximum")
    center = printer.center_xy or (width/2, depth/2)
    mesh.apply_translation((center[0]-(lo[0]+hi[0])/2, center[1]-(lo[1]+hi[1])/2, -lo[2]))
    lo, hi = mesh.bounds
    # Leave one nozzle radius around the part for wall/support/skirt paths.
    if lo[0] < printer.nozzle_diameter_mm or lo[1] < printer.nozzle_diameter_mm or hi[0] > width-printer.nozzle_diameter_mm or hi[1] > depth-printer.nozzle_diameter_mm or hi[2] > height:
        raise ValueError("Oriented part does not fit the printer build volume with nozzle margin")
    destination.mkdir(parents=True, exist_ok=True)
    placed = destination / "placed.stl"
    mesh.export(placed)
    settings = {
        "machine_width": width, "machine_depth": depth, "machine_height": height,
        "machine_center_is_zero": "false", "machine_extruder_count": 1,
        "machine_gcode_flavor": "RepRap (Marlin/Sprinter)",
        "machine_start_gcode": "G21\nG90\nM82", "machine_end_gcode": "M104 S0\nM140 S0\nM84",
        "layer_height": printer.layer_height_mm, "layer_height_0": printer.layer_height_mm,
        "machine_nozzle_size": printer.nozzle_diameter_mm,
        "line_width": printer.nozzle_diameter_mm,
        "wall_line_width": printer.nozzle_diameter_mm, "wall_line_width_0": printer.nozzle_diameter_mm,
        "wall_line_width_x": printer.nozzle_diameter_mm, "skin_line_width": printer.nozzle_diameter_mm,
        "infill_line_width": printer.nozzle_diameter_mm, "support_line_width": printer.nozzle_diameter_mm,
        "wall_line_count": printer.wall_count, "top_layers": 4, "bottom_layers": 4,
        "infill_sparse_density": printer.infill_percent,
        "infill_line_distance": printer.nozzle_diameter_mm*100/printer.infill_percent if printer.infill_percent else 0,
        "speed_print": printer.print_speed_mm_s, "speed_wall": printer.print_speed_mm_s,
        "speed_wall_0": printer.print_speed_mm_s/2, "speed_wall_x": printer.print_speed_mm_s,
        "speed_infill": printer.print_speed_mm_s, "speed_topbottom": printer.print_speed_mm_s/2,
        "speed_travel": 120, "speed_layer_0": 20,
        "material_diameter": printer.filament_diameter_mm,
        "material_print_temperature": printer.temperature_c, "material_print_temperature_layer_0": printer.temperature_c,
        "material_bed_temperature": printer.bed_temperature_c, "material_bed_temperature_layer_0": printer.bed_temperature_c,
        "support_enable": str(printer.supports).lower(), "support_angle": printer.support_angle_deg,
        "support_type": "everywhere", "support_pattern": "zigzag",
        "adhesion_type": "none", "retraction_enable": "true", "retraction_amount": 1.0,
        "mesh_position_x": center[0]-width/2, "mesh_position_y": center[1]-depth/2, "mesh_position_z": 0,
        "center_object": "true", "meshfix_union_all": "true",
    }
    argv = [str(executable), "slice", "-m2", "-j", str(definition)]
    for key, value in settings.items():
        argv += ["-s", f"{key}={value}"]
    argv += ["-e0", "-j", str(extruder)]
    for key, value in settings.items():
        argv += ["-s", f"{key}={value}"]
    output = destination / "build.gcode"
    argv += ["-l", str(placed), "-o", str(output)]
    _run(argv, destination, cancel)
    if not output.is_file() or output.stat().st_size > 100*1024*1024:
        raise ValueError("CuraEngine did not return a bounded G-code build file")
    analysis = parse_fff(output.read_text(errors="strict"), (machine.travel_min, machine.travel_max))
    log = (destination/"slicer.log").read_text(errors="replace")
    version = re.search(r"Cura_SteamEngine version ([^\s]+)", log)
    if not version:
        raise ValueError("Unable to identify the actual slicer engine/version")
    provenance = {"engine": "CuraEngine", "version": version.group(1), "engine_sha256": digest(executable),
                  "definition_sha256": digest(definition), "extruder_sha256": digest(extruder),
                  "input_mesh_sha256": digest(mesh_path), "placed_mesh_sha256": digest(placed),
                  "output_sha256": digest(output), "settings": settings, "profile_source": printer.profile_source,
                  "license": "AGPL-3.0-or-later; external executable, not vendored", "source": "https://github.com/Ultimaker/CuraEngine"}
    (destination/"slicer-provenance.json").write_text(json.dumps(provenance, indent=2, allow_nan=False)+"\n")
    return {"technology": "fff", "provenance": provenance, "mesh_bounds_mm": mesh.bounds.tolist(),
            "supports_requested": printer.supports, "support_locations_independently_verified": False,
            "analysis": analysis, "physical_print_validated": False,
            "limits": ["Single extruder FFF only", "No material/process calibration or physical print qualification", "No resin/powder/metal/DED build output"]}
