"""Quadratic geometry regressions, with independent Gmsh bounds and real meshes."""
import json
from pathlib import Path

import numpy as np
import pytest

from o3d.analysis_mesh import exterior_triangles,validate_mesh
from o3d.mesh_quality_worker import mesh_fingerprint
from o3d.quadratic import EDGES,FACES,weights_and_gradients
from o3d.solver import interpolate_probe,mesher_python,run_process


def tetrahedron(mapping=lambda p:p):
    vertices=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
    reference=np.vstack([vertices,[(vertices[a]+vertices[b])/2 for a,b in EDGES]])
    nodes={i+1:list(map(float,mapping(p))) for i,p in enumerate(reference)}
    return {"nodes":nodes,"elements":[list(nodes)],"surfaces":{"face":[[i+1 for i in FACES[0]]]}}


def audit_mesh(tmp_path,mesh):
    import o3d.mesh_quality_worker as worker
    request=tmp_path/"mesh.json";output=tmp_path/"curved-quality.json"
    request.write_text(json.dumps(mesh))
    run_process([mesher_python(),Path(worker.__file__),request,output],tmp_path,tmp_path/"audit.log",timeout=30)
    return json.loads(output.read_text())


@pytest.mark.parametrize("b,valid",[(3.,True),(3.4,False)])
def test_adaptive_bounds_catch_a_fold_missed_by_all_nodes_and_gauss_points(tmp_path,b,valid):
    # x=r*(1+a*s), y=s+b*r*(1-r), z=t. Its exact determinant is
    # 1+a*s-a*b*r+2*a*b*r^2; min=1-a*b/8 at r=1/4,s=0.
    a=2.5
    mesh=tetrahedron(lambda p:[p[0]*(1+a*p[1]),p[1]+b*p[0]*(1-p[0]),p[2]])
    xyz=np.asarray(list(mesh["nodes"].values()))
    for point in [[0,0,0],[1,0,0],[0,1,0],[0,0,1],[.5,0,0],[.5,.5,0],[0,.5,0],[0,0,.5],[.5,0,.5],[0,.5,.5]]:
        _,gradient=weights_and_gradients(point)
        assert np.linalg.det(xyz.T @ gradient)>0
    report=audit_mesh(tmp_path,mesh)
    assert report["sampled_gauss4_minimum_mm3"][0]>0
    expected=1-a*b/8
    assert report["jacobian_lower_mm3"][0]<=expected+1e-10
    assert report["jacobian_lower_mm3"][0]==pytest.approx(expected,abs=.011)
    if valid:
        quality=validate_mesh(mesh,curved_audit=report)
        assert quality["curved_jacobian_checked"]
        # Integral of r=s=1/4 and r^2=1/10 over a unit reference tet.
        expected_volume=(1+a/4-a*b/4+2*a*b/10)/6
        assert quality["integrated_volume_mm3"]==pytest.approx(expected_volume,rel=1e-10)
    else:
        assert report["jacobian_lower_mm3"][0]<0
        # The former corner-only check accepts this folded element.
        assert not validate_mesh(mesh)["curved_jacobian_checked"]
        with pytest.raises(ValueError,match="curved Jacobian"):
            validate_mesh(mesh,curved_audit=report)


def test_curved_audit_identity_completeness_and_positive_straight_volume(tmp_path):
    mesh=tetrahedron();report=audit_mesh(tmp_path,mesh)
    quality=validate_mesh(mesh,expected_volume=1/6,curved_audit=report)
    assert quality["minimum_jacobian_lower_mm3"]==pytest.approx(1)
    assert quality["integrated_volume_relative_error"]<5e-11
    changed=tetrahedron();changed["nodes"][5][0]=.51
    with pytest.raises(ValueError,match="exact solver mesh"):validate_mesh(changed,curved_audit=report)
    broken=report|{"element_ids":[]}
    with pytest.raises(ValueError,match="element identities"):validate_mesh(mesh,curved_audit=broken)
    broken=report|{"jacobian_lower_mm3":[]}
    with pytest.raises(ValueError,match="Incomplete"):validate_mesh(mesh,curved_audit=broken)
    broken=report|{"jacobian_lower_mm3":[1e-12]}
    with pytest.raises(ValueError,match="curved Jacobian"):validate_mesh(mesh,curved_audit=broken)


@pytest.mark.parametrize("a",[-.5,.5])
def test_physical_probe_uses_inverse_curved_geometry_instead_of_corner_coordinates(a):
    mesh=tetrahedron(lambda p:[p[0]+a*p[1]*p[2],p[1],p[2]])
    reference=np.array([.01,.4,.4]);physical=np.array([reference[0]+a*.16,.4,.4])
    scalar={n:2*p[0]-3*p[1]+4*p[2] for n,p in mesh["nodes"].items()}
    vector={n:[p[0],2*p[1],-p[2]] for n,p in mesh["nodes"].items()}
    assert interpolate_probe(mesh,scalar,physical)==pytest.approx(2*physical[0]-3*physical[1]+4*physical[2],abs=1e-10)
    assert interpolate_probe(mesh,vector,physical)==pytest.approx([physical[0],.8,-.4],abs=1e-10)
    if a>0:
        with pytest.raises(ValueError,match="could not be located"):interpolate_probe(mesh,scalar,[.01,.4,.4])


def test_boundary_midside_order_exterior_membership_and_display_facets():
    mesh=tetrahedron()
    assert len(exterior_triangles(mesh["elements"]))==16
    assert set(n for t in exterior_triangles(mesh["elements"]) for n in t)==set(mesh["nodes"])
    mesh["surfaces"]["face"][0][3:]=[6,5,7]
    with pytest.raises(ValueError,match="face-node ordering"):validate_mesh(mesh)
    mesh=tetrahedron();mesh["surfaces"]["face"][0]=[1,2,5,6,7,8]
    with pytest.raises(ValueError,match="non-exterior"):validate_mesh(mesh)
    mesh=tetrahedron();mesh["surfaces"]["face"]*=2
    with pytest.raises(ValueError,match="Duplicated"):validate_mesh(mesh)


def test_curved_planar_boundary_integrals_preserve_exact_force_moment_and_power():
    from o3d.quadratic import surface_integrals
    from o3d.analysis import distribute_scalar
    from o3d.solver import load_boundary
    from o3d.model import Study,Force
    # x=r*(1+2s), y=s. J=1+2s; exact area=5/6,
    # integral(x dA)=2/5, integral(y dA)=1/3 on the reference triangle.
    mesh=tetrahedron(lambda p:[p[0]*(1+2*p[1]),p[1],p[2]])
    triangles=mesh["surfaces"]["face"]
    weights,area,moment=surface_integrals(mesh,triangles)
    assert area==pytest.approx(5/6,rel=1e-12)
    assert moment==pytest.approx([2/5,1/3,0],abs=1e-12)
    assert weights[0]==pytest.approx([-1/60,-1/60,1/30,7/30,3/10,3/10],abs=1e-12)
    mesh["surfaces"]["load_0"]=triangles
    study=Study(forces=[Force(face="face",vector_n=(0,0,10))])
    _,forces=load_boundary(mesh,study)
    assert np.sum(list(forces.values()),axis=0)==pytest.approx([0,0,10],abs=1e-12)
    resultant=np.sum([np.cross(mesh["nodes"][n],f) for n,f in forces.items()],axis=0)
    assert resultant==pytest.approx(np.cross(moment/area,[0,0,10]),abs=1e-12)
    power=distribute_scalar(mesh,"face",1000)
    assert sum(power.values())==pytest.approx(1000,abs=1e-10)
    assert sum(v*mesh["nodes"][n][0] for n,v in power.items())==pytest.approx(1000*moment[0]/area,rel=1e-12)


def test_point_touching_volumes_and_incompatible_edge_nodes_refuse():
    from o3d.quadratic import boundary_faces
    mesh=tetrahedron();second=tetrahedron(lambda p:[-p[0],-p[2],-p[1]])
    mapping={1:1,**{n:n+9 for n in range(2,11)}}
    mesh["nodes"].update({mapping[n]:p for n,p in second["nodes"].items() if n!=1})
    mesh["elements"].append([mapping[n] for n in second["elements"][0]])
    with pytest.raises(ValueError,match="complete faces"):validate_mesh(mesh)
    mesh=tetrahedron();mesh["elements"]*=2
    with pytest.raises(ValueError,match="Duplicate volume"):validate_mesh(mesh)
    mesh=tetrahedron()
    second=[1,2,11,12,13,14,15,16,17,18]
    with pytest.raises(ValueError,match="shared edge"):
        boundary_faces([mesh["elements"][0],second])


def test_actual_mesh_with_a_rigid_frame_error_refuses_cad_plane(store,tmp_path,monkeypatch):
    import o3d.solver as solver
    from o3d.analysis_mesh import generate_mesh,prepare_geometry
    p=store.load();transfer=prepare_geometry(store,p)
    original=solver.run_process
    def translated(command,directory,log,*args,**kwargs):
        result=original(command,directory,log,*args,**kwargs)
        if str(command[1]).endswith('/mesh_worker.py'):
            file=Path(command[-1]);mesh=json.loads(file.read_text())
            for point in mesh['nodes'].values():point[0]+=.05
            file.write_text(json.dumps(mesh))
        return result
    monkeypatch.setattr(solver,'run_process',translated)
    run=tmp_path/'shifted';run.mkdir()
    with pytest.raises(ValueError,match="CAD plane"):
        generate_mesh(run,transfer,p)


def test_same_physical_mesh_cannot_earn_refinement_convergence(store,monkeypatch):
    import o3d.analysis as analysis
    original=analysis.generate_mesh
    def repeated(run,transfer,project,cancel=None):
        # Real mesher runs; deliberately hold its effective size constant to
        # reproduce a size field that was ignored by an adapter.
        p=project.model_copy(deep=True);p.study.mesh_size_mm=4
        return original(run,transfer,p,cancel)
    monkeypatch.setattr(analysis,'generate_mesh',repeated)
    p=store.load();p.study.stress_probe_mm=None
    result=analysis.simulate(store,p)
    assert result['state']=='inconclusive' and not result['converged']
    assert any(not i['refinement_verified'] for i in result['iterations'][1:])
    assert any('genuine refinement' in w for w in result['warnings'])


def test_mesher_binary_change_invalidates_study_without_changing_geometry(store,tmp_path,monkeypatch):
    from o3d.analysis import solver_identity
    from o3d.model import study_key,geometry_key
    runtime=tmp_path/'runtime';(runtime/'bin').mkdir(parents=True);(runtime/'lib').mkdir()
    (runtime/'bin/python').write_bytes(b'Fingerprint fixture, never executed')
    library=runtime/'lib/libgmsh.so.4.15';library.write_bytes(b'first library fixture')
    monkeypatch.setenv('O3D_MESH_PYTHON',str(runtime/'bin/python'))
    p=store.load();geometry=geometry_key(p);before=study_key(p)
    assert solver_identity()['mesher_runtime_files']['lib/libgmsh.so.4.15']
    library.write_bytes(b'changed library fixture')
    assert study_key(p)!=before and geometry_key(p)==geometry


def test_default_cad_probes_stay_in_material_when_mass_centroids_lie_in_holes():
    import build123d as bd
    from o3d.analysis_mesh import face_probe,solid_probe
    shape=bd.Cylinder(8,40)-bd.Cylinder(6,40)
    cap=next(f for f in shape.faces() if f.geom_type==bd.GeomType.PLANE)
    assert not cap.is_inside(cap.center()) and not shape.is_inside(shape.center())
    point,method=face_probe(cap)
    assert cap.is_inside(point) and 6<np.linalg.norm(point[:2])<8
    assert 'patch' in method.lower()
    point,method=solid_probe(shape)
    assert shape.is_inside(point) and 6<np.linalg.norm(point[:2])<8
    assert abs(point[2])<20
    assert 'interior offset' in method


def test_hollow_thermal_stress_keeps_one_cad_interior_probe_across_remeshing(tmp_path,beam,mesh_families):
    from o3d.model import TemperatureBoundary,HeatSource
    from o3d.store import Store
    from o3d.solver import simulate
    p=mesh_families['tube'];p.material=beam.material.model_copy(deep=True)
    p.material.thermal_conductivity_w_m_k=20;p.material.thermal_expansion_per_k=12e-6
    p.study.type='thermal_stress';p.study.forces=[];p.study.probe_point_mm=None
    p.study.reference_temperature_c=0;p.study.assumptions_confirmed=True
    p.study.temperatures=[TemperatureBoundary(face='part:xmin',temperature_c=0)]
    p.study.heat_sources=[HeatSource(face='part:xmax',power_w=1)]
    store=Store(tmp_path/'hollow');store.init(p);result=simulate(store,p)
    probes=[i['displacement_probe_mm'] for i in result['iterations']]
    assert all(v==probes[0] for v in probes)
    assert 6<np.linalg.norm(probes[0][1:])<8
    assert 'interior offset' in result['displacement_probe_method']
    field=json.loads(store.artifact(result['field_artifact']).read_text())
    extension=max(v[0] for v in field['displacements'].values())
    assert extension==pytest.approx(12e-6*result['max_temperature_c']/2*40,rel=.03)
    assert result['force_balance_error']<.01 and result['moment_balance_error']<.01
