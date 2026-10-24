"""First native migrations preserve user locks at the actual Store boundary."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from o3d.model import Command, NativeFeature, Parameter, Project, Requirement
from o3d.native_document import execute_native, prepare_native_command, query_native
from o3d.native_routes import mount_native_routes
from o3d.store import Conflict, Store


def locked_project(name):
    return Project(parameters={name: Parameter(value=20, locked=True)},
        features=[NativeFeature(id='base', command='solid.box', params={'length': '$'+name, 'width': 10, 'height': 5, 'body_name': 'Base'})],
        requirements=[Requirement(id='envelope', kind='max_dimension', axis='X', value=100)])


@pytest.mark.parametrize('name', ['length', 'bad-name'])
def test_first_agent_native_api_edit_cannot_replace_locked_binding(tmp_path, name):
    store = Store(tmp_path); original = locked_project(name); store.init(original)
    feature = query_native(original, 'document.inspect')['timeline'][0]['id']
    app = FastAPI(); mount_native_routes(app, store, None)
    response = TestClient(app).post('/api/native/execute', json={'expected_revision': 1, 'actor': 'agent',
        'command': 'timeline.edit', 'params': {'feature': feature, 'set': {'length': '30'}}})
    assert response.status_code == 409 and 'locked' in response.json()['detail']
    assert store.load() == original
    assert len(store.history()) == 1
    assert query_native(store.load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(1000)


@pytest.mark.parametrize('name', ['length', 'bad-name'])
def test_first_migration_full_project_replacement_cannot_bypass_locked_binding(tmp_path, name):
    store = Store(tmp_path); original = locked_project(name); store.init(original)
    feature = query_native(original, 'document.inspect')['timeline'][0]['id']
    proposal, _ = prepare_native_command(original, 'timeline.edit', {'feature': feature, 'set': {'length': '30'}}, store)
    # This reproduces the bypass: provenance and host numeric value remain unchanged.
    assert proposal.features == original.features and proposal.parameters == original.parameters
    with pytest.raises(Conflict, match='locked'):
        store.apply(Command(expected_revision=1, actor='agent', project=proposal))
    assert store.load() == original and len(store.history()) == 1
    # A human may explicitly replace their own binding.
    updated = store.apply(Command(expected_revision=1, actor='human', project=proposal))
    assert updated.revision == 2
    assert query_native(updated, 'inspect.measure')['total']['volume_mm3'] == pytest.approx(1500)


@pytest.mark.parametrize('name', ['length', 'bad-name'])
def test_valid_first_agent_migration_adds_body_without_changing_locks(tmp_path, name):
    store = Store(tmp_path); original = locked_project(name); store.init(original)
    response = execute_native(store, 1, 'agent', 'solid.box', {'length': 5, 'width': 5, 'height': 5, 'corner': [30, 0, 0], 'body_name': 'Auxiliary'})
    updated = response['project']
    assert updated.revision == 2 and updated.native_document is not None
    assert updated.parameters == original.parameters and updated.requirements == original.requirements
    alias = updated.domains['modeling']['legacy_parameter_map'][name]
    assert updated.domains['modeling']['parameter_locks'][alias] is True
    measured = query_native(Store(tmp_path).load(), 'inspect.measure')
    assert measured['body_count'] == 2 and measured['total']['volume_mm3'] == pytest.approx(1125)


def test_unverifiable_first_migration_refuses_agent_with_recoverable_instruction(tmp_path, monkeypatch):
    store = Store(tmp_path); original = locked_project('length'); store.init(original)
    proposal, _ = prepare_native_command(original, 'solid.box', {'length': 5, 'width': 5, 'height': 5, 'corner': [30, 0, 0]}, store)
    monkeypatch.setenv('O3D_ENGINE_BINARY', '/missing/native-engine')
    with pytest.raises(Conflict, match='human migrate this design first'):
        store.apply(Command(expected_revision=1, actor='agent', project=proposal))
    assert store.load() == original
