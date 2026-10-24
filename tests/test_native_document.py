"""Measured source-engine document/revision/multibody/surface/import acceptance."""
import hashlib
import json

import build123d as bd
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from o3d.geometry import build
from o3d.model import Command, NativeFeature, Parameter, Project, geometry_key
from o3d.native import NativeSession
from o3d.native_document import execute_native, inspect_native, opened_native, prepare_native_command, query_native
from o3d.native_routes import mount_native_routes
from o3d.store import Conflict, Store


def native_project(commands):
    with NativeSession() as session:
        for command, params in commands:
            session.execute(command, params)
        snapshot = session.execute('oxygen.document.get')
    return Project(name='Native fixture', native_document=snapshot['document'],
        domains={'modeling': {'schema_version': 1, 'native_context': snapshot['context']}})


def test_native_transactions_persist_reopen_revision_and_undo(tmp_path):
    store = Store(tmp_path)
    store.init(Project())
    response = execute_native(store, 1, 'human', 'solid.box', {'length': 20, 'width': 10, 'height': 5, 'body_name': 'Plate'})
    assert response['project'].revision == 2
    native = store.load().native_document
    assert native['format'] and native['features'][0]['id']
    first_id = native['features'][0]['id']
    execute_native(store, 2, 'human', 'timeline.edit', {'feature': first_id, 'set': {'length': '30'}})
    reopened = Store(tmp_path).load()
    assert reopened.native_document['features'][0]['id'] == first_id
    assert query_native(reopened, 'inspect.measure')['total']['volume_mm3'] == pytest.approx(1500)
    with pytest.raises(Conflict, match='Revision conflict'):
        execute_native(store, 2, 'human', 'solid.sphere', {'radius': 2})
    undone = store.apply(Command(expected_revision=3, actor='human', action='undo'))
    assert query_native(undone, 'inspect.measure')['total']['volume_mm3'] == pytest.approx(1000)
    assert store.apply(Command(expected_revision=4, actor='human', action='redo')).native_document == reopened.native_document


def test_multibody_scene_keeps_separate_exact_solids_and_topology(tmp_path):
    project = native_project([
        ('solid.box', {'length': 20, 'width': 10, 'height': 5, 'body_name': 'First'}),
        ('solid.box', {'length': 8, 'width': 6, 'height': 4, 'corner': [40, 0, 0], 'body_name': 'Second'}),
    ])
    store = Store(tmp_path); store.init(project)
    result = build(store, project)
    assert result['volume_mm3'] == pytest.approx(1192)
    assert len(bd.import_step(store.artifact(result['artifacts']['step'])).solids()) == 2
    bodies = result['scene']['bodies']
    assert {b['name'] for b in bodies} == {'First', 'Second'}
    assert len({b['id'] for b in bodies}) == 2
    assert all(b['geometry_kind'] == 'solid' and b['faces'] for b in bodies)
    assert all(store.artifact(b['artifacts']['step']).is_file() for b in bodies)
    assert query_native(project, 'model.faces', {'body': 'First'})['faces'][0]['name']
    assert max(b['volume_relative_error'] for b in result['engine']['independent_bodies']) < 1e-6


def test_active_sketch_entities_constraints_and_parameters_survive_cold_open(tmp_path):
    store = Store(tmp_path); store.init(Project())
    execute_native(store, 1, 'human', 'parameters.add', {'name': 'width', 'expression': '20 mm'})
    execute_native(store, 2, 'human', 'sketch.create', {'name': 'Section', 'plane': 'XY'})
    rectangle = execute_native(store, 3, 'human', 'sketch.rectangle.two_point', {'p0': [0, 0], 'p1': [20, 10]})['result']
    curve = rectangle['curves'][0]
    execute_native(store, 4, 'human', 'sketch.dimension', {'entities': [curve], 'value': 'width'})
    reopened = Store(tmp_path).load()
    inspected = inspect_native(reopened)
    assert inspected['inspection']['active_sketch']
    assert any(c['id'] == curve for c in inspected['sketches'][0]['curves'])
    assert any(c.get('param') for c in inspected['sketches'][0]['constraints'])
    assert any(c['id'] == 'sketch.dimension' and c['enabled'] and c['host_allowed'] for c in inspected['commands'])
    execute_native(store, 5, 'human', 'sketch.finish')
    execute_native(store, 6, 'human', 'solid.extrude', {'distance': 3, 'operation': 'new'})
    execute_native(store, 7, 'human', 'parameters.change', {'name': 'width', 'expression': '30 mm'})
    assert query_native(store.load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(900, rel=1e-5)
    assert store.load().native_document['features'][0]['id'] == reopened.native_document['features'][0]['id']


def test_immutable_asset_import_embeds_editable_geometry_and_rejects_unsafe_paths(tmp_path):
    source = tmp_path / 'source.step'; bd.export_step(bd.Box(4, 5, 6), source)
    content = source.read_bytes(); sha = hashlib.sha256(content).hexdigest()
    assets = tmp_path / 'assets'; assets.mkdir(); (assets / f'{sha}.step').write_bytes(content)
    project = Project(domains={'interop': {'schema_version': 1, 'assets': [{'id': 'cube', 'sha256': sha, 'file': f'assets/{sha}.step'}]}})
    store = Store(tmp_path); store.init(project)
    execute_native(store, 1, 'human', 'file.insert_step', {'asset_id': 'cube'})
    assert query_native(Store(tmp_path).load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(120)
    # Imported geometry is embedded in the native design: cold-open does not depend on the source path.
    (assets / f'{sha}.step').unlink()
    assert query_native(store.load(), 'inspect.measure')['body_count'] == 1
    with pytest.raises(ValueError, match='Filesystem'):
        execute_native(store, 2, 'human', 'file.insert_step', {'path': '/etc/passwd'})
    with pytest.raises(ValueError, match='asset'):
        execute_native(store, 2, 'human', 'file.insert_step', {'asset_id': 'cube'})
    assert store.load().revision == 2


def test_native_api_reports_failure_without_committing_and_read_queries_do_not_edit(tmp_path):
    store = Store(tmp_path); store.init(Project())
    app = FastAPI(); mount_native_routes(app, store, None)
    client = TestClient(app)
    response = client.post('/api/native/execute', json={'expected_revision': 1, 'actor': 'human', 'command': 'solid.box', 'params': {'length': -3}})
    assert response.status_code == 422 and store.load().revision == 1
    response = client.post('/api/native/execute', json={'expected_revision': 1, 'actor': 'human', 'command': 'engine.describe', 'params': {'command': 'solid.box'}})
    assert response.status_code == 200 and response.json()['result']['id'] == 'solid.box'
    assert store.load().revision == 1
    assert client.get('/api/native/inspect').status_code == 200


def test_legacy_parameter_bindings_migrate_into_native_graph(tmp_path):
    project = Project(parameters={'length': Parameter(value=20)}, features=[NativeFeature(id='box', command='solid.box', params={'length': '$length', 'width': 10, 'height': 5})])
    store = Store(tmp_path); store.init(project)
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'length', 'expression': '30 mm'})
    assert store.load().native_document is not None
    assert query_native(store.load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(1500)
    before = geometry_key(store.load()); changed = store.load().model_copy(deep=True)
    changed.native_document['name'] = 'Changed native document'
    assert geometry_key(changed) != before
