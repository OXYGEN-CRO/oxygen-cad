"""Standard-library-only identity and loaded-library audit of external solvers."""
from __future__ import annotations
import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


@lru_cache(maxsize=256)
def _hash(path,modified_ns,changed_ns,size,inode):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def file_hash(path):
    path=Path(path).resolve();stat=path.stat()
    return _hash(str(path),stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size,stat.st_ino)


def contract():
    return json.loads((Path(__file__).parent/'data/calculix-contact.json').read_text())


def loaded_libraries(binary,library_path=None):
    with Path(binary).open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':return None
    program=shutil.which('ldd')
    if program is None:return None
    environment=dict(os.environ)
    if library_path is not None:environment['LD_LIBRARY_PATH']=library_path
    output=subprocess.run([program,str(binary)],env=environment,text=True,capture_output=True,timeout=10,check=False)
    if output.returncode or 'not found' in output.stdout:raise ValueError('External solver runtime libraries are missing; inspect its configured runtime')
    paths=[]
    for line in output.stdout.splitlines():
        match=re.search(r'(?:=>\s*)?(/.*?)\s+\(0x[0-9a-fA-F]+\)',line)
        if match:paths.append(Path(match[1]).resolve())
    if not paths:raise ValueError('External solver loaded-library identity is unavailable')
    return {str(p):file_hash(p) for p in sorted(set(paths))}


def validated_build(binary):
    binary=Path(binary).resolve();path=binary.with_name('build-manifest.json')
    if not path.exists():return None
    if path.is_symlink() or not path.is_file():raise ValueError('Unsafe CalculiX build receipt')
    value=json.loads(path.read_text());expected=contract()
    for name in ('source_sha256','upstream_nonlingeo_sha256','patched_nonlingeo_sha256','telemetry_header_sha256','native_tangent_header_sha256','accepted_bulk_header_sha256'):
        if value.get(name)!=expected[name]:raise ValueError(f'CalculiX instrumentation receipt differs from the pinned {name}')
    if value.get('schema')!=1 or value.get('telemetry_schema')!=expected['schema'] or value.get('native_tangent_schema')!=1 or value.get('accepted_bulk_schema')!=1 or value.get('binary')!=binary.name:
        raise ValueError('Unsupported CalculiX instrumentation receipt')
    if file_hash(binary)!=value.get('binary_sha256'):raise ValueError('CalculiX binary differs from its instrumentation receipt')
    libraries=value.get('libraries')
    if not isinstance(libraries,dict) or not libraries:raise ValueError('CalculiX build receipt lacks numerical-library provenance')
    parents=[]
    for name,digest in libraries.items():
        dependency=Path(name)
        if not dependency.is_absolute() or not dependency.is_file() or file_hash(dependency)!=digest:
            raise ValueError('CalculiX build numerical library changed or is missing; rebuild against the current runtime')
        if str(dependency.parent) not in parents:parents.append(str(dependency.parent))
    library_path=os.pathsep.join([*parents,*([os.environ['LD_LIBRARY_PATH']] if os.environ.get('LD_LIBRARY_PATH') else [])])
    actual=loaded_libraries(binary,library_path)
    if actual is None or not set(libraries.values())<=set(actual.values()):
        raise ValueError('CalculiX runtime does not load the numerical libraries in its build receipt')
    return {**value,'receipt_sha256':file_hash(path),'runtime_library_path':library_path,'runtime_libraries_sha256':actual}


def validated_material_probe(build):
    """Resolve an administrator-configured, source-bound separate GPL replay tool."""
    configured=os.environ.get('O3D_BULK_KERNEL_EVIDENCE')
    if not configured:return None
    path=Path(configured)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        raise ValueError('Unsafe native material replay receipt')
    value=json.loads(path.read_text());root=path.parent
    if (value.get('schema')!=1 or value.get('material_api')!='native-j2-stress-148-v1'
        or value.get('source_sha256')!=contract()['source_sha256']
        or value.get('native_archive_sha256')!=build.get('verification_archive_sha256')
        or value.get('native_build_receipt_sha256')!=build.get('receipt_sha256')):
        raise ValueError('Native material replay source/build identity mismatch')
    if value.get('binaries',{}).get('material')!='material-kernel-probe':
        raise ValueError('Unsupported native material replay executable')
    expected=value.get('files_sha256',{})
    if expected.get('source/material_kernel_probe.f90')!=contract()['material_probe_source_sha256']:
        raise ValueError('Native material replay driver differs from the pinned source')
    for name in ('source/material_kernel_probe.f90','material-kernel-probe','COPYING'):
        p=root/name
        if p.is_symlink() or not p.is_file() or file_hash(p)!=expected.get(name):
            raise ValueError('Native material replay source or executable changed')
    runtime=value.get('runtime_libraries_sha256',{}).get('material')
    if not isinstance(runtime,dict) or not runtime:raise ValueError('Missing native material replay runtime identity')
    for name,digest in runtime.items():
        p=Path(name)
        if not p.is_absolute() or not p.is_file() or file_hash(p)!=digest:
            raise ValueError('Native material replay runtime library changed')
    library_path=os.pathsep.join(sorted({str(Path(p).parent) for p in runtime}))
    binary=root/'material-kernel-probe'
    if not os.access(binary,os.X_OK):raise ValueError('Native material replay executable cannot run')
    actual=loaded_libraries(binary,library_path)
    if actual!=runtime:raise ValueError('Native material replay loads a different runtime')
    return {'binary':str(binary),'binary_sha256':expected['material-kernel-probe'],
        'receipt_sha256':file_hash(path),'driver_sha256':expected['source/material_kernel_probe.f90'],
        'runtime_library_path':library_path,'runtime_libraries_sha256':actual}
