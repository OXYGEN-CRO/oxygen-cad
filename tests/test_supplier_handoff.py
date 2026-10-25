"""Real CAD/handoff files, provenance, invalidation and bounded offline packages."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import zipfile

import pytest

from o3d.geometry import build
from o3d.handoff import create as create_handoff
from o3d.model import Command
from o3d.supplier_handoff import capture_source, create_request, preflight, record_response, status
from o3d.supplier_models import SupplierRequest, SupplierResponse

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def source(store, name='capability.txt', content=b'Locally supplied test evidence, no real vendor assertions.\n'):
    path = store.cache / 'supplier-sources' / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return dict(url='https://example.invalid/fixture', capture=str(path.relative_to(store.cache)),
                sha256=hashlib.sha256(content).hexdigest(), captured_at=(NOW-timedelta(days=1)).isoformat(),
                expires_at=(NOW+timedelta(days=1)).isoformat(), kind='supplied_capture')


@pytest.fixture
def inputs(store):
    # This creates native geometry, CAD exports and derivative artifacts. No mocks.
    build(store, store.load())
    frozen = create_handoff(store, 0)
    profile = dict(provider='fixture-provider', record=dict(external_id='fixture-1', name='Test fixture',
        record_type='capability', process='fixture-process', material='fixture-material',
        source_url='https://example.invalid/fixture', facts={'supported_formats': ['step']},
        constraints=[dict(key=key, value=value, unit=unit, source_field='fixture.'+key) for key, value, unit in
            [('max_x', 200, 'mm'), ('max_y', 200, 'mm'), ('max_z', 200, 'mm'),
             ('min_quantity', 1, 'count'), ('max_quantity', 100, 'count')]]), source=source(store))
    request = dict(request_id='fixture-request-1', provider='fixture-provider', process='fixture-process',
        material='fixture-material', quantity=2, handoff_manifest=frozen['manifest'], artifacts=['native/part.step'])
    return profile, request


def test_package_contains_real_verified_exports_and_frozen_request(store, inputs):
    profile, request = inputs
    result = preflight(store, profile, request, now=NOW)
    assert result['state'] == 'request_ready'
    assert result['preflight_state'] == 'pass'
    assert result['manufacturability'] == 'unverified'
    assert result['supplier_acceptance'] == 'unknown'
    assert result['submission'] == 'not_sent'
    assert result['binding']['revision'] == store.load().revision
    assert len(result['binding']['artifacts']['native/part.step']) == 64
    package = create_request(store, profile, request, now=NOW)
    assert package['package_state'] == 'current' and package['quote_state'] == 'unknown'
    with zipfile.ZipFile(store.artifact(package['artifact'])) as archive:
        assert {'manifest.json', 'request.json', 'profile.json', 'preflight.json', 'source-capture.bin',
                'handoff/manifest.json', 'handoff/native/part.step', 'handoff/derivatives/drawing.pdf'} <= set(archive.namelist())
        assert archive.read('handoff/native/part.step').startswith(b'ISO-10303-21;')
        manifest = json.loads(archive.read('manifest.json'))
        for relative, sha in manifest['files'].items():
            assert hashlib.sha256(archive.read(relative)).hexdigest() == sha
        assert manifest['binding_sha256'] == package['binding_sha256']
    assert status(store, package['manifest'], now=NOW)['state'] == 'request_ready'


@pytest.mark.parametrize('field,value', [('quantity', 3), ('material', 'other'), ('provider', 'other'), ('request_id', 'different')])
def test_changed_request_invalidates_previous_package(store, inputs, field, value):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    changed = {**request, field: value}
    assert status(store, package['manifest'], profile, changed, now=NOW)['state'] == 'stale'


def test_changed_profile_revision_source_and_artifacts_invalidate(store, inputs):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    changed = deepcopy(profile)
    changed['record']['constraints'][0]['value'] += 1
    assert status(store, package['manifest'], changed, request, now=NOW)['state'] == 'stale'
    artifact = store.artifact(request['handoff_manifest']).parent / request['artifacts'][0]
    original = artifact.read_bytes()
    artifact.write_bytes(original + b'changed')
    assert status(store, package['manifest'], now=NOW)['state'] == 'stale'
    artifact.write_bytes(original)
    capture = store.artifact(profile['source']['capture'])
    original_source = capture.read_bytes()
    capture.write_bytes(b'changed source')
    assert status(store, package['manifest'], now=NOW)['state'] == 'stale'
    capture.write_bytes(original_source)
    project = store.load()
    project.name = 'Changed document, same geometry'
    store.apply(Command(expected_revision=project.revision, actor='human', project=project))
    assert status(store, package['manifest'], now=NOW)['state'] == 'stale'
    with pytest.raises(ValueError, match='Stale handoff'):
        preflight(store, profile, request, now=NOW)


def test_missing_unknown_incompatible_units_and_failed_rules_never_ready(store, inputs):
    profile, request = inputs
    missing = deepcopy(profile)
    missing['record']['constraints'] = []
    missing['record']['material'] = None
    result = preflight(store, missing, request, now=NOW)
    assert result['preflight_state'] == 'unknown' and result['state'] == 'manual_handoff'
    package = create_request(store, missing, request, now=NOW)
    assert package['state'] == 'manual_handoff'
    assert package['manufacturability'] == 'unverified'
    incompatible = deepcopy(profile)
    incompatible['record']['constraints'][0]['unit'] = 'inch'
    assert preflight(store, incompatible, request, now=NOW)['preflight_state'] == 'fail'
    over_quantity = {**request, 'quantity': 101}
    assert preflight(store, profile, over_quantity, now=NOW)['state'] == 'manual_handoff'
    assert preflight(store, profile, over_quantity, now=NOW)['preflight_state'] == 'fail'
    no_format = deepcopy(profile)
    no_format['record']['facts'] = {}
    assert preflight(store, no_format, request, now=NOW)['preflight_state'] == 'unknown'
    no_numeric_source = deepcopy(profile)
    no_numeric_source['record']['constraints'][0]['source_field'] = None
    assert preflight(store, no_numeric_source, request, now=NOW)['preflight_state'] == 'unknown'
    synthetic = deepcopy(profile)
    synthetic['source']['kind'] = 'synthetic_fixture'
    assert preflight(store, synthetic, request, now=NOW)['state'] == 'manual_handoff'


def test_tolerance_wall_and_unsupported_rules_are_explicit(store, inputs):
    profile, request = inputs
    constraints = profile['record']['constraints']
    constraints.append(dict(key='minimum_tolerance', value=.1, unit='mm', source_field='fixture.absolute_plus_minus_tolerance'))
    assert preflight(store, profile, request, now=NOW)['preflight_state'] == 'unknown'
    request['tolerance'] = dict(value=.2, unit='mm')
    assert preflight(store, profile, request, now=NOW)['preflight_state'] == 'pass'
    request['tolerance']['unit'] = 'micron'
    assert preflight(store, profile, request, now=NOW)['preflight_state'] == 'fail'
    request['tolerance']['unit'] = 'mm'
    request['minimum_wall'] = dict(value=1, unit='mm')
    assert preflight(store, profile, request, now=NOW)['preflight_state'] == 'unknown'
    constraints.append(dict(key='min_wall', value=2, unit='mm', source_field='fixture.min_wall'))
    assert preflight(store, profile, request, now=NOW)['preflight_state'] == 'fail'
    request['minimum_wall']['value'] = 3
    constraints.append(dict(key='tool_access', value=1, unit='mm', source_field='fixture.tool_access'))
    assert preflight(store, profile, request, now=NOW)['preflight_state'] == 'unknown'


def test_source_expiration_unknown_and_supplied_quote_lifecycle(store, inputs):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    response = dict(state='awaiting_response', request_id=request['request_id'], binding_sha256=package['binding_sha256'],
                    source=source(store, 'response.txt', b'Manually supplied response evidence.'), note='Manually submitted by fixture operator')
    waiting = record_response(store, package['manifest'], response, now=NOW)
    assert waiting['quote_state'] == 'awaiting_response'
    quote = {**response, 'state': 'quoted', 'amount': '42.50', 'currency': 'EUR'}
    quoted = record_response(store, package['manifest'], quote, now=NOW+timedelta(seconds=1))
    assert quoted['quote_state'] == 'quoted' and quoted['quote']['amount'] == '42.50'
    assert quoted['supplier_acceptance'] == 'unknown'
    rejected = {**response, 'state': 'rejected', 'note': 'Explicit supplied rejection'}
    assert record_response(store, package['manifest'], rejected, now=NOW+timedelta(seconds=2))['quote_state'] == 'rejected'
    unknown = {**response, 'state': 'unknown', 'note': 'Supplied ambiguous response'}
    assert record_response(store, package['manifest'], unknown, now=NOW+timedelta(seconds=3))['quote_state'] == 'unknown'
    assert status(store, package['manifest'], now=NOW+timedelta(days=2))['state'] == 'expired'
    expired_profile = deepcopy(profile)
    expired_profile['source']['expires_at'] = (NOW-timedelta(seconds=1)).isoformat()
    assert preflight(store, expired_profile, request, now=NOW)['preflight_state'] == 'unknown'
    other = {**response, 'request_id': 'other'}
    with pytest.raises(ValueError, match='different request'):
        record_response(store, package['manifest'], other, now=NOW)
    with pytest.raises(ValueError, match='stale or expired'):
        record_response(store, package['manifest'], response, now=NOW+timedelta(days=2))


@pytest.mark.parametrize('path', ['../outside.json', '/etc/passwd', 'handoffs/../outside/manifest.json', 'handoffs\\id\\manifest.json'])
def test_manifest_traversal_refused(store, inputs, path):
    profile, request = inputs
    request['handoff_manifest'] = path
    with pytest.raises(ValueError):
        preflight(store, profile, request, now=NOW)


def test_symlink_tampered_manifest_and_arbitrary_artifact_refused(store, inputs, tmp_path):
    profile, request = inputs
    capture = store.artifact(profile['source']['capture'])
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(capture.read_bytes())
    capture.unlink()
    capture.symlink_to(outside)
    with pytest.raises(ValueError, match='Symlink'):
        create_request(store, profile, request, now=NOW)
    capture.unlink()
    capture.write_bytes(outside.read_bytes())
    with pytest.raises(ValueError, match='bounded relative'):
        preflight(store, profile, {**request, 'artifacts': ['../../outside.step']}, now=NOW)
    with pytest.raises(ValueError, match='CAD/export'):
        preflight(store, profile, {**request, 'artifacts': ['preview.png']}, now=NOW)
    manifest = store.artifact(request['handoff_manifest'])
    data = json.loads(manifest.read_text())
    data['files']['../outside.step'] = '0'*64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='bounded relative'):
        preflight(store, profile, request, now=NOW)


def test_package_tampering_and_response_reuse_refused(store, inputs):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    response = dict(state='rejected', request_id=request['request_id'], binding_sha256='0'*64,
                    source=source(store, 'response.txt'), note='Fixture rejection')
    with pytest.raises(ValueError, match='different request'):
        record_response(store, package['manifest'], response, now=NOW)
    path = store.artifact(package['manifest']).parent / 'request.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='artifact changed'):
        status(store, package['manifest'], now=NOW)


def test_model_rejects_missing_inputs_nonfinite_units_and_unbound_quote():
    with pytest.raises(ValueError):
        SupplierRequest.model_validate(dict(quantity=True))
    with pytest.raises(ValueError):
        SupplierResponse.model_validate(dict(state='quoted', request_id='x', binding_sha256='0'*64,
            source=dict(url='https://example.invalid', capture='s.txt', sha256='0'*64,
                        captured_at=NOW.isoformat(), expires_at=(NOW+timedelta(days=1)).isoformat()), note='Missing price'))


def test_capture_is_bounded_hash_addressed_and_never_vendor_verified(store):
    content = 'Explicit user-supplied source bytes.'
    attached = capture_source(store, content)
    assert attached['capture'] == 'supplier-sources/' + hashlib.sha256(content.encode()).hexdigest() + '.bin'
    assert attached['provenance'] == 'user_supplied_bytes' and attached['vendor_verified'] is False
    assert store.artifact(attached['capture']).read_text() == content
    assert capture_source(store, content) == attached
    with pytest.raises(ValueError, match='1 byte to 2 MiB'):
        capture_source(store, '')
    with pytest.raises(ValueError, match='1 byte to 2 MiB'):
        capture_source(store, b'x' * (2 * 1024 * 1024 + 1))
    store.artifact(attached['capture']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='immutable supplier source changed'):
        capture_source(store, content)


def test_dimensions_come_from_immutable_export_report_and_selection_invalidates(store, inputs):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    changed = {**request, 'artifacts': ['native/part.stl']}
    assert status(store, package['manifest'], profile, changed, now=NOW)['state'] == 'stale'
    assert preflight(store, profile, changed, now=NOW)['preflight_state'] == 'fail'
    build_path = store.cache / 'builds' / package['binding']['geometry_key'] / 'build.json'
    build_data = json.loads(build_path.read_text())
    build_data['dimensions_mm'] = [0.001, 0.001, 0.001]
    build_path.write_text(json.dumps(build_data))
    limited = deepcopy(profile)
    limited['record']['constraints'][0]['value'] = 1
    evaluated = preflight(store, limited, request, now=NOW)
    assert evaluated['preflight_state'] == 'fail'
    assert evaluated['binding']['measurement_evidence']
    with pytest.raises(ValueError):
        preflight(store, profile, {**request, 'quantity': True}, now=NOW)
    with pytest.raises(ValueError):
        preflight(store, profile, {**request, 'dimensions': {'x': 1}}, now=NOW)


def test_quote_expiry_preserves_explicit_response_provenance(store, inputs):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    response_source = source(store, 'short-response.txt')
    response_source['expires_at'] = (NOW + timedelta(hours=1)).isoformat()
    response = dict(state='quoted', request_id=request['request_id'], binding_sha256=package['binding_sha256'],
                    source=response_source, note='Supplied short-lived quote', amount='12.00', currency='USD')
    record_response(store, package['manifest'], response, now=NOW)
    expired = status(store, package['manifest'], now=NOW + timedelta(hours=2))
    assert expired['state'] == 'expired' and expired['quote_state'] == 'expired'
    assert expired['quote']['source']['sha256'] == response_source['sha256']
    assert expired['quote_evidence_state'] == 'unknown'


def test_source_and_package_symlinks_are_refused_without_outside_reads(store, inputs, tmp_path):
    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    handoff_folder = store.artifact(request['handoff_manifest']).parent
    artifact = handoff_folder / request['artifacts'][0]
    outside = tmp_path / 'outside.step'
    outside.write_bytes(artifact.read_bytes())
    original = artifact.read_bytes()
    artifact.unlink()
    artifact.symlink_to(outside)
    with pytest.raises(ValueError, match='Symlink'):
        preflight(store, profile, request, now=NOW)
    artifact.unlink()
    artifact.write_bytes(original)
    package_folder = store.artifact(package['manifest']).parent
    archive = package_folder / 'request.zip'
    outside_archive = tmp_path / 'outside.zip'
    outside_archive.write_bytes(archive.read_bytes())
    archive.unlink()
    archive.symlink_to(outside_archive)
    with pytest.raises(ValueError, match='Symlink'):
        status(store, package['manifest'], now=NOW)
    profile['source']['capture'] = 'history/00000001.json'
    with pytest.raises(ValueError, match='bounded supplier-sources'):
        preflight(store, profile, request, now=NOW)


def _external_project_edit(store, *, bump_revision=True):
    """Simulate an uncooperative external writer in this disposable test project.

    Ordinary Store.apply writers respect the held lock and cannot interleave.
    Bypassing it here makes the final snapshot guard deterministic and testable.
    """
    project = store._load()
    project.name = 'Concurrent external document edit'
    if bump_revision:
        project.revision += 1
    if bump_revision:
        store._save(project, 'human', 'Exercise external writer race', 'replace')
    else:
        # Same-revision tampering is deliberately outside the guarded journal.
        (store.root / 'o3d.json').write_text(project.model_dump_json())


@pytest.mark.parametrize('operation', ['package', 'response'])
def test_expected_revision_is_checked_after_lock_acquisition(store, inputs, monkeypatch, operation):
    from contextlib import contextmanager
    from o3d.store import Conflict, Store

    profile, request = inputs
    expected = store.load().revision
    package = create_request(store, profile, request, now=NOW)
    response = dict(state='rejected', request_id=request['request_id'], binding_sha256=package['binding_sha256'],
                    source=source(store, 'race-response.txt'), note='Explicit supplied rejection')
    previous_packages = set((store.cache / 'supplier-requests').iterdir())
    original_lock = store.lock
    interleaved = False

    @contextmanager
    def intervening_edit():
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            # This is a real Store command committing between the caller's
            # expected-revision check and the helper's own lock/snapshot selection.
            other = Store(store.root)
            changed = other.load()
            changed.name = 'Edit after caller revision guard'
            other.apply(Command(expected_revision=expected, actor='human', project=changed))
        with original_lock():
            yield

    monkeypatch.setattr(store, 'lock', intervening_edit)
    with pytest.raises(Conflict, match=f'expected {expected}, current {expected+1}'):
        if operation == 'package':
            create_request(store, profile, request, now=NOW, expected_revision=expected)
        else:
            record_response(store, package['manifest'], response, now=NOW, expected_revision=expected)
    assert interleaved and store.load().revision == expected + 1
    assert set((store.cache / 'supplier-requests').iterdir()) == previous_packages
    assert not list((store.cache / 'supplier-events').glob('*.json'))


def test_package_refuses_external_edit_after_snapshot_and_removes_partial_package(store, inputs, monkeypatch):
    import o3d.supplier_handoff as module
    from o3d.store import Conflict

    profile, request = inputs
    expected = store.load().revision
    original_copy = module.shutil.copyfile
    interleaved = False

    def copy_then_edit(source_path, destination, *args, **kwargs):
        nonlocal interleaved
        result = original_copy(source_path, destination, *args, **kwargs)
        if not interleaved and str(destination).endswith('native/part.step'):
            interleaved = True
            _external_project_edit(store)
        return result

    monkeypatch.setattr(module.shutil, 'copyfile', copy_then_edit)
    with pytest.raises(Conflict, match='Revision conflict'):
        create_request(store, profile, request, now=NOW, expected_revision=expected)
    assert interleaved
    assert not list((store.cache / 'supplier-requests').glob('request-*'))


@pytest.mark.parametrize('stage', ['source', 'event'])
def test_response_refuses_external_document_edit_and_leaves_no_event(store, inputs, monkeypatch, stage):
    import o3d.supplier_handoff as module
    from o3d.store import Conflict

    profile, request = inputs
    package = create_request(store, profile, request, now=NOW, expected_revision=store.load().revision)
    expected = package['binding']['revision']
    response = dict(state='quoted', request_id=request['request_id'], binding_sha256=package['binding_sha256'],
                    source=source(store, 'race-response.txt'), note='Explicit supplied quote', amount='12.50', currency='EUR')
    interleaved = False
    original_source, original_write = module._source, module.write_json

    def source_then_edit(selected_store, evidence, when):
        nonlocal interleaved
        result = original_source(selected_store, evidence, when)
        if stage == 'source' and not interleaved and evidence.capture.endswith('race-response.txt'):
            interleaved = True
            _external_project_edit(store, bump_revision=False)
        return result

    def write_then_edit(path, value):
        nonlocal interleaved
        result = original_write(path, value)
        if stage == 'event' and not interleaved and path.parent.name == 'supplier-events':
            interleaved = True
            _external_project_edit(store, bump_revision=False)
        return result

    monkeypatch.setattr(module, '_source', source_then_edit)
    monkeypatch.setattr(module, 'write_json', write_then_edit)
    with pytest.raises(Conflict, match='Project document changed'):
        record_response(store, package['manifest'], response, now=NOW, expected_revision=expected)
    assert interleaved
    assert not list((store.cache / 'supplier-events').glob('*.json'))
    assert status(store, package['manifest'], now=NOW)['state'] == 'stale'


def test_canonical_body_selection_reopens_with_dependencies_and_native_edit(tmp_path):
    import build123d as bd
    from o3d.model import Project, Study
    from o3d.native_document import execute_native, query_native
    from o3d.projects import restore_archive
    from o3d.store import Store

    selected = Store(tmp_path / 'native-source')
    selected.init(Project(named_studies={'review': Study(name='Saved review')}))
    asset = tmp_path / 'source.step'
    bd.export_step(bd.Box(4, 5, 6), asset)
    sha = hashlib.sha256(asset.read_bytes()).hexdigest()
    retained = selected.root / 'assets' / (sha + '.step')
    retained.parent.mkdir()
    retained.write_bytes(asset.read_bytes())
    retained.with_name(retained.name + '.LICENSE.txt').write_text('Disposable fixture attribution')
    project = selected.load()
    project.domains['interop'] = {'schema_version': 1, 'assets': [{'id': 'fixture', 'file': 'assets/' + retained.name, 'sha256': sha}]}
    selected.apply(Command(expected_revision=1, actor='human', project=project))
    execute_native(selected, 2, 'human', 'file.insert_step', {'asset_id': 'fixture'})
    project = selected.load()
    build(selected, project)
    handoff = create_handoff(selected, 0, expected_revision=project.revision)
    profile = dict(provider='fixture', record=dict(external_id='fixture', name='Supplied fixture', record_type='capability',
        process='fixture', material='fixture', source_url='https://example.invalid/fixture', facts={'supported_formats': ['step']},
        constraints=[dict(key=key, value=value, unit=unit, source_field='fixture.'+key) for key, value, unit in
            [('max_x', 200, 'mm'), ('max_y', 200, 'mm'), ('max_z', 200, 'mm'), ('min_quantity', 1, 'count'), ('max_quantity', 100, 'count')]]),
        source=source(selected))
    request = dict(request_id='canonical-body', provider='fixture', process='fixture', material='fixture', quantity=1,
                   handoff_manifest=handoff['manifest'], artifacts=['native/geometry/body-000/part.step'])
    package = create_request(selected, profile, request, now=NOW, expected_revision=project.revision)
    assert package['state'] == 'request_ready' and package['supplier_acceptance'] == 'unknown'
    with zipfile.ZipFile(selected.artifact(package['artifact'])) as archive:
        assert archive.read('handoff/assets/' + retained.name) == retained.read_bytes()
        assert archive.read('handoff/assets/' + retained.name + '.LICENSE.txt') == b'Disposable fixture attribution'
        restore_archive(archive.read('handoff/native/project.oxygen.zip'), tmp_path / 'restored-supplier')
    reopened = Store(tmp_path / 'restored-supplier')
    assert reopened.load() == project
    assert query_native(reopened.load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(120)
    # The source asset, native document, named studies and timeline survive a cold reopen.
    assert reopened.load().named_studies == project.named_studies
    assert (reopened.root / 'assets' / retained.name).read_bytes() == retained.read_bytes()
    execute_native(selected, project.revision, 'human', 'solid.box', {'length': 1, 'width': 1, 'height': 1, 'corner': [20, 0, 0]})
    assert status(selected, package['manifest'], now=NOW)['state'] == 'stale'
    build(selected, selected.load())
    with pytest.raises(ValueError, match='Export the complete canonical CAD release'):
        create_handoff(selected, 0, expected_revision=selected.load().revision)


@pytest.mark.parametrize('role,kind', [('viewer', 'human'), ('reader', 'human'), ('editor', 'agent')])
def test_supplier_mutations_revalidate_current_project_roles(store, inputs, role, kind):
    from o3d.identity import Identity, principal_context

    profile, request = inputs
    package = create_request(store, profile, request, now=NOW)
    response = dict(state='rejected', request_id=request['request_id'], binding_sha256=package['binding_sha256'],
                    source=source(store, 'role-response.txt'), note='Explicit response')
    identity = Identity(store)
    admin = identity.authenticate(identity.enable('Owner')['token'])
    principal = identity.authenticate(identity.issue(admin, 'Role fixture', role, kind)['token'])
    context = principal_context.set(principal)
    try:
        if role in {'viewer', 'reader'}:
            with pytest.raises(PermissionError, match='cannot edit'):
                capture_source(store, 'Denied attachment')
            with pytest.raises(PermissionError, match='cannot edit'):
                record_response(store, package['manifest'], response, now=NOW)
        else:
            assert capture_source(store, 'Agent supplied bytes')['vendor_verified'] is False
            assert record_response(store, package['manifest'], response, now=NOW)['quote_state'] == 'rejected'
        if role == 'viewer':
            with pytest.raises(PermissionError, match='cannot export'):
                create_request(store, profile, request, now=NOW)
            with pytest.raises(PermissionError, match='cannot export'):
                create_handoff(store, 0)
        else:
            assert create_request(store, profile, request, now=NOW)['package_state'] == 'current'
            assert create_handoff(store, 0)['state'] == 'current'
    finally:
        principal_context.reset(context)


@pytest.mark.parametrize('operation', ['capture', 'package', 'response', 'prepare'])
def test_revoked_principal_cannot_publish_supplier_evidence(store, inputs, monkeypatch, operation):
    import o3d.handoff as handoff_module
    import o3d.supplier_handoff as module
    from o3d.identity import Identity, principal_context
    from types import SimpleNamespace

    profile, request = inputs
    existing = create_request(store, profile, request, now=NOW)
    original_packages = set((store.cache / 'supplier-requests').iterdir())
    pointer = (store.cache / 'handoffs/latest.json').read_bytes()
    response = dict(state='quoted', request_id=request['request_id'], binding_sha256=existing['binding_sha256'],
                    source=source(store, 'revocation-response.txt'), note='Explicit quote', amount='12.00', currency='EUR')
    identity = Identity(store)
    admin = identity.authenticate(identity.enable('Owner')['token'])
    principal = identity.authenticate(identity.issue(admin, 'Revoked editor', 'editor')['token'])
    revoked = False

    def revoke():
        nonlocal revoked
        if not revoked:
            revoked = True
            identity.revoke(admin, principal.id)

    original_copy, original_source, original_helper = module.shutil.copyfile, module._source, handoff_module.helper

    def copy_then_revoke(source_path, target, *args, **kwargs):
        result = original_copy(source_path, target, *args, **kwargs)
        if operation == 'package' and str(target).endswith('native/part.step'):
            revoke()
        return result

    def source_then_revoke(selected_store, evidence, when):
        result = original_source(selected_store, evidence, when)
        if operation == 'response' and evidence.capture.endswith('revocation-response.txt'):
            revoke()
        return result

    def helper_then_revoke(folder, name):
        helper = original_helper(folder, name)
        if operation != 'prepare' or folder != 'oxygen-cad-drawing':
            return helper
        def draw(*args, **kwargs):
            result = helper.drawing(*args, **kwargs)
            revoke()
            return result
        return SimpleNamespace(drawing=draw)

    monkeypatch.setattr(module.shutil, 'copyfile', copy_then_revoke)
    monkeypatch.setattr(module, '_source', source_then_revoke)
    monkeypatch.setattr(handoff_module, 'helper', helper_then_revoke)
    if operation == 'capture':
        revoke()
    context = principal_context.set(principal)
    try:
        with pytest.raises(PermissionError, match='revoked'):
            if operation == 'capture':
                capture_source(store, 'Revoked evidence bytes')
            elif operation == 'package':
                create_request(store, profile, request, now=NOW, expected_revision=store.load().revision)
            elif operation == 'response':
                record_response(store, existing['manifest'], response, now=NOW, expected_revision=store.load().revision)
            else:
                create_handoff(store, 0, expected_revision=store.load().revision)
    finally:
        principal_context.reset(context)
    assert revoked
    assert set((store.cache / 'supplier-requests').iterdir()) == original_packages
    assert not list((store.cache / 'supplier-events').glob('*.json'))
    assert (store.cache / 'handoffs/latest.json').read_bytes() == pointer


def test_package_checks_copied_bytes_against_frozen_handoff_hashes(store, inputs, monkeypatch):
    import o3d.supplier_handoff as module

    profile, request = inputs
    original = module.shutil.copyfile
    def corrupt_copy(source_path, target, *args, **kwargs):
        result = original(source_path, target, *args, **kwargs)
        if str(target).endswith('native/part.step'):
            with open(target, 'ab') as output:
                output.write(b'Corrupted during copy')
        return result
    monkeypatch.setattr(module.shutil, 'copyfile', corrupt_copy)
    with pytest.raises(ValueError, match='Copied handoff artifact hash changed'):
        create_request(store, profile, request, now=NOW)
    assert not list((store.cache / 'supplier-requests').glob('request-*'))


def test_package_publication_rechecks_auth_and_removes_pending_directory(store, inputs, monkeypatch):
    import o3d.supplier_handoff as module
    from o3d.identity import Identity, principal_context

    profile, request = inputs
    identity = Identity(store)
    admin = identity.authenticate(identity.enable('Owner')['token'])
    principal = identity.authenticate(identity.issue(admin, 'Export worker', 'editor')['token'])
    original_status = module.status
    revoked = False
    def revoke_after_final_status(*args, **kwargs):
        nonlocal revoked
        result = original_status(*args, **kwargs)
        if not revoked and '/.pending-' in args[1]:
            revoked = True
            identity.revoke(admin, principal.id)
        return result
    monkeypatch.setattr(module, 'status', revoke_after_final_status)
    context = principal_context.set(principal)
    try:
        with pytest.raises(PermissionError, match='revoked'):
            create_request(store, profile, request, now=NOW)
    finally:
        principal_context.reset(context)
    assert revoked
    assert not list((store.cache / 'supplier-requests').iterdir())
