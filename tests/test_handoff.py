"""Real derivative packages, invalidation and export integrity across revisions."""
import json
from pathlib import Path
import zipfile

import build123d as bd
import pytest

from o3d.geometry import build
from o3d.handoff import create, status
from o3d.model import Command


def test_handoff_is_reopenable_and_old_derivatives_become_stale(store):
    assert status(store)['state']=='not_run'
    with pytest.raises(ValueError,match='Build current'): create(store,0)
    build(store,store.load())
    first=create(store,0)
    assert first['state']=='current'
    assert first['findings']['analysis_state']=='not_run'
    assert 'physical fit and strength' in first['findings']['unverified']
    with zipfile.ZipFile(store.artifact(first['artifact'])) as package:
        assert {'o3d.json','native/part.step','derivatives/drawing.pdf','derivatives/section.dxf','manifest.json','findings.json'} <= set(package.namelist())
        assert json.loads(package.read('manifest.json'))['revision']==1
    old=store.load();old.features[1].distance=84
    store.apply(Command(expected_revision=1,actor='agent',project=old,summary='Exercise handoff invalidation'))
    assert 'project_changed' in status(store)['reasons']
    assert status(store,first['manifest'])['state']=='stale'
    with pytest.raises(ValueError,match='Build current'):create(store,0)
    build(store,store.load());second=create(store,0)
    assert second['state']=='current' and second['revision']==2
    assert second['artifact']!=first['artifact']
    assert status(store,first['manifest'])['state']=='stale'
    # A copied project reopens with the same actual geometry, not just a screenshot.
    from o3d.model import Project
    from o3d.geometry import compile_shape
    with zipfile.ZipFile(store.artifact(second['artifact'])) as package:
        saved=Project.model_validate_json(package.read('o3d.json'))
        assert compile_shape(saved).bounding_box().size.X==pytest.approx(84)
        reopened=store.root.parent/'reopened-handoff'
        package.extractall(reopened)
    from o3d.store import Store
    portable=status(Store(reopened),'manifest.json')
    assert portable['state']=='current'
    assert portable['archive_state']=='not_applicable_to_unpacked_files'


def test_handoff_detects_modified_missing_derivatives_and_archive(store):
    build(store,store.load());result=create(store,0)
    folder=store.artifact(result['manifest']).parent
    (folder/'derivatives/drawing.pdf').write_bytes(b'changed')
    assert 'artifact_changed:derivatives/drawing.pdf' in status(store)['reasons']
    (folder/'handoff.zip').unlink()
    assert 'archive_missing' in status(store)['reasons']


def test_handoff_keeps_reference_assets_and_license_metadata(store,tmp_path):
    step=tmp_path/'component.step';bd.export_step(bd.Box(2,2,2),step)
    project=store.import_step(step.read_bytes(),'component.step',1)
    project.references[0].placement.translation=(500,0,0)
    store.apply(Command(expected_revision=2,actor='agent',project=project))
    asset=store.root/project.references[0].file
    notice=asset.with_name(asset.name+'.LICENSE.txt');notice.write_text('Fixture attribution and permission')
    build(store,store.load());result=create(store,0)
    with zipfile.ZipFile(store.artifact(result['artifact'])) as package:
        assert project.references[0].file in package.namelist()
        assert project.references[0].file+'.LICENSE.txt' in package.namelist()
    notice.write_text('Updated source notice')
    assert 'reference_inputs_changed' in status(store)['reasons']


def test_handoff_records_failure_without_replacing_latest_valid_manifest(store):
    build(store,store.load());good=create(store,0)
    with pytest.raises(ValueError,match='intersect'):create(store,1000)
    assert status(store)['manifest']==good['manifest']
    assert list((store.cache/'handoffs').glob('*/failure.json'))


def test_handoff_refuses_project_change_during_generation(store,monkeypatch):
    from types import SimpleNamespace
    import o3d.handoff as module
    build(store,store.load());good=create(store,0)
    original=module.helper
    def load(folder,name):
        value=original(folder,name)
        if folder!='oxygen-cad-drawing':return value
        def draw(*args,**kwargs):
            result=value.drawing(*args,**kwargs)
            project=store.load();project.name='Concurrent user edit'
            store.apply(Command(expected_revision=project.revision,actor='human',project=project))
            return result
        return SimpleNamespace(drawing=draw)
    monkeypatch.setattr(module,'helper',load)
    with pytest.raises(ValueError,match='Project changed'):create(store,0)
    pointer=json.loads((store.cache/'handoffs/latest.json').read_text())
    assert pointer['manifest']==good['manifest']


def test_handoff_expected_revision_is_checked_at_selected_snapshot(store, monkeypatch):
    from contextlib import contextmanager
    from o3d.store import Conflict, Store

    build(store, store.load())
    expected = store.load().revision
    original_lock = store.lock
    interleaved = False

    @contextmanager
    def edit_before_snapshot():
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            other = Store(store.root)
            project = other.load()
            project.name = 'Changed after outer route guard'
            other.apply(Command(expected_revision=expected, actor='human', project=project))
        with original_lock():
            yield

    monkeypatch.setattr(store, 'lock', edit_before_snapshot)
    with pytest.raises(Conflict, match=f'expected {expected}, current {expected+1}'):
        create(store, 0, expected_revision=expected)
    assert interleaved
    assert not list((store.cache / 'handoffs').glob('*/manifest.json'))


def test_native_handoff_preserves_complete_canonical_project_and_cold_reopen(tmp_path):
    from o3d.model import Project, Study
    from o3d.native_document import execute_native, query_native
    from o3d.projects import restore_archive
    from o3d.store import Store

    store = Store(tmp_path / 'native')
    store.init(Project(named_studies={'review': Study(name='Independent review')}))
    execute_native(store, 1, 'human', 'solid.box', {'length': 20, 'width': 10, 'height': 5, 'body_name': 'Plate'})
    project = store.load()
    feature_id = project.native_document['features'][0]['id']
    execute_native(store, project.revision, 'human', 'timeline.edit', {'feature': feature_id, 'set': {'length': '30'}})
    project = store.load()
    build(store, project)
    result = create(store, 1, expected_revision=project.revision)
    assert result['state'] == 'current'
    assert result['geometry_scope']['mode'] == 'single_exact_solid'
    assert len(result['geometry_scope']['body_ids']) == 1
    with zipfile.ZipFile(store.artifact(result['artifact'])) as archive:
        assert {'native/project.oxygen.zip', 'native/part.solidcraft', 'native/scene.json',
                'native/geometry/body-000/part.step', 'native/release-manifest.json'} <= set(archive.namelist())
        portable = archive.read('native/project.oxygen.zip')
        native = json.loads(archive.read('native/o3d.json'))
        assert native['native_document'] == project.native_document
        assert native['named_studies'] == project.model_dump(mode='json')['named_studies']
        measured = bd.import_step(store.artifact(result['manifest']).parent / 'native/geometry/body-000/part.step')
        assert measured.volume == pytest.approx(1500)
    restored = tmp_path / 'cold-reopen'
    restore_archive(portable, restored)
    reopened = Store(restored)
    assert reopened.load() == project
    assert reopened.load().native_document['features'][0]['id'] == feature_id
    assert query_native(reopened.load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(1500)
    assert len(reopened.history()) == len(store.history())
    execute_native(reopened, project.revision, 'human', 'timeline.edit', {'feature': feature_id, 'set': {'length': '40'}})
    assert query_native(reopened.load(), 'inspect.measure')['total']['volume_mm3'] == pytest.approx(2000)


@pytest.mark.parametrize('kind', ['multibody', 'surface', 'mesh'])
def test_handoff_refuses_unsupported_scene_without_flattening(tmp_path, kind):
    import hashlib
    import trimesh
    from o3d.model import Project
    from o3d.native import NativeSession
    from o3d.store import Store

    root = tmp_path / kind
    domains = {}
    with NativeSession() as session:
        if kind == 'multibody':
            session.execute('solid.box', {'length': 20, 'width': 10, 'height': 5, 'body_name': 'First'})
            session.execute('solid.box', {'length': 8, 'width': 6, 'height': 4, 'corner': [40, 0, 0], 'body_name': 'Second'})
        elif kind == 'surface':
            session.execute('sketch.create', {'plane': 'XY'})
            session.execute('sketch.rectangle.two_point', {'p0': [0, 0], 'p1': [20, 7]})
            session.execute('sketch.finish')
            session.execute('surface.patch', {'body_name': 'Sheet'})
        else:
            raw = trimesh.creation.box(extents=[4, 5, 6]).export(file_type='stl')
            sha = hashlib.sha256(raw).hexdigest()
            source = root / 'assets' / (sha + '.stl')
            source.parent.mkdir(parents=True)
            source.write_bytes(raw)
            domains['interop'] = {'schema_version': 1, 'assets': [{'id': 'mesh', 'file': 'assets/' + source.name, 'sha256': sha}]}
            session.execute('file.insert_mesh', {'path': str(source)})
        snapshot = session.execute('oxygen.document.get')
    domains['modeling'] = {'schema_version': 1, 'native_context': snapshot['context']}
    project = Project(native_document=snapshot['document'], domains=domains)
    store = Store(root)
    store.init(project)
    built = build(store, project)
    with pytest.raises(ValueError, match='Export the complete canonical CAD release'):
        create(store, 0, expected_revision=project.revision)
    assert not (store.cache / 'handoffs/latest.json').exists()
    # Refusal leaves the complete truthful canonical release available for review.
    with zipfile.ZipFile(store.cache / 'exports/1/release.zip') as archive:
        report = json.loads(archive.read('report.json'))
        assert report['geometry']['body_count'] == len(built['scene']['bodies'])
        assert json.loads(archive.read('o3d.json'))['native_document'] == project.native_document
        assert 'project.oxygen.zip' in archive.namelist()
        if kind == 'multibody':
            assert {'geometry/body-000/part.step', 'geometry/body-001/part.step'} <= set(archive.namelist())


def test_handoff_copies_canonical_manifest_files_only(store, monkeypatch):
    import o3d.handoff as module

    build(store, store.load())
    original_export = module.export_bundle
    def export_with_stale_loose_file(selected_store, project):
        result = original_export(selected_store, project)
        (selected_store.artifact(result['artifact']).parent / 'stale-private-part.step').write_bytes(b'Unlisted old output')
        return result
    monkeypatch.setattr(module, 'export_bundle', export_with_stale_loose_file)
    result = create(store, 0)
    with zipfile.ZipFile(store.artifact(result['artifact'])) as archive:
        assert not any('stale-private-part' in name for name in archive.namelist())
        assert 'native/scene.json' in archive.namelist()


@pytest.mark.parametrize('attack', ['traversal', 'symlink'])
def test_handoff_rejects_unsafe_canonical_archive_members(store, attack):
    import hashlib
    import stat
    from o3d.artifacts import export_bundle
    from o3d.handoff import _release

    build(store, store.load())
    release = export_bundle(store, store.load())
    with zipfile.ZipFile(store.artifact(release['artifact'])) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    name = '../escaped.step' if attack == 'traversal' else 'linked.step'
    raw = b'Outside path'
    manifest = json.loads(content['release-manifest.json'])
    manifest['files'][name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    content['release-manifest.json'] = json.dumps(manifest).encode()
    bad = store.cache / 'unsafe-release.zip'
    with zipfile.ZipFile(bad, 'w') as archive:
        for existing, value in content.items():
            archive.writestr(existing, value)
        info = zipfile.ZipInfo(name)
        if attack == 'symlink':
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, raw)
    with pytest.raises(ValueError, match='Unsafe project path|regular files'):
        _release(store, {'artifact': 'unsafe-release.zip'})
    assert not (store.root / 'escaped.step').exists()
