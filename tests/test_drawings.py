"""Measured paper-space/geometry/export round trips using disposable projects."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from o3d.domains.drawings import Drawings, execute, read_step_pmi, validate
from o3d.model import Project, geometry_key
from o3d.store import Store


def attach(project, data):
    # The domain envelope is integrated centrally by the coordinator.
    object.__setattr__(project, "domains", {"drawings": validate(data)})
    return project


@pytest.fixture
def drawing(tmp_path):
    p=Project.model_validate({"name":"Measured shaft plate","geometry_engine":"occt","features":[
        {"id":"profile","type":"profile","plane":"XY","width":60,"height":40},
        {"id":"plate","type":"extrude","profile":"profile","distance":10},
        {"id":"bores","type":"hole","axis":"Z","center":[-10,0,0],"diameter":10,"rows":1,"columns":2,"spacing":[20,0]}]})
    store=Store(tmp_path/"project");store.init(p)
    data={"schema_version":1,"documents":[{"id":"print","title":"Measured plate","number":"PL-001","source_revision":1,"source_geometry_key":geometry_key(p),
        "sheets":[{"id":"sheet1","views":[{"id":"top","orientation":"top","position":[100,90]}],"annotations":[
            {"id":"dia","view":"top","kind":"diameter","references":[{"kind":"hole","target":"bores"}],"position":[-40,-30],"precision":3,"tolerance":{"kind":"fit","fit":"H7"}},
            {"id":"pitch","view":"top","kind":"linear","references":[{"kind":"hole","target":"bores"},{"kind":"hole","target":"bores","column":1}],"position":[0,25]},
            {"id":"datumA","view":"top","kind":"datum","datum":"A","references":[{"kind":"face","target":"part:zmax"}],"position":[30,25]},
            {"id":"position","view":"top","kind":"gdt","characteristic":"position","tolerance_mm":.02,"datums":["A"],"material_condition":"MMC","references":[{"kind":"hole","target":"bores"}],"position":[35,-25]}
        ],"tables":[{"id":"holes","kind":"holes","view":"top","position":[10,135],"references":[{"kind":"hole","target":"bores"},{"kind":"hole","target":"bores","column":1}]}]},
        {"id":"sheet2","size":"Letter","views":[{"id":"front","position":[70,70]},{"id":"top2","kind":"projected","parent":"front","orientation":"top","position":[75,130]}]}],
        "revisions":[{"code":"A","date":"2026-10-24","description":"First issue","author":"test"}]}]}
    attach(p,data)
    return store,p,data


def test_drawing_vectors_measured_roundtrip(drawing):
    import ezdxf
    store,p,data=drawing
    result=execute(store,p,"publish",{"document":"print"})["documents"][0]
    page=result["pages"][0]
    measurements={m["id"]:m for m in page["measurements"]}
    assert measurements["dia"]["value_mm"]==10
    assert measurements["dia"]["limits_mm"]==pytest.approx([10,10.015])
    assert measurements["pitch"]["value_mm"]==20
    svg_path=store.artifact(result["artifacts"][0]["svg"])
    svg=ET.parse(svg_path).getroot()
    assert svg.attrib["width"]=="297mm"
    assert any("⌀10.000 H7" in (node.text or "") for node in svg.iter())
    dxf=ezdxf.readfile(store.artifact(result["artifacts"][0]["dxf"]))
    assert dxf.units==4
    lines=list(dxf.modelspace().query("LINE[layer=='VISIBLE']"))
    lengths=[(line.dxf.end-line.dxf.start).magnitude for line in lines]
    assert any(abs(length-60)<1e-7 for length in lengths)
    assert any("⌀10.000 H7" in t.dxf.text for t in dxf.modelspace().query("TEXT"))
    assert "HIDDEN" in dxf.linetypes
    pdf=store.artifact(result["pdf"]).read_bytes()
    assert pdf.startswith(b"%PDF-1.7") and pdf.endswith(b"%%EOF\n")
    assert b"/Count 2 " in pdf and b"/ToUnicode" in pdf and b"/FontFile2" in pdf
    with zipfile.ZipFile(store.artifact(result["artifact"])) as archive:
        reopened=json.loads(archive.read("document.json"))
        assert execute(store,p,"reopen",{"data":reopened})["data"]["documents"][0]["source_revision"]==1
        assert "sheet2.dxf" in archive.namelist()
    top=result["pages"][1]["views"][1]
    assert top["position"]==[70,130] # first-angle, parent alignment retained


def test_change_detach_and_explicit_repair(drawing):
    store,p,data=drawing
    p.features[2].diameter=12
    p.revision=2
    assert execute(store,p,"inspect",{})["documents"][0]["state"]=="stale"
    with pytest.raises(ValueError,match="stale"):
        execute(store,p,"publish",{})
    updated=execute(store,p,"refresh",{})["data"]
    attach(p,updated)
    result=execute(store,p,"preview",{})["documents"][0]
    assert result["pages"][0]["measurements"][0]["value_mm"]==12
    p.features[2].suppressed=True
    p.revision=3
    state=execute(store,p,"inspect",{})["documents"][0]
    assert state["state"]=="unresolved" and state["issues"]
    with pytest.raises(ValueError):execute(store,p,"publish",{})


def test_section_detail_and_hidden_geometry(drawing):
    store,p,data=drawing
    doc=data["documents"][0]
    doc["sheets"]=[{"id":"section","size":"A3","views":[
        {"id":"front","orientation":"front","position":[80,90]},
        {"id":"cut","kind":"section","parent":"front","position":[180,90],"section_origin":[0,0,0],"section_normal":[0,1,0],"label":"A-A"},
        {"id":"enlarged","kind":"detail","parent":"cut","position":[300,90],"scale":2,"detail_center":[-10,5],"detail_radius":12}
    ]}]
    attach(p,data)
    page=execute(store,p,"preview",{})["documents"][0]["pages"][0]
    assert any(path["style"]=="hidden" for path in page["paths"])
    assert any(path["style"]=="hatch" for path in page["paths"])
    assert page["circles"][0]["radius"]==24
    # Parent relationships and detail scale are persisted in canonical drawing intent.
    assert [v["parent"] for v in page["views"]]==[None,"front","cut"]
    # At y=0 the two 10 mm bores remove two strips: hatch must not fill a bore.
    from o3d.domains.drawing_worker import hatch, section_shape, load_geometry, ORIENTATIONS
    shape,_,_=load_geometry(store,p)
    cut,boundary=section_shape(shape,(0,0,0),(0,1,0))
    assert cut.volume==pytest.approx(shape.volume/2,rel=1e-7)
    for path in hatch(boundary,ORIENTATIONS["front"]):
        a,b=path["points"]
        mx,my=(a[0]+b[0])/2,(a[1]+b[1])/2
        assert not (0 < my < 10 and (abs(mx-10)<5-1e-6 or abs(mx+10)<5-1e-6))


def test_pmi_semantics_and_inventory(drawing):
    store,p,data=drawing
    data["pmi"]=[{"id":"boreSize","kind":"size","reference":{"kind":"hole","target":"bores"},"source_geometry_key":geometry_key(p),"source_revision":1,
                  "plane_origin":[0,0,10],"tolerance":{"kind":"deviation","upper_mm":.015,"lower_mm":0}}]
    attach(p,data)
    result=execute(store,p,"export_mbd",{})
    record=result["annotations"][0]
    assert record["inspection"]=={"nominal_mm":10,"lower_mm":10,"upper_mm":10.015}
    assert json.loads(store.artifact(result["artifact"]).read_text())["annotations"][0]["plane_origin"]==[0,0,10]
    inventory=read_step_pmi("ISO-10303-21;DATA;#1=DATUM('A','',#2);#2=SHAPE_ASPECT('face','',#3,.T.);#3=PRODUCT_DEFINITION_SHAPE('','',#4);#4=PRODUCT('part','','',());#8=POSITION_TOLERANCE('p','',#9,#2);#9=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(0.02),#10);#10=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));ENDSEC;END-ISO-10303-21;")
    assert inventory["root_entities"]==["#1","#8"]
    assert set(inventory["entities"])=={"#1","#2","#3","#4","#8","#9","#10"}
    assert inventory["semantic_mapping"]=="requires_explicit_attachment"


def test_invalid_associations_and_standard_fields():
    with pytest.raises(ValueError,match="parent"):
        validate({"documents":[{"id":"x","source_geometry_key":"k","source_revision":1,"sheets":[{"id":"s","views":[{"id":"v","kind":"detail"}]}]}]})
    with pytest.raises(ValueError,match="Form tolerances"):
        from o3d.domains.drawings import Annotation
        Annotation(id="x",view="v",kind="gdt",characteristic="flatness",tolerance_mm=.02,datums=["A"],references=[{"kind":"point"}])
