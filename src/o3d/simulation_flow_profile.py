"""Administrator-configured pinned OpenCFD runtime; no project commands or rc files."""
import json
import os
from pathlib import Path
from .simulation_solver_profile import file_hash,loaded_libraries


def profile():
    configured=os.environ.get('O3D_OPENFOAM_ROOT')
    if not configured:raise ValueError('Laminar CFD runtime unavailable; prepare the pinned isolated OpenFOAM runtime and set O3D_OPENFOAM_ROOT')
    root=Path(configured)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():raise ValueError('OpenFOAM root must be an absolute administrator-configured directory')
    # OpenFOAM searches this directory before project/etc, even with FOAM_ETC.
    if (Path.home()/'.OpenFOAM').exists():raise ValueError('User OpenFOAM configuration would override the qualified runtime; use an isolated account without ~/.OpenFOAM')
    contract_path=Path(__file__).parent/'data/openfoam-laminar.json';contract=json.loads(contract_path.read_text())
    receipt_path=root/'oxygen-openfoam.json'
    if receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size>2_000_000:raise ValueError('Missing or unsafe pinned OpenFOAM preparation receipt')
    receipt=json.loads(receipt_path.read_text())
    if receipt.get('schema')!=1 or receipt.get('contract_sha256')!=file_hash(contract_path):raise ValueError('OpenFOAM receipt differs from the pinned package/source contract')
    tracked=receipt.get('files_sha256')
    if not isinstance(tracked,dict) or not tracked:raise ValueError('OpenFOAM receipt lacks runtime files')
    actual={str(p.relative_to(root)) for folder in ['usr/lib','usr/share/openfoam/etc'] for p in (root/folder).rglob('*') if p.is_file()}
    if actual!=set(tracked):raise ValueError('OpenFOAM runtime/configuration file set changed')
    for name,digest in tracked.items():
        path=root/name
        if not path.resolve().is_relative_to(root.resolve()) or file_hash(path)!=digest:raise ValueError('OpenFOAM runtime/configuration changed: '+name)
    site=root/'empty-site'
    if site.is_symlink() or not site.is_dir() or any(site.iterdir()):raise ValueError('Qualified OpenFOAM site configuration must be empty')
    library_path=os.pathsep.join(str(root/p) for p in ['usr/lib','usr/lib/x86_64-linux-gnu'])
    environment={'LD_LIBRARY_PATH':library_path,'WM_PROJECT_DIR':str(root/'usr/share/openfoam'),'FOAM_ETC':str(root/'usr/share/openfoam/etc'),'WM_PROJECT_SITE':str(site),'FOAM_SIGFPE':'true','FOAM_SETNAN':'true'}
    binaries={};runtime={}
    for name,digest in contract['binaries_sha256'].items():
        binary=root/'usr/bin'/name
        if binary.is_symlink() or not binary.is_file() or file_hash(binary)!=digest:raise ValueError('OpenFOAM executable differs from the pinned binary: '+name)
        binaries[name]=str(binary);runtime[name]=loaded_libraries(binary,library_path)
        if not runtime[name]:raise ValueError('OpenFOAM loaded-library provenance unavailable')
    return {'binaries':binaries,'environment':environment,'identity':{'distribution':contract['distribution'],'version':contract['version'],
        'contract_sha256':file_hash(contract_path),'receipt_sha256':file_hash(receipt_path),'binaries_sha256':contract['binaries_sha256'],
        'runtime_files_sha256':tracked,'loaded_libraries_sha256':runtime,'source':contract['source']}}
