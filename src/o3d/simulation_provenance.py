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


def software_fingerprint(study_type):
    root=Path(__file__).parent
    sources={name:fingerprint_file(root/name) for name in ("simulation_models.py","simulation_fem.py","simulation_worker.py","simulation_search.py","extended_solver.py","simulation_mesh.py","simulation_units.py","simulation_provenance.py")}
    packages={}
    for name in ("numpy","scipy"):
        try:packages[name]=version(name)
        except PackageNotFoundError:packages[name]="unavailable"
    result={"source_sha256":sources,"packages":packages}
    if study_type=="nonlinear_static":
        # Resolve a declared backend, never execute a project-controlled command.
        import os,shutil
        configured=os.environ.get("CCX_BINARY")
        local=root.parents[1]/".tools/root/usr/bin/ccx"
        binary=configured or shutil.which("ccx") or (str(local) if local.exists() else None)
        result["calculix_binary_sha256"]=fingerprint_file(binary) if binary and Path(binary).is_file() else "unavailable"
    return result
