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
    # A worker runs in a job directory. Keep it on the same package revision as
    # its parent, including source checkouts sharing an editable environment.
    package_root = str(Path(__file__).resolve().parent.parent)
    search_paths = [str(Path(p).resolve()) for p in environment.get("PYTHONPATH", "").split(os.pathsep) if p]
    if "o3d.worker" in [str(v) for v in command]:
        environment["PYTHONPATH"] = os.pathsep.join([package_root, *search_paths])
    elif search_paths:
        environment["PYTHONPATH"] = os.pathsep.join(search_paths)
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
    from .analysis import validate_study
    errors = validate_study(project)
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
    from .analysis import simulate as run_study
    return run_study(store, project, cancel)
