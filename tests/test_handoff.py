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
