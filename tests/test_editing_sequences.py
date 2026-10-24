"""The agreed 10-part / 20 alternating edit acceptance gate."""
import math
import build123d as bd
import pytest
from o3d.geometry import build, compile_shape, resolve_face
from o3d.model import Command, Extrude, Hole, Parameter, Profile, Project
from o3d.store import Store


@pytest.mark.parametrize("variant",range(10))
def test_twenty_alternating_agent_human_edits(variant,tmp_path):
    project=Project(name=f"Mount fixture {variant}",parameters={
        "width":Parameter(value=30+variant),"thickness":Parameter(value=3+variant/5),
        "bolt":Parameter(value=2.4,locked=True)},features=[
        Profile(id="outline",width="$width",height=20+variant),
        Extrude(id="body",profile="outline",distance="$thickness"),
        Hole(id="mount_hole",diameter="$bolt")])
    store=Store(tmp_path/f"part-{variant}");store.init(project)
    for edit in range(20):
        current=store.load();next_project=current.model_copy(deep=True)
        next_project.parameters["width"].value=30+variant+(edit+1)/10
        updated=store.apply(Command(expected_revision=current.revision,actor="agent" if edit%2==0 else "human",project=next_project,summary=f"Alternating edit {edit+1}"))
        result=build(store,updated)
        width=updated.parameters["width"].value;thickness=updated.parameters["thickness"].value
        expected=(width*(20+variant)-math.pi*1.2**2)*thickness
        assert result["volume_mm3"]==pytest.approx(expected,rel=1e-8)
        assert result["dimensions_mm"]==pytest.approx([width,20+variant,thickness],abs=1e-6)
        reimported=bd.import_step(store.artifact(result["artifacts"]["step"]))
        assert reimported.volume==pytest.approx(expected,rel=1e-6)
        assert resolve_face(reimported,"part:xmax").center().X==pytest.approx(width/2)
        assert store.load().parameters["bolt"].locked
    assert store.load().revision==21 and len(store.history())==21
