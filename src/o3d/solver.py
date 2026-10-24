"""Bounded linear-static workflow with actual CalculiX results and diagnostics."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import numpy as np

from .model import Project, geometry_key, study_key
from .store import Store, now, write_json


class Cancelled(RuntimeError):
    pass


def run_process(command, directory: Path, log: Path, cancel: Event | None = None, timeout=180):
    environment = dict(os.environ)
    tools = Path(__file__).resolve().parents[2] / ".tools/root/usr/lib/x86_64-linux-gnu"
    if tools.exists():
        environment["LD_LIBRARY_PATH"] = f"{tools}:{environment.get('LD_LIBRARY_PATH', '')}"
    environment["OMP_NUM_THREADS"] = "2"
    with log.open("w") as output:
        process = subprocess.Popen([str(v) for v in command], cwd=directory, stdout=output, stderr=subprocess.STDOUT,
                                   env=environment, start_new_session=True)
        start = time.monotonic()
        while process.poll() is None:
            if (cancel and cancel.is_set()) or time.monotonic() - start > timeout:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                if cancel and cancel.is_set():
                    raise Cancelled("Job cancelled")
                raise RuntimeError(f"Worker timed out after {timeout}s; inspect {log.name}")
            time.sleep(0.1)
        if process.returncode != 0:
            raise RuntimeError(f"Worker exited {process.returncode}: {log.read_text()[-2500:]}")


def solver_binary():
    configured = os.environ.get("CCX_BINARY")
    local = Path(__file__).resolve().parents[2] / ".tools/root/usr/bin/ccx"
    result = configured or shutil.which("ccx") or (str(local) if local.exists() else None)
    if not result:
        raise RuntimeError("CalculiX is unavailable. Run doctor or use the packaged Docker runtime.")
    return result


def mesher_python():
    configured = os.environ.get("O3D_MESH_PYTHON")
    local = Path(__file__).resolve().parents[2] / ".mesh-venv/bin/python"
    return configured or str(local if local.exists() else Path("/opt/mesh/bin/python"))


def load_boundary(mesh, study):
    """Consistent traction integration on planar quadratic triangles.

    Integrals of corner shape functions are zero and each midside shape
    function integrates to A/3. Equal weights on all six nodes are incorrect.
    """
    nodes = {int(k): np.array(v, dtype=float) for k, v in mesh["nodes"].items()}
    fixed = set()
    for i, _ in enumerate(study.fixed_faces):
        fixed.update(n for tri in mesh["surfaces"][f"fixed_{i}"] for n in tri)
    forces = {}
    for i, load in enumerate(study.forces):
        triangles = mesh["surfaces"][f"load_{i}"]
        areas = [np.linalg.norm(np.cross(nodes[t[1]] - nodes[t[0]], nodes[t[2]] - nodes[t[0]])) / 2 for t in triangles]
        total = sum(areas)
        if total <= 0:
            raise ValueError("Loaded region has no area")
        for triangle, area in zip(triangles, areas):
            for n in triangle[3:]:
                forces[n] = forces.get(n, np.zeros(3)) + np.array(load.vector_n) * area / (3 * total)
    if fixed.intersection(forces):
        raise ValueError("A load region overlaps a fixed region; select separate faces")
    return fixed, forces


def write_deck(path, mesh, project):
    fixed, forces = load_boundary(mesh, project.study)
    nodes = {int(k): v for k, v in mesh["nodes"].items()}
    lines = ["*HEADING", "Oxygen CAD linear static; mm, N, MPa", "*NODE"]
    lines += [f"{n}, {p[0]:.12g}, {p[1]:.12g}, {p[2]:.12g}" for n, p in sorted(nodes.items())]
    lines += ["*ELEMENT,TYPE=C3D10,ELSET=EALL"]
    lines += [f"{i}, " + ", ".join(map(str, e)) for i, e in enumerate(mesh["elements"], 1)]
    lines += ["*NSET,NSET=ALLNODES"]
    ordered = sorted(nodes)
    lines += [", ".join(map(str, ordered[i:i + 16])) for i in range(0, len(ordered), 16)]
    lines += ["*NSET,NSET=FIXED"]
    ordered = sorted(fixed)
    lines += [", ".join(map(str, ordered[i:i + 16])) for i in range(0, len(ordered), 16)]
    material = project.material
    lines += ["*MATERIAL,NAME=MAT", "*ELASTIC", f"{material.youngs_modulus_mpa}, {material.poisson_ratio}",
              "*SOLID SECTION,ELSET=EALL,MATERIAL=MAT", "*BOUNDARY", "FIXED,1,3", "*STEP", "*STATIC", "*CLOAD"]
    for node, force in sorted(forces.items()):
        for axis, value in enumerate(force, 1):
            if abs(value) > 1e-14:
                lines.append(f"{node},{axis},{value:.12g}")
    lines += ["*NODE PRINT,NSET=ALLNODES", "U", "*NODE PRINT,NSET=FIXED", "RF",
              "*NODE FILE", "U,RF", "*EL FILE", "S", "*END STEP"]
    path.write_text("\n".join(lines) + "\n")
    return fixed, forces


def parse_dat(path):
    displacement, reactions = {}, {}
    target = None
    for line in path.read_text().splitlines():
        lower = line.lower()
        if "displacements" in lower:
            target = displacement
        elif "forces" in lower:
            target = reactions
        elif target is not None:
            pieces = line.split()
            if len(pieces) >= 4 and pieces[0].isdigit():
                try:
                    target[int(pieces[0])] = [float(x.replace("D", "E")) for x in pieces[1:4]]
                except ValueError:
                    pass
    if not displacement or not reactions:
        raise RuntimeError("Solver output is missing displacements or reactions")
    return displacement, reactions


def parse_stress(path):
    stress = {}
    active = False
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith(" -4"):
            active = "STRESS" in line
        elif active and line.startswith(" -1"):
            node = int(line[3:13])
            values = re.findall(r"[+-]?\d\.\d+(?:[ED][+-]\d+)", line[13:])
            if len(values) >= 6:
                sx, sy, sz, txy, tyz, tzx = [float(v.replace("D", "E")) for v in values[:6]]
                stress[node] = float(np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2)))
    if not stress:
        raise RuntimeError("Solver output has no nodal stress field")
    return stress


def preflight(project):
    errors = []
    if project.material is None:
        errors.append("Specify material elastic properties, density and data source")
    if not project.study.fixed_faces:
        errors.append("Select at least one fully fixed planar face")
    if not project.study.forces or not any(np.linalg.norm(f.vector_n) > 0 for f in project.study.forces):
        errors.append("Specify a nonzero force and its application face")
    names = project.study.fixed_faces + [f.face for f in project.study.forces]
    if len(names) != len(set(names)):
        errors.append("Load and fixed regions must be distinct and unique")
    if not project.study.assumptions_confirmed:
        errors.append("Review and confirm small-deflection, linear-elastic, static assumptions")
    if errors:
        raise ValueError("; ".join(errors))


def interpolate_probe(mesh, values, point):
    """Sample a fixed geometric point using quadratic tetrahedral shape functions.

    This avoids comparing whichever node happens to be nearest after remeshing.
    The bounded workflow uses a planar loaded-face centroid and interior probes.
    """
    coordinates = {int(k): np.asarray(v) for k, v in mesh["nodes"].items()}
    for element in mesh["elements"]:
        corners = np.array([coordinates[n] for n in element[:4]])
        matrix = (corners[:3] - corners[3]).T
        try:
            first = np.linalg.solve(matrix, np.asarray(point) - corners[3])
        except np.linalg.LinAlgError:
            continue
        barycentric = np.append(first, 1 - first.sum())
        if barycentric.min() < -1e-6 or barycentric.max() > 1 + 1e-6:
            continue
        weights = [x * (2*x - 1) for x in barycentric]
        weights += [4*barycentric[i]*barycentric[j] for i,j in ((0,1),(1,2),(2,0),(0,3),(1,3),(2,3))]
        return sum((weight * np.asarray(values[n]) for n, weight in zip(element, weights)), np.asarray(values[element[0]]) * 0)
    raise ValueError("The named probe could not be located inside the analysis mesh; choose an interior point away from idealized supports")


def simulate(store: Store, project: Project, cancel=None):
    from .geometry import build, resolve_face, vector
    import build123d as bd
    preflight(project)
    gkey, skey = geometry_key(project), study_key(project)
    build_dir = store.cache / "builds" / gkey
    if not (build_dir / "build.json").exists():
        build(store, project)
    shape = bd.import_brep(build_dir / "part.brep")
    regions = []
    for prefix, selections in (("fixed", project.study.fixed_faces), ("load", [f.face for f in project.study.forces])):
        for i, selection in enumerate(selections):
            face = resolve_face(shape, selection)
            if face.geom_type != bd.GeomType.PLANE:
                raise ValueError("The alpha supports only planar support and load faces")
            regions.append({"name": f"{prefix}_{i}", "center": vector(face.center()), "area": face.area})
    destination = store.cache / "studies" / skey
    destination.mkdir(parents=True, exist_ok=True)
    iterations = []
    previous = None
    field = None
    converged = False
    start = time.monotonic()
    for iteration in range(project.study.refinement_steps):
        if cancel and cancel.is_set():
            raise Cancelled("Job cancelled")
        run = destination / f"mesh-{iteration}"
        run.mkdir(exist_ok=True)
        size = project.study.mesh_size_mm / (1.5 ** iteration)
        request = {"step": str(build_dir / "part.step"), "regions": regions, "mesh_size": size}
        write_json(run / "request.json", request)
        run_process([mesher_python(), Path(__file__).with_name("mesh_worker.py"), run / "request.json", run / "mesh.json"], run, run / "mesher.log", cancel)
        mesh = json.loads((run / "mesh.json").read_text())
        fixed, loads = write_deck(run / "analysis.inp", mesh, project)
        run_process([solver_binary(), "analysis"], run, run / "solver.log", cancel)
        if "*ERROR" in (run / "solver.log").read_text().upper():
            raise RuntimeError("CalculiX reported an error; inspect the solver log")
        displacement, reactions = parse_dat(run / "analysis.dat")
        stress = parse_stress(run / "analysis.frd")
        coords = {int(k): np.array(v) for k, v in mesh["nodes"].items()}
        maximum = max(np.linalg.norm(v) for v in displacement.values())
        displacement_probe = next(region["center"] for region in regions if region["name"] == "load_0")
        probe_displacement = float(np.linalg.norm(interpolate_probe(mesh, displacement, displacement_probe)))
        applied = sum(loads.values(), np.zeros(3))
        reacted = sum((np.array(v) for v in reactions.values()), np.zeros(3))
        force_error = float(np.linalg.norm(applied + reacted) / max(np.linalg.norm(applied), 1e-12))
        applied_moment = sum((np.cross(coords[n], v) for n, v in loads.items()), np.zeros(3))
        reacted_moment = sum((np.cross(coords[n], v) for n, v in reactions.items()), np.zeros(3))
        moment_scale = max(np.linalg.norm(applied_moment), np.linalg.norm(applied) * np.linalg.norm(np.ptp(np.array(list(coords.values())), axis=0)), 1e-12)
        moment_error = float(np.linalg.norm(applied_moment + reacted_moment) / moment_scale)
        probe_stress = None
        if project.study.stress_probe_mm is not None:
            probe_stress = float(interpolate_probe(mesh, stress, project.study.stress_probe_mm))
        delta = abs(probe_displacement - previous[0]) / max(probe_displacement, 1e-12) if previous else None
        stress_delta = abs(probe_stress - previous[1]) / max(abs(probe_stress), 1e-12) if previous and probe_stress is not None else None
        converged = delta is not None and delta <= project.study.convergence_tolerance and (stress_delta is None or stress_delta <= project.study.convergence_tolerance)
        iterations.append({"mesh_size_mm": size, "nodes": len(mesh["nodes"]), "elements": len(mesh["elements"]), "max_displacement_mm": maximum,
                           "displacement_probe_mm": displacement_probe, "probe_displacement_mm": probe_displacement,
                           "stress_probe_mpa": probe_stress, "displacement_change": delta, "stress_probe_change": stress_delta,
                           "force_balance_error": force_error, "moment_balance_error": moment_error})
        boundary = [tri[:3] for triangles in mesh["surfaces"].values() for tri in triangles]
        # Include the entire boundary, not only the selected support/load regions.
        field = {"nodes": mesh["nodes"], "displacements": displacement, "stress_mpa": stress, "boundary_triangles": boundary}
        previous = probe_displacement, probe_stress
        if converged:
            break
    # Field rendering uses all element exterior faces when region-only triangles are insufficient.
    counts = {}
    for element in mesh["elements"]:
        for indices in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
            triangle = tuple(element[i] for i in indices)
            key = tuple(sorted(triangle))
            counts[key] = (counts.get(key, (0, triangle))[0] + 1, triangle)
    field["boundary_triangles"] = [list(tri) for count, tri in counts.values() if count == 1]
    balanced = force_error <= 0.01 and moment_error <= 0.01
    findings = []
    for req in project.requirements:
        if req.enabled and req.kind == "max_displacement":
            findings.append({"id": req.id, "value": maximum, "limit": req.value, "passed": bool(maximum <= req.value)})
    state = "inconclusive" if not converged or not balanced else "failed" if any(not f["passed"] for f in findings) else "passed"
    warnings = ["Results apply only to the specified idealized linear-elastic model.", "Peak stresses at fixtures and sharp corners may be singular; this study does not certify strength."]
    if not project.study.stress_probe_mm:
        warnings.append("No named stress probe: convergence applies to displacement only; stress is a screening field.")
    if not balanced:
        warnings.append("Reaction equilibrium check failed")
    if not converged:
        warnings.append("Mesh refinement did not converge within the allowed steps")
    result = {"key": skey, "geometry_key": gkey, "revision": project.revision, "state": state, "iterations": iterations,
              "max_displacement_mm": maximum, "max_stress_mpa": max(stress.values()), "converged": converged,
              "force_balance_error": force_error, "moment_balance_error": moment_error,
              "findings": findings, "warnings": warnings, "elapsed_seconds": time.monotonic() - start,
              "solver": "CalculiX", "material": project.material.model_dump(), "study": project.study.model_dump(), "completed_at": now(),
              "field_artifact": f"studies/{skey}/field.json", "logs": [f"studies/{skey}/mesh-{i}/solver.log" for i in range(len(iterations))]}
    write_json(destination / "field.json", field)
    write_json(destination / "result.json", result)
    write_json(store.cache / "last-study.json", result)
    return result
