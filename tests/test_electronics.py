"""Measured electrical authoring/tool tests. External tests execute real binaries."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from o3d.domains import electronics as eda
from o3d.domains.eda.schema import Document, Via, Rule, Track, PinRef, Net, Sheet, Port, Hole, Pair, Waiver, Model3D
from o3d.domains.eda.checks import erc,drc,flattened,mcad,net_map,pad_world,harness_report
from o3d.domains.eda.kicad import export_board,import_board,parse_sexpr
from o3d.domains.eda.workers import assembly,simulate,kicad,spice_deck
from o3d.domains.eda.schema import digest


@pytest.fixture
def design(): return Document.model_validate(eda.example())
@pytest.fixture
def workspace(tmp_path):return SimpleNamespace(cache=tmp_path/'.o3d')

def project(doc):return SimpleNamespace(domains={'electronics':doc.model_dump(mode='json')},revision=17)
def codes(report):return {f['code'] for f in report['findings']}


def test_versioned_roundtrip_and_one_pin_one_net(design):
    assert Document.model_validate_json(design.model_dump_json())==design
    bad=design.model_dump();bad['nets'][1]['members'].append({'component_id':'v1','pin_id':'p1'})
    with pytest.raises(ValidationError,match='assigned more than once'):Document.model_validate(bad)
    bad=design.model_dump();bad['schema_version']=2
    with pytest.raises(ValidationError):Document.model_validate(bad)


def test_erc_real_connectivity_not_graphical_contact(design):
    assert erc(design)['state']=='passed'
    data=design.model_dump();data['library'][0]['pins'][1]['electrical']='input';data['nets'][2]['members']=[]
    data['wires']=[{'id':'visual','sheet_id':'root','net_id':'vin','points':[(1,1),(2,2)]}]
    doc=Document.model_validate(data)
    assert {'erc.unconnected','erc.graphical_endpoint'}<=codes(erc(doc))


def test_erc_output_conflict_power_and_waiver_invalidation(design):
    data=design.model_dump();data['library'][1]['pins'][0]['electrical']='output';data['library'][2]['pins'][0]['electrical']='power_in';doc=Document.model_validate(data)
    report=erc(doc);assert {'erc.output_conflict','erc.unpowered'}<=codes(report)
    for f in report['findings']:
        doc.waivers.append(Waiver(finding_id=f['id'],reason='Reviewed fixture exception',author='Engineer',source_hash=digest(doc),revision=17))
    assert erc(doc)['state']=='passed'
    doc.components[1].value='2k'
    assert erc(doc)['state']=='failed'


def test_hierarchy_repeat_modules_and_bus_ids(design,workspace):
    d=design.model_dump();d['sheets'].append({'id':'channel','name':'Sensor','parent_id':'root','ports':[{'id':'port','name':'power','net_id':'channel_vcc','parent_net_id':'vin'}]})
    c=copy.deepcopy(d['components'][1]);c.update(id='sensor',sheet_id='channel',reference='R2');d['components'].append(c)
    d['nets'] += [{'id':'channel_vcc','sheet_id':'channel','name':'VCC','members':[{'component_id':'sensor','pin_id':'p1'}]},{'id':'local','sheet_id':'channel','name':'LOCAL','members':[{'component_id':'sensor','pin_id':'p2'}]}]
    d['buses']=[{'id':'bus','sheet_id':'channel','name':'interface','net_ids':['channel_vcc','local']}]
    doc=Document.model_validate(d)
    response=eda.execute(workspace,project(doc),'instantiate',{'sheet_id':'channel','suffix':'B','ports':{'power':'vin'}})
    new=Document.model_validate(response['data']);aliases,pins=net_map(new)
    assert pins['sensor/p1']==pins['sensor_B/p1']==aliases['vin']
    assert pins['sensor/p2']!=pins['sensor_B/p2']
    assert next(b for b in new.buses if b.id=='bus_B').net_ids==['channel_vcc_B','local_B']
    assert len(flattened(new))==5
    assert Document.model_validate_json(new.model_dump_json())==new


def test_cycles_bad_ports_and_unknown_library_rejected(design):
    bad=design.model_dump();bad['sheets'][0]['parent_id']='root'
    with pytest.raises(ValidationError,match='root sheet|cycle'):Document.model_validate(bad)
    bad=design.model_dump();bad['components'][0]['library_version']='missing'
    with pytest.raises(ValidationError,match='pinned library'):Document.model_validate(bad)


def test_agent_locks_and_source_conflicts(design,workspace):
    design.board.placements[0].locked=True;new=design.model_copy(deep=True);new.board.placements[0].at=(3,3)
    with pytest.raises(ValueError,match='locked'):eda.validate_transition(design.model_dump(),new.model_dump(),'agent')
    eda.validate_transition(design.model_dump(),new.model_dump(),'human')
    with pytest.raises(ValueError,match='source conflict'):eda.execute(workspace,project(design),'example',{'expected_source_hash':'stale'})
    original=digest(design);new=eda.execute(workspace,project(design),'edit',{'changes':[{'collection':'components','id':'r1','action':'upsert','value':design.components[1].model_dump()|{'value':'2k'}}]})
    assert new['previous_source_hash']==original and new['source_hash']!=original
    assert Document.model_validate(new['data']).board.placements[0].locked


def test_pin_pad_mapping_bottom_mirror_and_missing_models(design):
    data=mcad(design);assert data['state']=='incomplete';assert all(p['model_state']=='missing' for p in data['occurrences'])
    c=next(o for o in data['occurrences'] if o['component_id']=='c1')
    assert c['pads'][0]['pin_id']=='p1';assert c['pads'][0]['board_mm']==pytest.approx([28,11.5,0])
    assert c['pads'][1]['board_mm']==pytest.approx([28,8.5,0])
    design.board.transform=(10,20,30,0,0,90)
    point=mcad(design)['occurrences'][2]['pads'][0]['world_mm'];assert point==pytest.approx([-1.5,48,30])


def test_soic_package_dimensions_pin_one_and_library_substitution(design,workspace):
    p=eda.package({'id':'SOIC16','version':'1','name':'Test datasheet dimensions','pin_count':16,'pitch':1.27,'body_size':[3.9,9.9],'row_spacing':5.4,'pad_size':[1.55,0.6],'source':'Fixture dimensions, original test input','licence':'MIT'})
    assert len(p.pins)==len(p.pads)==16;assert p.pads[0].at==pytest.approx([-2.7,-4.445]);assert p.pads[7].at==pytest.approx([-2.7,4.445]);assert p.pads[8].at==pytest.approx([2.7,4.445]);assert p.pads[-1].at==pytest.approx([2.7,-4.445]);assert p.body_size==(3.9,9.9)
    original=design.library[1];alternate=original.model_copy(deep=True);alternate.version='2';alternate.pads[0].pin_id='p2'
    payload={'library_id':original.id,'from_version':'1','part':alternate.model_dump()}
    assert not eda.library_diff(design,payload)['compatible']
    with pytest.raises(ValueError,match='Incompatible'):eda.execute(workspace,project(design),'substitute',payload)
    alternate.pads[0].pin_id='p1';alternate.pads[0].size=(1.3,1.5);payload['part']=alternate.model_dump()
    out=eda.execute(workspace,project(design),'substitute',payload);new=Document.model_validate(out['data'])
    assert new.components[1].library_version=='2';assert new.board.placements==design.board.placements
    design.library[1].voltage_rating=10;alternate.voltage_rating=5;payload['part']=alternate.model_dump()
    assert not eda.library_diff(design,payload)['compatible']


def test_stack_and_via_validation(design):
    assert sum(l.thickness for l in design.board.stackup)==pytest.approx(1.6)
    for via in [dict(id='bad',net_id='gnd',at=(5,5),diameter=.6,drill=.3,start_layer='In1.Cu',end_layer='In2.Cu',technology='through'),dict(id='bad',net_id='gnd',at=(5,5),diameter=.6,drill=.3,start_layer='F.Cu',end_layer='missing')]:
        bad=design.model_dump();bad['board']['vias']=[via]
        with pytest.raises(ValidationError):Document.model_validate(bad)
    bad=design.model_dump();bad['board']['stackup'][1]['kind']='copper'
    with pytest.raises(ValidationError):Document.model_validate(bad)


def test_local_drc_faults_scopes_and_real_copper_connectivity(design):
    assert 'drc.unrouted' in codes(drc(design))
    design.board.tracks=[Track(id='connected',net_id='vin',layer='F.Cu',width=.25,points=[(6,10),(16.5,10)])]
    assert not any(f['code']=='drc.unrouted' and 'vin' in f['objects'] for f in drc(design)['findings'])
    design.rules=[Rule(id='priority',priority=100,net_ids=['vin'],layers=['F.Cu'],width=.4)]
    assert 'drc.width' in codes(drc(design))
    design.board.tracks += [Track(id='short',net_id='gnd',layer='F.Cu',width=.25,points=[(10,8),(10,12)]),Track(id='edge',net_id='vout',layer='F.Cu',width=.25,points=[(0,0),(4,0)]),Track(id='cutout',net_id='vout',layer='F.Cu',width=.25,points=[(19,22),(25,22)])]
    design.board.vias=[Via(id='small',net_id='gnd',at=(32,10),diameter=.35,drill=.2)]
    assert {'drc.short','drc.board_edge','drc.drill','drc.annular_ring'}<=codes(drc(design))


def test_quick_route_preserves_existing_manual_route_and_reports_remaining(design,workspace):
    out=eda.execute(workspace,project(design),'route',{'id':'auto','net_id':'vin','start':{'component_id':'v1','pin_id':'p1'},'end':{'component_id':'r1','pin_id':'p1'},'width':.25,'layer':'F.Cu'})
    new=Document.model_validate(out['data']);assert new.board.tracks[0].points[0]==(6,10);assert new.board.tracks[0].points[-1]==(16.5,10);assert new.board.placements==design.board.placements
    report=drc(new);assert not any(f['code']=='drc.unrouted' and 'vin' in f['objects'] for f in report['findings']);assert report['state']=='failed'
    new.board.tracks[0].locked=True
    with pytest.raises(ValueError,match='already exists'):eda.execute(workspace,project(new),'route',{'id':'auto','net_id':'vin','start':{'component_id':'v1','pin_id':'p1'},'end':{'component_id':'r1','pin_id':'p1'}})


def test_route_walks_obstacle_and_cancel_is_not_success(design,workspace):
    design.board.tracks=[Track(id='obstacle',net_id='gnd',layer='F.Cu',points=[(11,8),(11,12)],width=.5,locked=True)]
    payload={'id':'walk','net_id':'vin','start':{'component_id':'v1','pin_id':'p1'},'end':{'component_id':'r1','pin_id':'p1'},'width':.25}
    with pytest.raises(RuntimeError,match='cancelled'):eda.execute(workspace,project(design),'route',payload,cancel=lambda:True)
    out=eda.execute(workspace,project(design),'route',payload);new=Document.model_validate(out['data']);assert len(new.board.tracks[-1].points)>2
    assert new.board.tracks[0]==design.board.tracks[0]
    assert not any(f['code'] in ('drc.short','drc.clearance') and any('walk' in o for o in f['objects']) for f in drc(new)['findings'])


def test_pair_length_width_gap_constraints(design):
    design.board.tracks=[Track(id='p',net_id='vin',layer='In1.Cu',width=.2,points=[(5,15),(15,15)]),Track(id='n',net_id='vout',layer='In1.Cu',width=.3,points=[(5,15.3),(13,15.3)])]
    design.differential_pairs=[Pair(id='pair',positive='vin',negative='vout',width=.2,gap=.2,max_skew=.5)]
    assert {'drc.pair_width','drc.pair_gap','drc.pair_skew'}<=codes(drc(design))


def test_assembly_variants_rotation_origin_and_quantities(design):
    design.board.origin=(2,3);base=assembly(design);variant=assembly(design,'no_cap')
    assert sum(r['quantity'] for r in base['bom'])==len(base['positions'])==3
    assert sum(r['quantity'] for r in variant['bom'])==len(variant['positions'])==2
    assert all(r['component_id']!='c1' for r in variant['positions'])
    c=next(r for r in base['positions'] if r['component_id']=='c1');assert (c['x_mm'],c['y_mm'],c['rotation_deg'],c['side'])==(26,7,90,'bottom')
    assert all(r['missing_mpn'] for r in base['bom'])


@pytest.mark.parametrize('slot',[(2,0),(0,2)])
def test_kicad_roundtrip_holes_cutouts_pad_identity_stack_and_mirrors(design,slot):
    design.board.holes[-1].slot=slot
    design.board.placements[0].rotation=37
    design.library[0].pads[0].rotation=21
    text=export_board(design);assert parse_sexpr(text)[0]=='kicad_pcb'
    new,loss=import_board(text)
    assert [h.model_dump() for h in new.board.holes]==[h.model_dump() for h in design.board.holes]
    assert new.board.outline==design.board.outline and new.board.cutouts==design.board.cutouts
    assert [(l.kind,l.thickness,l.material,l.epsilon_r) for l in new.board.stackup]==[(l.kind,l.thickness,l.material,l.epsilon_r) for l in design.board.stackup]
    a,b=mcad(design),mcad(new)
    for old,fresh in zip(a['occurrences'],b['occurrences']):
        assert old['id']==fresh['id'] and old['component_id']==fresh['component_id']
        for x,y in zip(old['pads'],fresh['pads']):assert x['board_mm']==pytest.approx(y['board_mm']);assert x['pin_id']==y['pin_id'];assert x['pad_id']==y['pad_id']
    assert {tuple(sorted((m.component_id,m.pin_id) for m in n.members)) for n in new.nets}=={tuple(sorted((m.component_id,m.pin_id) for m in n.members)) for n in design.nets}
    assert loss['requires_review'];assert any(f['kind']=='design_rules' for f in loss['findings'])


def test_import_loss_preview_immutable_original_and_editable_json(design,workspace):
    text=export_board(design);out=eda.execute(workspace,project(design),'import',{'format':'kicad_board','text':text})
    assert out['changed'] is False and out['state']=='review_required'
    original=workspace.cache/out['candidate']['provenance'][-1]['original_artifact'];assert original.read_text()==text
    assert eda.execute(workspace,project(design),'import',{'format':'oxygen','text':design.model_dump_json()})['data']==design.model_dump(mode='json')
    for fmt in ['altium','eagle','kicad_schematic']:
        with pytest.raises(ValueError,match='Unsupported'):eda.execute(workspace,project(design),'import',{'format':fmt,'text':'anything'})


def test_missing_model_bad_terminal_order_and_model_injection(design):
    design.library[1].spice=None
    with pytest.raises(ValueError,match='missing simulation model'):spice_deck(design,{'probes':['vout']})
    d=Document.model_validate(eda.example());model=d.library[0].spice;model.kind='D';model.model_name='testdiode';model.model_text='.model testdiode D';model.source='Original fixture model';model.sha256=hashlib.sha256(model.model_text.encode()).hexdigest();model.terminal_roles={'p1':'K','p2':'A'}
    with pytest.raises(ValueError,match='terminal roles'):spice_deck(d,{'probes':['vout']})
    model.terminal_roles={'p1':'A','p2':'K'};model.model_text='.control\nshell echo forbidden\n.endc';model.sha256=hashlib.sha256(model.model_text.encode()).hexdigest()
    with pytest.raises(ValueError,match='directives'):spice_deck(d,{'probes':['vout']})


def test_unavailable_tools_and_pre_cancelled_workers_never_pass(design,workspace,monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda _:None)
    with pytest.raises(RuntimeError,match='unavailable'):simulate(workspace,design,{'probes':['vout']})
    with pytest.raises(RuntimeError,match='unavailable'):kicad(workspace,design,'fabrication',{})


@pytest.mark.skipif(not shutil.which('ngspice'),reason='Real ngspice executable required')
def test_real_ngspice_op_dc_ac_and_transient_against_analytic_rc(design,workspace):
    op=simulate(workspace,design,{'analysis':'op','probes':['vout']});assert op['state']=='solved';assert op['rows'][0][-1]==pytest.approx(5,rel=1e-6)
    dc=simulate(workspace,design,{'analysis':'dc','source':'v1','start':0,'stop':5,'step':1,'probes':['vout']});assert len(dc['rows'])==6;assert [r[-1] for r in dc['rows']]==pytest.approx([0,1,2,3,4,5],abs=1e-6)
    fc=1/(2*math.pi*1000*1e-6)
    ac=simulate(workspace,design,{'analysis':'ac','ac_sources':{'v1':'1'},'start':fc,'stop':fc*10,'points':10,'probes':['vout']});assert math.hypot(*ac['rows'][0][1:3])==pytest.approx(1/math.sqrt(2),rel=1e-5)
    tran=simulate(workspace,design,{'analysis':'tran','step':1e-5,'stop':.01,'pulses':{'v1':['0','5','0','1n','1n','1','2']},'probes':['vout']});at_tau=min(tran['rows'],key=lambda r:abs(r[0]-.001));assert at_tau[1]==pytest.approx(5*(1-math.exp(-at_tau[0]/.001)),abs=.01)
    old=op['source_hash'];design.components[2].value='2u';new=simulate(workspace,design,{'analysis':'op','probes':['vout']});assert new['source_hash']!=old
    with pytest.raises(RuntimeError,match='cancelled'):simulate(workspace,design,{'analysis':'op','probes':['vout']},cancel=lambda:True)


@pytest.mark.skipif(not shutil.which('kicad-cli'),reason='Real KiCad executable required')
def test_real_kicad_independent_unconnected_checks_and_fabrication(design,workspace):
    check=kicad(workspace,design,'kicad_drc',{});assert check['state']=='failed';assert len(check['report']['unconnected_items'])==3
    out=kicad(workspace,design,'fabrication',{});assert out['state']=='exported';assert out['manifest']['review_state']=='blocked_by_findings'
    names={a['name'] for a in out['artifacts']};assert {'board-F_Cu.gbr','board-In1_Cu.gbr','board-In2_Cu.gbr','board-B_Cu.gbr','board-F_Mask.gbr','board-B_Mask.gbr','board-F_Paste.gbr','board-B_Paste.gbr','board-Edge_Cuts.gbr','board-PTH.drl','board-NPTH.drl'}<=names


@pytest.mark.skipif(not shutil.which('kicad-cli'),reason='Real KiCad executable required')
def test_manufacturing_independent_gerbonara_parses_cutouts_drills_widths_layers_and_pad_rotation(design,workspace):
    gerbonara=pytest.importorskip('gerbonara',reason='Optional independent Gerbonara 1.5 parser required')
    design.board.tracks=[Track(id='known',net_id='vin',layer='In1.Cu',width=.37,points=[(7,16),(17,16)])]
    out=kicad(workspace,design,'fabrication',{});files={a['name']:workspace.cache/a['path'] for a in out['artifacts']}
    edge=gerbonara.GerberFile.open(files['board-Edge_Cuts.gbr']);assert len(edge.objects)==8
    # Gerber axes invert board Y; outline strokes add half 0.05 mm at each edge.
    bounds=edge.bounding_box();assert bounds[0]==pytest.approx((-.025,-30.025));assert bounds[1]==pytest.approx((40.025,.025))
    outline_vertices={(round(o.x1,4),round(o.y1,4)) for o in edge.objects};assert {(20,-20),(24,-20),(24,-24),(20,-24)}<=outline_vertices
    pth=gerbonara.ExcellonFile.open(files['board-PTH.drl']);npth=gerbonara.ExcellonFile.open(files['board-NPTH.drl']);assert len(pth.objects)==1 and len(npth.objects)==2
    slot=pth.objects[0];assert slot.tool.diameter==pytest.approx(1);assert math.dist((slot.x1,slot.y1),(slot.x2,slot.y2))==pytest.approx(2)
    assert sorted((p.x,p.y) for p in npth.objects)==[(3,-3),(37,-27)]
    inner=gerbonara.GerberFile.open(files['board-In1_Cu.gbr']);line=next(o for o in inner.objects if hasattr(o,'x1'));assert line.aperture.diameter==pytest.approx(.37)
    top=gerbonara.GerberFile.open(files['board-F_Cu.gbr']);bottom=gerbonara.GerberFile.open(files['board-B_Cu.gbr'])
    flashes=[o for o in bottom.objects if hasattr(o,'x') and o.attrs.get('.P',('',))[0]=='C1'];assert len(flashes)==2
    assert sorted((o.x,o.y) for o in flashes)==[(28,-11.5),(28,-8.5)]
    # A 1.2 x 1.4 pad rotated 90deg has 1.4 x 1.2 manufacturing extents.
    bounds=flashes[0].bounding_box();assert bounds[1][0]-bounds[0][0]==pytest.approx(1.4);assert bounds[1][1]-bounds[0][1]==pytest.approx(1.2)
    assert len([o for o in top.objects if hasattr(o,'x') and o.attrs.get('.P',('',))[0] in ('V1','R1')])==4


def test_spice_does_not_short_distinct_scoped_grounds(design):
    design.nets.append(Net(id='isolated_ground',sheet_id='root',name='GND'))
    with pytest.raises(ValueError,match='unambiguous'):spice_deck(design,{'probes':['vout']})
    deck,meta=spice_deck(design,{'probes':['vout'],'ground_net':'gnd'})
    assert meta['nodes']['gnd']=='0' and meta['nodes']['isolated_ground']!='0'


@pytest.mark.skipif(not shutil.which('kicad-cli'),reason='Real KiCad executable required')
def test_release_reproducibility_checked_layer_job_drawing_dimensions_and_hashes(design,workspace):
    import zipfile
    import xml.etree.ElementTree as xml
    from o3d.domains.eda.workers import normalized_metadata
    source=workspace.cache/'source.oxygen-eda.json';source.parent.mkdir(parents=True,exist_ok=True);source.write_text('{"provenance":[{"date":"2026-10-24"}]}')
    assert normalized_metadata(source)==source.read_bytes()  # user provenance is immutable
    first=kicad(workspace,design,'release',{});second=kicad(workspace,design,'release',{})
    def package(result):return workspace.cache/next(a['path'] for a in result['artifacts'] if a['name']=='release.zip')
    a,b=package(first),package(second)
    assert a.read_bytes()==b.read_bytes()
    with zipfile.ZipFile(a) as bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        assert manifest['source_hash']==digest(design)
        for item in manifest['files']:assert hashlib.sha256(bundle.read(item['path'])).hexdigest()==item['sha256']
        job=json.loads(bundle.read('fabrication/board-job.gbrjob'))
        functions={item['Path']:item['FileFunction'] for item in job['FilesAttributes']}
        assert functions['board-In1_Cu.gbr']=='Copper,L2,Inr';assert functions['board-In2_Cu.gbr']=='Copper,L3,Inr'
        assert manifest['verification']['copper_file_functions']['board-B_Cu.gbr']=='Copper,L4,Bot'
        drawing=xml.fromstring(bundle.read('top-drawing.svg'));assert drawing.attrib['width']=='60.0mm' or drawing.attrib['width']=='60mm'
        assert len(drawing.findall('.//{http://www.w3.org/2000/svg}polygon'))>=6
        assert not any('external-tool-originals' in name or 'tool-' in name for name in bundle.namelist())


def test_router_rejects_fast_path_cancellation_and_unreachable_layer_endpoint(design,workspace):
    payload={'id':'route','net_id':'vin','layer':'F.Cu','start':{'component_id':'v1','pin_id':'p1'},'end':{'component_id':'r1','pin_id':'p1'}}
    design.library[0].pads[1].at=(-2,4)  # no obstacle on the direct route
    with pytest.raises(RuntimeError,match='cancelled before starting'):eda.execute(workspace,project(design),'route',payload,cancel=lambda:True)
    payload.update(net_id='vout',start={'component_id':'r1','pin_id':'p2'},end={'component_id':'c1','pin_id':'p1'})
    with pytest.raises(ValueError,match='no pad on the selected copper layer'):eda.execute(workspace,project(design),'route',payload)
