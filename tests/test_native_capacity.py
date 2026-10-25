"""Bounded large canonical assemblies keep actual meshes and protocol refusals."""
import json
import math
import subprocess
import zipfile

import pytest

from o3d.model import Project
from o3d.native import NativeSession, engine_binary
from o3d.native_document import _check_imported_design, query_native
from o3d.native_limits import MAX_NATIVE_DOCUMENT_BYTES, MAX_NATIVE_REQUEST_BYTES
from o3d.projects import portable_archive, restore_archive
from o3d.store import Store


def _torus(path, major_count=520, minor_count=220):
    """Actual closed tessellated geometry, rather than inert metadata padding."""
    with path.open('w') as output:
        for i in range(major_count):
            a = 2 * math.pi * i / major_count
            for j in range(minor_count):
                b = 2 * math.pi * j / minor_count
                r = 20 + 4 * math.cos(b)
                output.write(f'v {r*math.cos(a):.15g} {r*math.sin(a):.15g} {4*math.sin(b):.15g}\n')
        for i in range(major_count):
            for j in range(minor_count):
                a = i * minor_count + j + 1
                b = ((i + 1) % major_count) * minor_count + j + 1
                c = ((i + 1) % major_count) * minor_count + (j + 1) % minor_count + 1
                d = i * minor_count + (j + 1) % minor_count + 1
                output.write(f'f {a} {b} {c}\nf {a} {c} {d}\n')


def test_real_large_mesh_assembly_persists_and_reopens(tmp_path):
    mesh = tmp_path / 'torus.obj'
    _torus(mesh)
    with NativeSession() as engine:
        for name in ('Left torus', 'Right torus'):
            engine.execute('component.activate', {'component': 0})
            inserted = engine.execute('file.insert_mesh', {'path': str(mesh), 'name': name})
            if name == 'Right torus':
                engine.execute('occurrence.move', {'occurrence': inserted['occurrence'], 'translate': [70, 0, 0]})
        snapshot = engine.execute('oxygen.document.get')
    document_bytes = len(json.dumps(snapshot['document'], allow_nan=False).encode())
    assert 15 * 1024 * 1024 < document_bytes <= MAX_NATIVE_DOCUMENT_BYTES
    assert 16 * 1024 * 1024 < len(json.dumps(snapshot, separators=(',', ':')).encode())
    project = Project(name='Large mesh assembly regression', native_document=snapshot['document'],
        domains={'modeling': {'schema_version': 1, 'native_context': snapshot['context']}})
    store = Store(tmp_path / 'source'); store.init(project)
    archive = portable_archive(store)
    restore_archive(archive['path'], tmp_path / 'restored')
    reopened = Store(tmp_path / 'restored')
    assert reopened.load().native_document == project.native_document
    result = query_native(reopened.load(), 'assembly.bom', {}, store=reopened)
    assert result['total_occurrences'] == 2
    with NativeSession() as engine:
        engine.execute('oxygen.document.set', snapshot)
        roundtrip = engine.execute('oxygen.document.get')
    assert roundtrip['document'] == snapshot['document']
    assert sum(len(mesh['triangles']) // 3 for feature in roundtrip['document']['features']
        for mesh in feature.get('meshes', [])) == 4 * 520 * 220


def test_document_and_native_import_budgets_still_refuse(tmp_path):
    document = {'format': 'solidcraft-v1', 'features': [], 'params': [],
                'name': 'x' * MAX_NATIVE_DOCUMENT_BYTES}
    with pytest.raises(ValueError, match='Native document exceeds 32 MiB'):
        Project(native_document=document)
    source = tmp_path / 'oversize.solidcraft'
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('document.json', ' ' * (MAX_NATIVE_DOCUMENT_BYTES + 1))
    with pytest.raises(ValueError, match='Imported native document exceeds 32 MiB'):
        _check_imported_design(source)


def test_host_and_actual_engine_request_budgets_still_refuse():
    params = {'padding': 'x' * MAX_NATIVE_REQUEST_BYTES}
    with NativeSession() as engine:
        with pytest.raises(ValueError, match='Engine request exceeds 34 MiB'):
            engine.execute('oxygen.version', params)
        # A rejected host request does not corrupt the live native session.
        assert engine.execute('oxygen.version')['protocol'] == 1
        prefix = len(json.dumps({'command': 'oxygen.version', 'params': {'padding': ''}},
                                separators=(',', ':')).encode())
        boundary = {'padding': 'x' * (MAX_NATIVE_REQUEST_BYTES - prefix - 1)}
        assert engine.execute('oxygen.version', boundary)['protocol'] == 1
        with pytest.raises(ValueError, match='Engine request exceeds 34 MiB'):
            engine.execute('oxygen.version', {'padding': boundary['padding'] + 'x'})
    request = json.dumps({'command': 'oxygen.version', 'params': params}, separators=(',', ':'))
    raw = subprocess.run([engine_binary()], input=request + '\n', text=True,
                         capture_output=True, timeout=30, check=True)
    assert json.loads(raw.stdout) == {'ok': False, 'error': 'Request exceeds 34 MiB'}
