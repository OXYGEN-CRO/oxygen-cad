"""Exercise generated artifacts and failure boundaries of the bundled workflows."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import zipfile
from urllib.parse import parse_qs, urlparse, unquote

import build123d as bd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper(skill, filename):
    path=ROOT/'skills'/('oxygen-cad-'+skill)/'scripts'/filename
    spec=importlib.util.spec_from_file_location(skill.replace('-','_')+'_'+path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.fixture
def part(tmp_path):
    solid=bd.Box(20,10,4)-bd.Cylinder(1.5,4)
    step,stl=tmp_path/'source.step',tmp_path/'source.stl'
    bd.export_step(solid,step);bd.export_stl(solid,stl)
    return step,stl


def test_catalog_checksum_content_and_no_overwrite(tmp_path):
    m=helper('parts','catalog.py');content=b'ISO-10303-21;\nEND-ISO-10303-21;\n';queries=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed=urlparse(self.path);queries.append(parsed)
            if parsed.path=='/asset':body=content
            elif parsed.path=='/v1/parts':body=json.dumps({'items':[{'id':'chosen'}]}).encode()
            else:body=json.dumps({'id':parsed.path.rsplit('/',1)[-1], 'stepUrl':origin+'/asset',
                                  'sha256':hashlib.sha256(content).hexdigest() if parsed.path.endswith('/chosen') else '0'*64}).encode()
            self.send_response(200);self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);origin=f'http://127.0.0.1:{server.server_port}'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert m.search(origin,'M3 & motor',5)['items'][0]['id']=='chosen'
        assert parse_qs(queries[0].query)=={'q':['M3 & motor'],'pageSize':['5']}
        output=tmp_path/'incoming'/'part.step';result=m.download(origin,'chosen',output)
        assert result['catalog_checksum_verified'] and output.read_bytes()==content
        with pytest.raises(ValueError,match='exists'):m.download(origin,'chosen',output)
        with pytest.raises(ValueError,match='checksum'):m.download(origin,'bad',tmp_path/'bad.step')
        assert not (tmp_path/'bad.step').exists()
    finally:server.shutdown();server.server_close();thread.join()


def test_pdf_views_use_measured_geometry_and_preserve_existing(part,tmp_path):
    pytest.importorskip('reportlab')
    m=helper('drawing','drawing.py');output=tmp_path/'part.pdf'
    result=m.drawing(part[0],output,'Test bracket','7')
    assert result['dimensions_mm']==pytest.approx([20,10,4])
    assert result['views']==['front','top','right']
    assert output.read_bytes().startswith(b'%PDF') and len(output.read_bytes())>2000
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='exists'):m.drawing(part[0],output,'Replace','8')
    assert hashlib.sha256(output.read_bytes()).hexdigest()==digest


def test_dxf_section_preserves_hole_units_and_closed_contours(part,tmp_path):
    m=helper('dxf','profiles.py');output=tmp_path/'section.dxf'
    result=m.section(part[0],0,output)
    assert result['insunits']==4 and result['dimensions_in_declared_units']==pytest.approx([20,10,0])
    assert result['entity_counts'].get('CIRCLE')==1
    assert result['unjoined_or_branched_endpoints']==[]
    assert not result['degenerate_entities'] and not result['exact_duplicates']
    with pytest.raises(ValueError,match='exists'):m.section(part[0],0,output)
    with pytest.raises(ValueError,match='intersect'):m.section(part[0],100,tmp_path/'empty.dxf')
    assert not (tmp_path/'empty.dxf').exists()


def test_dxf_inspection_detects_duplicate_open_and_unknown_geometry(tmp_path):
    import ezdxf
    doc=ezdxf.new();doc.header['$INSUNITS']=0;space=doc.modelspace()
    space.add_line((0,0),(2,0));space.add_line((0,0),(2,0));space.add_line((3,0),(4,0));space.add_text('Not a cut')
    path=tmp_path/'bad.dxf';doc.saveas(path)
    result=helper('dxf','profiles.py').inspect_dxf(path)
    assert result['exact_duplicates']==1 and result['unjoined_or_branched_endpoints']
    assert result['units']=='unknown' and result['unsupported_entities']


def test_mesh_screen_has_numeric_chords_orientations_and_explicit_omissions(tmp_path):
    import trimesh
    path=tmp_path/'plate.stl';trimesh.creation.box(extents=(20,10,2)).export(path)
    result=helper('dfam','mesh_review.py').measure(path,samples=128,overhang=45)
    assert result['watertight'] and result['consistent_winding']
    assert result['dimensions_mm']==pytest.approx([20,10,2])
    assert result['normal_chord_mm']['min']==pytest.approx(2,abs=1e-4)
    assert result['normal_chord_mm']['samples']>0 and len(result['orientations'])==6
    assert result['orientations'][0]['downward_area_over_limit_mm2']==pytest.approx(0)
    assert 'minimum_wall_everywhere' in result['unmeasured']
    mesh=trimesh.creation.box();mesh.update_faces(list(range(len(mesh.faces)-1)));mesh.export(path)
    result=helper('dfam','mesh_review.py').measure(path)
    assert not result['watertight'] and result['normal_chord_mm']['min'] is None


def test_manufacturing_facts_and_mold_candidates_are_separate(part,tmp_path):
    import trimesh
    facts=helper('dfm','part_facts.py').facts(part[0])
    assert facts['valid'] and facts['solids']==1
    assert 1.5 in facts['circular_edge_radii_mm'] and 'tool_access' in facts['unmeasured']
    path=tmp_path/'box.stl';trimesh.creation.box(extents=(20,10,4)).export(path)
    result=helper('dfm','mold_review.py').measure(path,'z',2)
    assert result['projected_bounding_rectangle_mm2']==pytest.approx(200)
    assert result['side_face_area_below_threshold_mm2']>0
    assert result['pull_occlusion_candidates']==0


@pytest.fixture
def robot(tmp_path):
    path=tmp_path/'robot.urdf'
    path.write_text('''<robot name="arm"><link name="base"/><link name="tip"><inertial><mass value="1"/>
    <inertia ixx="1" iyy="1" izz="1" ixy="0" ixz="0" iyz="0"/></inertial>
    <visual><geometry><box size="0.02 0.01 0.004"/></geometry></visual></link>
    <joint name="hinge" type="revolute"><parent link="base"/><child link="tip"/><axis xyz="0 0 1"/>
    <limit lower="-1.5" upper="1.5" effort="2" velocity="1"/></joint></robot>''')
    return path


def test_urdf_lint_reports_inertia_topology_and_missing_mesh(robot):
    m=helper('urdf','robot_check.py');result=m.check(robot)
    assert result['ok'] and len(result['links'])==2
    assert any(i['code']=='missing_inertial' for i in result['issues'])
    original=robot.read_text();robot.write_text(original.replace('izz="1"','izz="10"'))
    result=m.check(robot);assert not result['ok'] and any(i['code']=='inertia' for i in result['issues'])
    robot.write_text(original.replace('<box size="0.02 0.01 0.004"/>','<mesh filename="absent.stl"/>'))
    assert any(i['code']=='missing_mesh' for i in m.check(robot)['issues'])
    robot.write_text(original.replace('child link="tip"','child link="base"'))
    assert not m.check(robot)['ok']


def test_urdf_rejects_entity_xml_and_nonfinite_values(robot):
    m=helper('urdf','robot_check.py');robot.write_text(robot.read_text().replace('value="1"','value="nan"'))
    assert not m.check(robot)['ok']
    robot.write_text('<!DOCTYPE robot [<!ENTITY a "x">]><robot name="x"/>')
    with pytest.raises(ValueError,match='DTD'):m.check(robot)


def test_srdf_cross_checks_chain_and_joint_limits(robot,tmp_path):
    m=helper('srdf','planning_check.py');path=tmp_path/'robot.srdf'
    text='''<robot name="arm"><group name="arm"><chain base_link="base" tip_link="tip"/></group>
    <group_state name="home" group="arm"><joint name="hinge" value="0"/></group_state>
    <disable_collisions link1="base" link2="tip" reason="Adjacent"/></robot>'''
    path.write_text(text);result=m.check(path,robot)
    assert result['ok'] and result['groups']['arm']==['hinge']
    path.write_text(text.replace('value="0"','value="10"'))
    assert not m.check(path,robot)['ok']
    path.write_text(text.replace('base_link="base" tip_link="tip"','base_link="tip" tip_link="base"'))
    assert not m.check(path,robot)['ok']


def test_sdf_frame_cycles_and_required_consumer_are_not_passes(tmp_path,monkeypatch):
    m=helper('sdf','simulator_check.py');path=tmp_path/'model.sdf'
    text='''<sdf version="1.9"><model name="m"><link name="base"><inertial><mass>1</mass>
    <inertia><ixx>1</ixx><iyy>1</iyy><izz>1</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia>
    </inertial></link><frame name="a" attached_to="base"><pose relative_to="base">0 0 0 0 0 0</pose></frame></model></sdf>'''
    path.write_text(text);assert m.check(path,'never')['ok']
    monkeypatch.setattr(m.shutil,'which',lambda name:None)
    result=m.check(path,'required');assert not result['ok'] and result['gz_check']['state']=='unavailable'
    path.write_text(text.replace('relative_to="base"','relative_to="a"'))
    assert any(i['code']=='frame_cycle' for i in m.check(path,'never')['issues'])


def test_slicer_resolves_inheritance_refuses_cycles_and_plans_without_running(tmp_path):
    m=helper('slice','slicer.py');library=tmp_path/'profiles';library.mkdir()
    parent=library/'parent.json';child=library/'child.json'
    parent.write_text(json.dumps({'name':'base','bed_size':[200,200],'layer_height':'0.2'}))
    child.write_text(json.dumps({'name':'custom','inherits':'base','layer_height':'0.16'}))
    resolved=m.resolve(child,library);assert resolved['bed_size']==[200,200] and resolved['layer_height']=='0.16' and 'inherits' not in resolved
    parent.write_text(json.dumps({'name':'base','inherits':'custom'}))
    with pytest.raises(ValueError,match='Cyclic'):m.resolve(child,library)
    child.write_text(json.dumps(resolved));model=tmp_path/'part.stl';model.write_text('fixture')
    output=tmp_path/'new slices';command=m.plan('/not/run/orca',model,child,child,child,output)
    assert '--slice' in command and '--load-filaments' in command and not output.exists()
    child.write_text(json.dumps({'inherits':'missing'}))
    with pytest.raises(ValueError,match='Resolve'):m.plan('orca',model,child,child,child,output)


def test_bambu_handoff_encodes_path_and_never_claims_print_started(tmp_path):
    m=helper('bambu','handoff.py');path=tmp_path/'part with # &.gcode.3mf';path.write_bytes(b'fixture')
    result=m.plan(path,'connect','darwin');query=parse_qs(urlparse(result['uri']).query)
    assert query['path']==[str(path)] and not result['print_started']
    assert '%23' in result['uri'] and '%26' in result['uri'] and result['argv'][0]=='open'
    assert m.plan(path,'connect','win32')['argv'][0]=='os.startfile'
    with pytest.raises(ValueError,match='Studio'):m.plan(path,'connect','linux')
    model=tmp_path/'part.stl';model.write_bytes(b'fixture')
    assert m.plan(model,'studio','linux')['argv'][0]=='bambu-studio'


def test_slicer_does_not_accept_an_unsliced_project_as_sliced_output(tmp_path):
    m=helper('slice','slicer.py');path=tmp_path/'job.gcode.3mf'
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('3D/model.model','geometry only')
    assert m.slice_outputs(tmp_path)==[]
    with zipfile.ZipFile(path,'a') as archive:archive.writestr('Metadata/plate_1.gcode','G1 X1 Y2\n')
    assert m.slice_outputs(tmp_path)==[path]
