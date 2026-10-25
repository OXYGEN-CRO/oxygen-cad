"""Lightweight exact solver/source fingerprints without importing numerical libraries."""
from __future__ import annotations
from functools import lru_cache
import hashlib
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path


@lru_cache(maxsize=128)
def _file_hash(path,modified_ns,size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint_file(path):
    path=Path(path).resolve();stat=path.stat()
    return _file_hash(str(path),stat.st_mtime_ns,stat.st_size)


def software_fingerprint(study_type,element_family="C3D4"):
    root=Path(__file__).parent
    sources={name:fingerprint_file(root/name) for name in ("simulation_models.py","simulation_fem.py","simulation_worker.py","simulation_search.py","extended_solver.py","simulation_mesh.py","simulation_mesh_worker.py","simulation_units.py","simulation_quadratic.py","quadratic.py","thermal_exchange.py","analysis_mesh.py","mesh_quality_worker.py","solver.py","analysis.py","domains/simulation.py","simulation_provenance.py")}
    packages={}
    for name in ("numpy","scipy"):
        try:packages[name]=version(name)
        except PackageNotFoundError:packages[name]="unavailable"
    result={"source_sha256":sources,"packages":packages,"element_family":element_family}
    if study_type=="nonlinear_static" or element_family=="C3D10":
        # Resolve a declared backend, never execute a project-controlled command.
        import os,shutil
        configured=os.environ.get("CCX_BINARY")
        local=root.parents[1]/".tools/root/usr/bin/ccx"
        binary=configured or shutil.which("ccx") or (str(local) if local.exists() else None)
        result["calculix_binary_sha256"]=fingerprint_file(binary) if binary and Path(binary).is_file() else "unavailable"
    if element_family=="C3D10":
        from .solver import mesher_python
        python=Path(mesher_python());prefix=python.parent.parent
        runtime={"python":fingerprint_file(python) if python.is_file() else "unavailable"}
        for pattern in ("lib/libgmsh.so*","lib/python*/site-packages/gmsh.py","lib/python*/site-packages/netgen*/*.so*","lib/python*/site-packages/gmsh*.dist-info/METADATA","lib/python*/site-packages/netgen*.dist-info/METADATA"):
            for path in sorted(prefix.glob(pattern)):
                if path.is_file():runtime[str(path.relative_to(prefix))]=fingerprint_file(path)
        result["mesher_runtime_sha256"]=runtime
    return result
