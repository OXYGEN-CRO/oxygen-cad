import json
import zipfile

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from o3d.artifacts import export_bundle
from o3d.cli import app
from o3d.model import Command, Project, Requirement, Study, geometry_key, study_key
from o3d.solver import simulate
from o3d.store import Conflict
from o3d.studies import edit_study


def test_legacy_project_and_scoped_study_schema(beam):
    document=beam.model_dump(exclude={"named_studies","active_study_id"})
    loaded=Project.model_validate(document)
    assert loaded.active_study_id=="default" and loaded.study_config()==loaded.study
    for change in [{"active_study_id":"absent"},{"named_studies":{"default":Study().model_dump()}}]:
        with pytest.raises(ValidationError):Project.model_validate(document|change)
    document["requirements"][0]["study_id"]="absent"
    with pytest.raises(ValidationError,match="missing study"):Project.model_validate(document)


def test_clone_and_select_preserve_inputs_and_revision_checks(store):
    original=store.load()
    updated=edit_study(store,"clone","alternate",1,name="Alternative load")
    assert updated.study==original.study
    assert updated.named_studies["alternate"].forces==original.study.forces
    assert not updated.named_studies["alternate"].assumptions_confirmed
    assert updated.active_study_id=="alternate"
    assert geometry_key(updated)==geometry_key(original)
    with pytest.raises(Conflict,match="Revision conflict"):
        edit_study(store,"select","default",1)
    edit_study(store,"select","default",2)
    assert store.load().named_studies["alternate"].name=="Alternative load"


def test_real_independent_study_results_comparison_stale_scope_and_release(store):
    p=store.load()
    p.requirements=[Requirement(id="default_limit",kind="max_displacement",value=.2,study_id="default")]
    p=store.apply(Command(expected_revision=1,actor="human",project=p))
    first=simulate(store,p)
    p=edit_study(store,"clone","double_load",p.revision,actor="human",name="Double load")
    p.named_studies["double_load"].forces[0].vector_n=(0,0,-20)
    p.named_studies["double_load"].assumptions_confirmed=True
    p=store.apply(Command(expected_revision=p.revision,actor="human",project=p))
    second=simulate(store,p)
    assert second["study_id"]=="double_load"
    assert second["max_displacement_mm"]==pytest.approx(2*first["max_displacement_mm"],rel=.002)
    assert second["state"]=="passed"  # The default-only displacement limit does not apply.
    states=store.state()["studies"]
    assert states["default"]["state"]==states["double_load"]["state"]=="passed"
    compare=store.compare_studies(["default","double_load"])
    row=next(v for v in compare["quantities"] if v["metric"]=="max_displacement_mm")
    assert row["unit"]=="mm" and row["values"]["double_load"]>row["values"]["default"]
    old=study_key(p,"default")
    p.named_studies["double_load"].name="Renamed only"
    assert study_key(p,"default")==old
    current_key=study_key(p)
    p.named_studies["double_load"].name="Another label"
    assert study_key(p)==current_key
    p=store.apply(Command(expected_revision=p.revision,actor="human",project=p))
    release=export_bundle(store,p)
    with zipfile.ZipFile(store.artifact(release["artifact"])) as archive:
        report=json.loads(archive.read("report.json"))
        assert report["study_packages"]=={"default":"analyses/default","double_load":"analysis"}
        assert json.loads(archive.read("analysis/result.json"))["study_id"]=="double_load"
        assert archive.read("analyses/default/result.json")
    p.named_studies["double_load"].forces[0].vector_n=(0,0,-25)
    p=store.apply(Command(expected_revision=p.revision,actor="human",project=p))
    states=store.state()["studies"]
    assert states["double_load"]["state"]=="stale" and states["default"]["state"]=="passed"
    release=export_bundle(store,p)
    with zipfile.ZipFile(store.artifact(release["artifact"])) as archive:
        assert not any(n.startswith("analysis/") for n in archive.namelist())
        assert json.loads(archive.read("report.json"))["studies"]["double_load"]["state"]=="stale"


def test_study_cli_roundtrip_and_requirement_protected_deletion(store):
    runner=CliRunner()
    base=["--project",str(store.root)]
    output=runner.invoke(app,base+["study","clone","variant","--name","My alternative","--expected-revision","1"])
    assert output.exit_code==0,output.output
    assert json.loads(output.output)["active_study_id"]=="variant"
    p=store.load();p.requirements.append(Requirement(id="variant_limit",kind="max_displacement",value=.3,study_id="variant"))
    p=store.apply(Command(expected_revision=p.revision,actor="human",project=p))
    with pytest.raises(ValueError,match="requirements"):
        edit_study(store,"delete","variant",p.revision,actor="human")
    output=runner.invoke(app,base+["studies","--json"])
    assert output.exit_code==0
    assert set(json.loads(output.output)["saved_studies"])=={"default","variant"}
