"""Actual UI/CLI service contracts and revision/error boundaries."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from o3d.api import create_app
from o3d.cli import app as cli
from o3d.model import Project
from o3d.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_store(tmp_path):
    store = Store(tmp_path / 'project')
    store.init(Project.model_validate_json((ROOT/'examples/bracket.json').read_text()))
    return store


@pytest.fixture
def client(project_store):
    with TestClient(create_app(project_store.root)) as client:
        yield client


def test_brief_ui_and_cli_share_revision_protected_persistence(client, project_store, tmp_path):
    brief = {'schema_version': 1, 'title': 'Payload mount', 'task': 'Mount a known sensor', 'inputs': [], 'links': []}
    result = client.post('/api/engineering/brief/save', json={'brief': brief, 'expected_revision': 1, 'actor': 'human'})
    assert result.status_code == 200, result.text
    assert result.json()['project']['revision'] == 2
    assert Store(project_store.root).load().engineering_brief.task == 'Mount a known sensor'
    before = (project_store.root/'o3d.json').read_bytes()
    conflict = client.post('/api/engineering/brief/bind', json={'expected_revision': 1})
    assert conflict.status_code == 409
    assert (project_store.root/'o3d.json').read_bytes() == before
    bound = client.post('/api/engineering/brief/bind', json={'expected_revision': 2})
    assert bound.status_code == 200, bound.text
    assert bound.json()['project']['revision'] == 3
    cli_result = CliRunner().invoke(cli, ['--project', str(project_store.root), 'brief', 'inspect', '--json'])
    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.output) == client.get('/api/engineering/brief').json()


def test_engineering_request_bounds_and_missing_fields(client):
    assert client.post('/api/engineering/brief/save', json=[]).status_code == 422
    assert client.post('/api/engineering/brief/save', content='{bad').status_code == 422
    assert client.post('/api/engineering/supplier/evaluate', json={}).status_code == 422
    assert client.post('/api/engineering/brief/bind', json={'expected_revision': True}).status_code == 422
    assert client.post('/api/engineering/brief/bind', json={'expected_revision': 1, 'extra': 3}).status_code == 422
    assert client.post('/api/engineering/no-such-operation/evaluate', json={}).status_code == 422
    too_large = client.post('/api/engineering/brief/save', content=b' ' * (1024*1024+1))
    assert too_large.status_code == 413


def test_examples_pure_evaluations_and_transient_offers(client, project_store, tmp_path):
    assert client.get('/api/engineering/examples/brief').json()['schema_version'] == 1
    assert client.get('/api/engineering/examples/unknown').status_code == 404
    before = (project_store.root/'o3d.json').read_bytes()
    interfaces = client.get('/api/engineering/examples/interfaces').json()
    comparison = client.post('/api/engineering/compatibility/evaluate', json={'a': interfaces['a'], 'b': interfaces['b'], 'fastener_diameter_mm': 3})
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()['overall_status'] == 'unknown'
    proposal = client.post('/api/engineering/compatibility/adapter', json=interfaces)
    assert proposal.status_code == 200, proposal.text
    assert Project.model_validate(proposal.json()['project']).native_document is not None
    procurement = client.get('/api/engineering/examples/procurement').json()
    result = client.post('/api/engineering/procurement/evaluate', json=procurement)
    assert result.status_code == 200, result.text
    assert result.json()['complete'] is False
    assert result.json()['clock'] == 'explicit_snapshot'
    assert (project_store.root/'o3d.json').read_bytes() == before
    # Adapter creation is a new file, never an implicit replacement of a live project.
    source = tmp_path/'adapter-input.json'; source.write_text(json.dumps(interfaces))
    output = project_store.root/'o3d.json'
    blocked = CliRunner().invoke(cli, ['--project', str(project_store.root), 'compatibility', 'adapter', str(source), '--output', str(output)])
    assert blocked.exit_code != 0
    assert output.read_bytes() == before


def test_supplier_source_attachment_has_no_arbitrary_path(client, project_store):
    response = client.post('/api/engineering/supplier/source', json={'content': 'Explicit supplied rule source; not vendor verified.'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['vendor_verified'] is False
    assert project_store.artifact(data['capture']).read_text().startswith('Explicit supplied rule')
    assert client.post('/api/engineering/supplier/source', json={'content': 'bad', 'path': '../escape'}).status_code == 422


def test_supplier_api_prepares_real_package_and_detects_later_changes(client, project_store):
    from datetime import datetime, timedelta, timezone
    from o3d.geometry import build
    build(project_store, project_store.load())
    before = project_store.load()
    handoff = client.post('/api/engineering/supplier/prepare', json={
        'section_z_mm': 2, 'overhang_degrees': 45, 'expected_revision': before.revision})
    assert handoff.status_code == 200, handoff.text
    assert handoff.json()['state'] == 'current'
    captured = client.post('/api/engineering/supplier/source', json={'content': 'Local API fixture; no vendor capability asserted.'}).json()
    example = client.get('/api/engineering/examples/supplier').json()
    profile, request = example['profile'], example['request']
    now = datetime.now(timezone.utc)
    profile['source'].update(capture=captured['capture'], sha256=captured['sha256'],
                             captured_at=now.isoformat(), expires_at=(now+timedelta(days=1)).isoformat())
    request['handoff_manifest'] = handoff.json()['manifest']
    payload = {'profile': profile, 'request': request}
    evaluated = client.post('/api/engineering/supplier/evaluate', json=payload)
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()['supplier_acceptance'] == 'unknown'
    assert evaluated.json()['manufacturability'] == 'unverified'
    packaged = client.post('/api/engineering/supplier/package', json={**payload, 'expected_revision': before.revision})
    assert packaged.status_code == 200, packaged.text
    result = packaged.json()
    assert result['state'] == 'manual_handoff'  # Synthetic profile never becomes a provider approval.
    download = client.get('/api/artifacts/'+result['artifact'])
    assert download.status_code == 200 and download.content.startswith(b'PK')
    changed = dict(request, quantity=request['quantity']+1)
    stale = client.post('/api/engineering/supplier/status', json={
        'manifest': result['manifest'], 'profile': profile, 'request': changed})
    assert stale.status_code == 200 and stale.json()['state'] == 'stale'
    assert project_store.load().model_dump() == before.model_dump()


def test_prepare_refuses_edit_between_service_guard_and_snapshot(client, project_store, monkeypatch):
    from o3d import handoff
    from o3d.geometry import build
    from o3d.model import Command
    build(project_store, project_store.load())
    original = handoff.create
    def interleaved(store, *args, **kwargs):
        current = store.load()
        current.name = 'Concurrent human edit'
        store.apply(Command(expected_revision=current.revision, actor='human', project=current))
        return original(store, *args, **kwargs)
    monkeypatch.setattr(handoff, 'create', interleaved)
    response = client.post('/api/engineering/supplier/prepare', json={
        'section_z_mm': 2, 'overhang_degrees': 45, 'expected_revision': 1})
    assert response.status_code == 409, response.text
    assert project_store.load().revision == 2
    assert not (project_store.cache/'handoffs/latest.json').exists()


def test_engineering_routes_respect_current_project_credentials(client, project_store):
    from o3d.identity import Identity
    identity = Identity(project_store)
    owner = identity.enable('Disposable engineering owner')
    principal = identity.authenticate(owner['token'])
    viewer = identity.issue(principal, 'Disposable viewer', 'viewer')
    editor = identity.issue(principal, 'Disposable editor agent', 'editor', 'agent')
    assert client.get('/api/engineering/brief').status_code == 401
    viewer_headers = {'authorization': 'Bearer '+viewer['token']}
    assert client.get('/api/engineering/brief', headers=viewer_headers).status_code == 200
    assert client.post('/api/engineering/supplier/source', json={'content': 'Not allowed'}, headers=viewer_headers).status_code == 403
    assert not (project_store.cache/'supplier-sources').exists()
    headers = {'authorization': 'Bearer '+editor['token']}
    brief = {'schema_version': 1, 'title': 'Agent brief', 'task': 'Explicit task', 'inputs': [], 'links': []}
    saved = client.post('/api/engineering/brief/save', json={'brief': brief, 'expected_revision': 1, 'actor': 'human'}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert project_store.history()[-1]['actor'] == 'agent'
    identity.revoke(principal, editor['principal']['id'])
    assert client.post('/api/engineering/brief/bind', json={'expected_revision': 2}, headers=headers).status_code == 403
    assert project_store.load().revision == 2


def test_brief_route_cannot_erase_accepted_joint_tasks_by_actor_spoof(client, project_store):
    from o3d import joints
    from o3d.identity import Identity
    from o3d.model import Command
    current = project_store.load()
    data = joints.example()
    # Configure real typed joint contracts through their existing service first.
    configured = joints.configure(project_store, data, current.revision, 'human')
    configured.engineering_brief.joint_requirements[0].state = 'accepted'
    current = project_store.apply(Command(expected_revision=configured.revision, actor='human', project=configured))
    identity = Identity(project_store)
    owner = identity.enable('Disposable owner')
    principal = identity.authenticate(owner['token'])
    agent = identity.issue(principal, 'Disposable agent', 'editor', 'agent')
    before = (project_store.root/'o3d.json').read_bytes()
    brief = current.engineering_brief.model_dump(mode='json')
    brief['joint_requirements'] = []
    denied = client.post('/api/engineering/brief/save', json={'brief': brief, 'expected_revision': current.revision, 'actor': 'human'}, headers={'authorization': 'Bearer '+agent['token']})
    assert denied.status_code == 409, denied.text
    assert (project_store.root/'o3d.json').read_bytes() == before


def test_supplier_export_roles_allow_reader_packages_but_not_source_edits(client, project_store):
    from datetime import datetime, timedelta, timezone
    from o3d.geometry import build
    from o3d.identity import Identity
    build(project_store, project_store.load())
    captured = client.post('/api/engineering/supplier/source', json={
        'content': 'Synthetic permission fixture, not vendor verified.'}).json()
    example = client.get('/api/engineering/examples/supplier').json()
    profile, quote_request = example['profile'], example['request']
    now = datetime.now(timezone.utc)
    profile['source'].update(capture=captured['capture'], sha256=captured['sha256'],
                            captured_at=now.isoformat(), expires_at=(now+timedelta(days=1)).isoformat())
    identity = Identity(project_store)
    owner = identity.enable('Disposable export owner')
    principal = identity.authenticate(owner['token'])
    reader = identity.issue(principal, 'Disposable reader', 'reader')
    viewer = identity.issue(principal, 'Disposable viewer', 'viewer')
    reader_headers = {'authorization': 'Bearer '+reader['token']}
    viewer_headers = {'authorization': 'Bearer '+viewer['token']}
    prepare = {'section_z_mm': 2, 'overhang_degrees': 45, 'expected_revision': 1}
    assert client.post('/api/engineering/supplier/prepare', json=prepare, headers=viewer_headers).status_code == 403
    handoff = client.post('/api/engineering/supplier/prepare', json=prepare, headers=reader_headers)
    assert handoff.status_code == 200, handoff.text
    quote_request['handoff_manifest'] = handoff.json()['manifest']
    payload = {'profile': profile, 'request': quote_request, 'expected_revision': 1}
    assert client.post('/api/engineering/supplier/package', json=payload, headers=viewer_headers).status_code == 403
    package = client.post('/api/engineering/supplier/package', json=payload, headers=reader_headers)
    assert package.status_code == 200, package.text
    assert client.get('/api/artifacts/'+package.json()['artifact'], headers=reader_headers).status_code == 200
    assert client.post('/api/engineering/supplier/source', json={'content': 'Denied'}, headers=reader_headers).status_code == 403
    assert client.post('/api/engineering/supplier/response', json={
        'manifest': package.json()['manifest'], 'response': {}, 'expected_revision': 1},
        headers=reader_headers).status_code == 403
    identity.revoke(principal, reader['principal']['id'])
    assert client.post('/api/engineering/supplier/package', json=payload, headers=reader_headers).status_code == 403
