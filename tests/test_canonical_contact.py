"""Actual face contact, independent SI series compliance and explicit limits."""
import json
import numpy as np
import pytest
from pydantic import ValidationError
from o3d.simulation_models import Interface,Region,Support,Load
from o3d.simulation_quadratic import QuadraticSystem
from o3d.extended_solver import calculix_solve
from o3d.simulation_worker import solve
from .test_canonical_bonded import reference as bonded_reference
from .test_canonical_quadratic import request


def reference(refined=True):
    project,domain,study,case=bonded_reference(refined=refined)
    study.type='nonlinear_static';study.id='contact'
    domain.active_study_id=study.id
    study.interfaces=[Interface(id='contact',kind='separation',formulation='surface_to_surface_penalty',
        primary_region_id='first:xmax',secondary_region_id='second:xmin',position_tolerance_mm=1e-7,
        normal_penalty_n_mm3=1e7,friction_coefficient=0,maximum_penetration_mm=1e-4,
        source='Declared numerical linear pressure/overclosure model; frictionless, not a measured interface')]
    for mesh in [domain.models[0].mesh,*domain.models[0].refinements]:
        nodes=sorted({n for e in mesh.elements if e.body_id=='second' for n in e.nodes})
        mesh.regions.append(Region(id='second:guide',body_id='second',node_ids=nodes))
    case.supports.append(Support(id='guide',region_id='second:guide',kind='partial',components=(False,True,True)))
    case.loads[0].vector=(-100,0,0)
    return project,domain,study,case


def test_real_face_contact_matches_independent_SI_bar_and_penalty_compliance(tmp_path):
    _,domain,study,case=reference(refined=False)
    system=QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    result=calculix_solve(system,case,tmp_path)
    # Two 10 mm bars, area 4 mm2, E=200 GPa; pressure=25 MPa.
    elastic_mm=100*.020/(200e9*4e-6)*1000
    penetration_mm=(100/4)/1e7
    assert result['values']['probe_displacement_mm']==pytest.approx(elastic_mm+penetration_mm,rel=1e-6)
    assert result['interfaces'][0]['maximum_pressure_mpa']==pytest.approx(25,rel=1e-6)
    assert result['checks']['maximum_contact_penetration_mm']==pytest.approx(penetration_mm,rel=1e-6)
    assert result['interfaces'][0]['final_secondary_resultant_n']==pytest.approx([100,0,0],abs=1e-6)
    assert result['checks']['contact_body_force_balance_error']<1e-6
    assert result['checks']['contact_body_moment_balance_error']<1e-6
    assert result['checks']['contact_pressure_law_error']<1e-6
    assert result['checks']['contact_initial_support_audit']['rank']==12
    for row in result['load_displacement_history']:
        assert row['max_displacement_mm']==pytest.approx((elastic_mm+penetration_mm)*row['time'],rel=1e-6)
    assert result['contact_initialization']['maximum_displacement_mm']<1e-10
    data=json.loads((tmp_path/'contact.json').read_text())
    assert data['history']['1.0']['spring_energy_n_mm']==pytest.approx(.5*100*penetration_mm,rel=1e-6)
    assert data['frictional_dissipation_n_mm'] is None
    assert 'positive opening is clipped' in result['field']['contact_gap_convention']
    deck=(tmp_path/'analysis.inp').read_text()
    assert 'TYPE=SURFACE TO SURFACE' in deck and 'ADJUST' not in deck
    assert 'CONTACT_0_SECONDARY,CONTACT_0_PRIMARY' in deck


def test_friction_cannot_replace_a_missing_mounting_constraint(tmp_path):
    _,domain,study,case=reference(refined=False)
    case.supports=case.supports[:1]
    study.interfaces[0].friction_coefficient=.3;study.interfaces[0].tangential_penalty_n_mm3=1e5
    system=QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    with pytest.raises(ValueError,match='initial unrestrained rigid mechanism'):
        calculix_solve(system,case,tmp_path)
    assert not (tmp_path/'analysis.inp').exists()


def test_surface_units_and_source_parameters_are_not_inferred():
    interface=reference(refined=False)[2].interfaces[0].model_dump()
    for change in [{'normal_penalty_n_mm3':None},{'friction_coefficient':None},{'maximum_penetration_mm':None},
                   {'friction_coefficient':.3},{'tangential_penalty_n_mm3':1e5},{'stiffness_n_mm':1e7},
                   {'initial_gap_mm':.1},{'kind':'bonded'},{'source':''}]:
        with pytest.raises(ValidationError):Interface.model_validate(interface|change)


def test_worker_retains_nominal_fields_and_independent_penalty_trial_without_overclaim(tmp_path):
    project,domain,study,_=reference()
    result=solve(request(project,domain,study),tmp_path)
    assert result['values']['probe_displacement_mm']==pytest.approx(.0025025,rel=1e-6)
    assert result['checks']['mesh_converged']
    assert result['checks']['contact_penalty_change']==pytest.approx(.0004995,rel=.01)
    assert result['checks']['load_step_change']<1e-5
    assert result['numerical_state']=='inconclusive'
    assert result['verification_gaps']==['contact active-set tangent stability not qualified']
    assert result['physical_validation']=='not_supplied'
    assert (tmp_path/'mesh-1/penalty-double/contact.json').exists()
    lines=(tmp_path/'mesh-1/penalty-double/analysis.inp').read_text().splitlines()
    assert float(lines[lines.index('*SURFACE BEHAVIOR,PRESSURE-OVERCLOSURE=LINEAR')+1])==2e7
    assert result['interfaces'][0]['normal_penalty_n_mm3']==1e7


def test_open_interface_carries_no_tension_and_is_not_reported_as_measured_closed(tmp_path):
    _,domain,study,case=reference(refined=False)
    case.supports=[Support(id='first',region_id='first:xmin'),Support(id='second',region_id='second:xmax')]
    case.loads[0].region_id='second:xmin';case.loads[0].vector=(100,0,0)
    study.settings.probe_point_mm=(10,1,1)
    system=QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    result=calculix_solve(system,case,tmp_path)
    assert result['values']['probe_displacement_mm']==pytest.approx(.00125,rel=1e-5)
    assert result['interfaces'][0]['final_secondary_resultant_n']==pytest.approx([0,0,0],abs=1e-7)
    assert result['checks']['maximum_contact_penetration_mm']==0
    assert result['checks']['contact_initial_support_audit']['uses_contact_for_rigid_support'] is False
    assert max(abs(v[0]) for v in result['field']['nodal_contact'].values())==0


@pytest.mark.parametrize('shear_n',[10.,100.])
def test_sourced_friction_matches_independent_stick_and_slip_limits(tmp_path,shear_n):
    _,domain,study,case=reference(refined=False)
    study.interfaces[0].friction_coefficient=.3;study.interfaces[0].tangential_penalty_n_mm3=1e5
    study.interfaces[0].source='Declared Coulomb verification coefficient 0.3 and stick slope 100000 N/mm3; not measured hardware'
    mesh=domain.models[0].mesh
    mesh.regions.append(Region(id='first:guide',body_id='first',node_ids=sorted({n for e in mesh.elements if e.body_id=='first' for n in e.nodes})))
    case.supports.append(Support(id='simple-shear-guide',region_id='first:guide',kind='partial',components=(True,False,True)))
    case.loads.append(Load(id='shear',region_id='first:xmax',vector=(0,shear_n,0),source='Declared simple shear verification; x/z displacement constrained in first solid'))
    system=QuadraticSystem(mesh,study,{m.id:m for m in domain.materials})
    result=calculix_solve(system,case,tmp_path)
    # G=100 GPa, A=4 mm2, L=10 mm: shear stiffness=40000 N/mm.
    # Stick stiffness=1e5 N/mm3 * 4 mm2. Sliding caps total shear at mu*N.
    expected_friction=min(shear_n*400000/440000,.3*100)
    assert result['interfaces'][0]['final_secondary_resultant_n'][1]==pytest.approx(expected_friction,rel=1e-3)
    from o3d.quadratic import surface_integrals
    region=next(r for r in mesh.regions if r.id=='first:xmax')
    weights,area,_=surface_integrals({'nodes':mesh.nodes},region.triangles)
    mean=sum(w*result['field']['displacements_mm'][n][1] for t,ws in zip(region.triangles,weights) for n,w in zip(t,ws))/area
    assert mean==pytest.approx((shear_n-expected_friction)/40000,rel=1e-3)
    assert result['checks']['contact_coulomb_bound_error']<1e-5
    assert result['checks']['contact_body_force_balance_error']<1e-5
    assert result['checks']['frictional_work_qualified'] is False
    assert result['interfaces'][0]['declared_maximum_penetration_mm']==1e-4


@pytest.mark.parametrize('text,value',[('-1.061915-102',-1.061915e-102),('1.234567+101',1.234567e101),('2.5D-02',.025),('0',0.)])
def test_actual_fortran_exponent_format_preserves_small_values(text,value):
    from o3d.solver import calculix_float
    assert calculix_float(text)==value


def test_three_body_contact_chain_retains_each_pair_force_and_body_balance(tmp_path):
    from o3d.simulation_mesh import block_mesh
    from o3d.simulation_models import BodyAssignment
    from o3d.model import geometry_key
    project,domain,study,case=reference(refined=False)
    mesh=domain.models[0].mesh;third=block_mesh(geometry_key(project),origin=(20,0,0),divisions=(2,2,2),body_id='third',element_family='C3D10')
    offset=max(mesh.nodes);element_offset=max(e.id for e in mesh.elements)
    mesh.nodes.update({n+offset:p for n,p in third.nodes.items()})
    mesh.elements.extend(e.model_copy(update={'id':e.id+element_offset,'nodes':tuple(n+offset for n in e.nodes)}) for e in third.elements)
    mesh.regions.extend(r.model_copy(update={'node_ids':[n+offset for n in r.node_ids],'triangles':[tuple(n+offset for n in t) for t in r.triangles]}) for r in third.regions)
    mesh.regions.append(Region(id='third:guide',body_id='third',node_ids=[n+offset for n in third.nodes]))
    study.assignments.append(BodyAssignment(body_id='third',material_id='synthetic'))
    study.interfaces.append(study.interfaces[0].model_copy(update={'id':'second-contact','primary_region_id':'second:xmax','secondary_region_id':'third:xmin'}))
    case.supports.append(Support(id='third-guide',region_id='third:guide',kind='partial',components=(False,True,True)))
    case.loads[0].region_id='third:xmax';study.settings.probe_point_mm=(30,1,1);study.settings.probe_body_id='third'
    result=calculix_solve(QuadraticSystem(mesh,study,{m.id:m for m in domain.materials}),case,tmp_path)
    assert result['values']['probe_displacement_mm']==pytest.approx(.003755,rel=1e-6)
    assert len(result['interfaces'])==2
    for pair in result['interfaces']:assert pair['final_secondary_resultant_n']==pytest.approx([100,0,0],abs=1e-6)
    assert result['checks']['contact_initial_support_audit']['rank']==18
    assert result['checks']['contact_body_force_balance_error']<1e-6
