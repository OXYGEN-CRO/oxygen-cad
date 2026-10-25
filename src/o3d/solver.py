"""Bounded linear-static workflow with actual CalculiX results and diagnostics."""
from __future__ import annotations

import json
import math
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


CALCULIX_REAL_PATTERN=r"[+-]?\d\.\d+(?:[EeDd][+-]\d+|[+-]\d{3,})"


def calculix_float(token):
    """Fortran E-format omits E when the exponent needs three digits."""
    token=token.replace('D','E').replace('d','e')
    if re.fullmatch(r'[+-]?(?:\d+\.\d*|\.\d+)[+-]\d{3,}',token):
        token=re.sub(r'([+-]\d{3,})$',r'E\1',token)
    value=float(token)
    if not math.isfinite(value):raise ValueError('Nonfinite CalculiX output')
    return value


def calculix_number(value):
    """A complete finite numeric token for CalculiX's F20.0 input readers.

    Thirteen significant digits fit even a signed three-digit exponent. Longer
    tokens can silently lose the exponent's final digit in nodes.f/CLOAD readers.
    """
    value=float(value)
    if not np.isfinite(value):raise ValueError("CalculiX input must be finite")
    token=f"{value:.12e}"
    if len(token)>20:raise ValueError("CalculiX numeric token exceeds its input field")
    return token


def run_process(command, directory: Path, log: Path, cancel: Event | None = None, timeout=180, *, environment_overrides=None):
    environment = dict(os.environ)
    # Workers change cwd into a job directory. Keep this checkout authoritative,
    # including when development launched with a relative PYTHONPATH=src.
    source = str(Path(__file__).resolve().parents[1])
    paths = [str(Path(p).resolve()) for p in environment.get("PYTHONPATH", "").split(os.pathsep) if p]
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([source, *paths]))
    tools = Path(__file__).resolve().parents[2] / ".tools/root/usr/lib/x86_64-linux-gnu"
    if tools.exists():
        environment["LD_LIBRARY_PATH"] = f"{tools}:{environment.get('LD_LIBRARY_PATH', '')}"
    environment["OMP_NUM_THREADS"] = "2"
    if environment_overrides:environment.update(environment_overrides)
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
        if cancel and cancel.is_set():
            raise Cancelled("Job cancelled")
        if process.returncode != 0:
            raise RuntimeError(f"Worker exited {process.returncode}: {log.read_text()[-2500:]}")


def solver_binary():
    configured = os.environ.get("CCX_BINARY")
    local = Path(__file__).resolve().parents[2] / ".tools/root/usr/bin/ccx"
    result = (shutil.which(configured) or configured) if configured else (shutil.which("ccx") or (str(local) if local.exists() else None))
    if not result:
        raise RuntimeError("CalculiX is unavailable. Run doctor or use the packaged Docker runtime.")
    return str(Path(result).expanduser().resolve())


def mesher_python():
    configured = os.environ.get("O3D_MESH_PYTHON")
    local = Path(__file__).resolve().parents[2] / ".mesh-venv/bin/python"
    return configured or str(local if local.exists() else Path("/opt/mesh/bin/python"))


def load_boundary(mesh, study):
    """Consistent traction integration on planar quadratic triangles.

    For affine faces corner weights are zero and midside weights are A/3.
    Curved planar faces require the full geometry Jacobian; corner weights can
    be nonzero or negative. Equal weights are incorrect in both cases.
    """
    from .quadratic import surface_integrals
    fixed = set()
    for i, _ in enumerate(study.fixed_faces):
        fixed.update(n for tri in mesh["surfaces"][f"fixed_{i}"] for n in tri)
    forces = {}
    for i, load in enumerate(study.forces):
        triangles = mesh["surfaces"][f"load_{i}"]
        weights,total,_=surface_integrals(mesh,triangles)
        for triangle,integrals in zip(triangles,weights):
            for n,weight in zip(triangle,integrals):
                if weight==0:continue
                forces[n] = forces.get(n, np.zeros(3)) + np.array(load.vector_n) * weight / total
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
            values = re.findall(CALCULIX_REAL_PATTERN, line[13:])
            if len(values) >= 6:
                sx, sy, sz, txy, tyz, tzx = [calculix_float(v) for v in values[:6]]
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

    Invert the full quadratic geometry map, including curved edges. This avoids
    corner-only coordinates and whichever node happens to be nearest after remeshing.
    """
    from .quadratic import locate_probe
    element,weights=locate_probe(mesh,point)
    return sum((weight*np.asarray(values[n]) for n,weight in zip(element,weights)),np.asarray(values[element[0]])*0)


def simulate(store: Store, project: Project, cancel=None):
    from .analysis import simulate as run_study
    return run_study(store, project, cancel)
