"""Actual native geometry, decoded image pixels and animation publication checks."""
import base64
import copy
import io
import json
from threading import Event
import zipfile

import numpy as np
from PIL import Image
import pytest

from o3d.domains.visualization import (Appearance, Camera, Keyframe, Scene, Section,
    Storyboard, Visualization, capture_scene, execute, sample, validate)
from o3d.domains.visualization_render import render_image, validate_targets
from o3d.model import Project, geometry_key, study_key
from o3d.solver import Cancelled
from o3d.store import Store


@pytest.fixture
def visual_store(tmp_path):
    store = Store(tmp_path/"project")
    store.init(Project(name="Render fixture", features=[
        {"id":"profile","type":"profile","width":20,"height":16},
        {"id":"box","type":"extrude","profile":"profile","distance":12}],
        material={"name":"Engineering fixture","density_g_cm3":2.7,"youngs_modulus_mpa":70000,
            "poisson_ratio":0.33,"source":"Explicit test inputs"}))
    return store


def scene_fixture():
    return Scene(width=128,height=96,camera=Camera(target=[0,0,6],distance=70))


def render_fixture(store, scene=None, assets=None):
    return render_image(store,capture_scene(store,store.load()),scene or scene_fixture(),assets or {})


def mask(image):
    return np.asarray(image)[:,:,3]>0


def test_native_raster_geometry_pixels_appearance_and_section_do_not_edit_physics(visual_store):
    store=visual_store
    original=store.load()
    geometry=capture_scene(store,original)
    assert geometry["bodies"]
    scene=scene_fixture()
    scene.transparent=True
    image,evidence=render_image(store,geometry,scene,{})
    assert image.size==(128,96)
    assert evidence["source_triangles"]>=12
    assert 100<mask(image).sum()<128*96
    # Front and rear geometry occlude; object pixels are colored, background alpha stays zero.
    assert np.unique(np.asarray(image)[mask(image),:3],axis=0).shape[0]>3
    red=scene.model_copy(deep=True)
    red.appearances["*"]=Appearance(color="#ee1111",roughness=0.8)
    red_image,_=render_image(store,geometry,red,{})
    assert np.asarray(red_image)[mask(red_image),0].mean()>np.asarray(red_image)[mask(red_image),2].mean()*2
    cut=scene.model_copy(deep=True)
    cut.sections=[Section(normal=[1,0,0],offset_mm=0)]
    cut_image,cut_evidence=render_image(store,geometry,cut,{})
    assert 0<mask(cut_image).sum()<mask(image).sum()*0.85
    assert "uncapped" in " ".join(cut_evidence["limitations"])
    target=geometry["bodies"][0]["id"]
    hidden=scene.model_copy(deep=True)
    hidden.hidden=[target]
    hidden_image,_=render_image(store,geometry,hidden,{})
    assert mask(hidden_image).sum()==0
    assert store.load()==original
    assert geometry_key(store.load())==geometry_key(original)
    assert study_key(store.load())==study_key(original)


def test_perspective_point_and_spot_pixels_and_nonaccumulating_explode(visual_store):
    store=visual_store
    geometry=capture_scene(store,store.load())
    scene=scene_fixture();scene.transparent=True
    base,_=render_image(store,geometry,scene,{})
    perspective=scene.model_copy(deep=True);perspective.camera.fov=0.7
    changed,_=render_image(store,geometry,perspective,{})
    assert np.count_nonzero(mask(base)!=mask(changed))>100
    from o3d.domains.visualization import Light
    point=scene.model_copy(deep=True);point.ambient=0
    point.lights=[Light(kind="point",vector=[40,-40,50],reference_distance_mm=50)]
    illuminated,_=render_image(store,geometry,point,{})
    point.lights[0].kind="spot";point.lights[0].target=[100,100,100];point.lights[0].cone_degrees=5
    away,_=render_image(store,geometry,point,{})
    assert np.asarray(illuminated)[:,:,:3][mask(illuminated)].mean()>np.asarray(away)[:,:,:3][mask(away)].mean()+10
    target=geometry["bodies"][0]["id"]
    board=Storyboard(duration=2,keyframes=[Keyframe(time=0),Keyframe(time=2,transforms={target:[20,0,0]})])
    middle,_=sample(scene,board,1)
    assert middle.transforms[target]==[10,0,0]
    again,_=sample(scene,board,1)
    assert again==middle
    moved,_=render_image(store,geometry,middle,{})
    assert np.abs(np.argwhere(mask(moved)).mean(0)-np.argwhere(mask(base)).mean(0)).max()>8
    assert not scene.transforms


def test_project_owned_decal_hash_rotation_and_opacity(visual_store):
    store=visual_store
    image=Image.new("RGBA",(8,8),(250,0,0,255))
    for x in range(4):
        for y in range(8):image.putpixel((x,y),(0,0,250,255))
    raw=io.BytesIO();image.save(raw,format="PNG")
    asset=execute(store,store.load(),"import_image",{"base64":base64.b64encode(raw.getvalue()).decode()})["asset"]
    assert store.root.joinpath(asset["file"]).exists()
    scene=scene_fixture();scene.camera.pitch=1.5707963267948966;scene.style="shaded";scene.ambient=1;scene.lights=[]
    data=Visualization(scenes=[scene],assets={asset["sha256"]:asset})
    from o3d.domains.visualization import Mapping
    scene.appearances["*"]=Appearance(color="#00ff00",texture=Mapping(asset=asset["sha256"],size_mm=[20,16]))
    first,_=render_fixture(store,scene,data.assets)
    scene.appearances["*"].texture.rotation=3.141592653589793
    rotated,_=render_fixture(store,scene,data.assets)
    assert np.abs(np.asarray(first).astype(int)-np.asarray(rotated).astype(int)).sum()>100_000
    scene.appearances["*"].texture.opacity=0
    invisible,_=render_fixture(store,scene,data.assets)
    assert np.abs(np.asarray(invisible).astype(int)-np.asarray(first).astype(int)).sum()>100_000
    store.root.joinpath(asset["file"]).write_bytes(b"changed")
    with pytest.raises(ValueError,match="hash mismatch"):
        render_fixture(store,scene,data.assets)


def test_render_and_real_gif_png_sequence_revision_provenance_reopen(visual_store):
    store=visual_store
    project=store.load()
    geometry=capture_scene(store,project)
    target=geometry["bodies"][0]["id"]
    scene=scene_fixture();scene.width=96;scene.height=64;scene.transparent=True
    data=Visualization(scenes=[scene],storyboards=[Storyboard(name="Instructions",duration=1,fps=10,
        keyframes=[Keyframe(time=0,callout="Assembled"),Keyframe(time=1,transforms={target:[25,0,0]},callout="Remove cover")])])
    payload={"data":data.model_dump(mode="json"),"storyboard":"Instructions"}
    result=execute(store,project,"render",payload)
    assert result["revision"]==project.revision
    with Image.open(store.artifact(result["artifacts"]["image"])) as decoded:
        assert decoded.size==(96,64) and decoded.mode=="RGBA"
    movie=execute(store,project,"publish",payload|{"format":"gif"})
    with Image.open(store.artifact(movie["artifacts"]["movie"])) as decoded:
        assert decoded.n_frames==10
        durations=[];first=np.asarray(decoded.convert("RGB"))
        for i in range(decoded.n_frames):
            decoded.seek(i);durations.append(decoded.info["duration"])
        assert sum(durations)==1000
        assert np.count_nonzero(first!=np.asarray(decoded.convert("RGB")))>200
    sequence=execute(store,project,"publish",payload|{"format":"png_sequence"})
    with zipfile.ZipFile(store.artifact(sequence["artifacts"]["sequence"])) as archive:
        assert len([n for n in archive.namelist() if n.endswith(".png")])==10
        assert json.loads(archive.read("timing.json"))["duration"]==1
        with Image.open(io.BytesIO(archive.read("frame-0009.png"))) as decoded:
            assert decoded.size==(96,64)
    reopened=Store(store.root)
    gallery=execute(reopened,reopened.load(),"gallery",{})["results"]
    assert len(gallery)==3 and all(not r["stale"] for r in gallery)
    assert reopened.load()==project


def test_validation_resource_guards_and_real_cancel(visual_store):
    with pytest.raises(ValueError):validate({"scenes":[{"width":2048,"height":2048}]})
    with pytest.raises(ValueError):validate({"scenes":[{"sections":[{"normal":[0,0,0]}]}]})
    with pytest.raises(ValueError):validate({"storyboards":[{"keyframes":[{"time":1}]}]})
    with pytest.raises(ValueError):validate({"assets":{"x":{"file":"../../escape.png"}}})
    geometry=capture_scene(visual_store,visual_store.load())
    scene=scene_fixture();scene.hidden=["missing-stable-target"]
    with pytest.raises(ValueError,match="Unresolved"):
        validate_targets(geometry,scene)
    event=Event();event.set()
    with pytest.raises(Cancelled):
        execute(visual_store,visual_store.load(),"render",{},event)
    class CancelDuringRaster:
        checks=0
        def is_set(self):
            self.checks+=1
            return self.checks>=2
    with pytest.raises(Cancelled):
        render_image(visual_store,geometry,scene_fixture(),{},CancelDuringRaster())
    assert execute(visual_store,visual_store.load(),"gallery",{})["results"]==[]
