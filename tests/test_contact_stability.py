"""Current contact restraint, reference bulk kernels and explicit limits."""
import json
import numpy as np
import pytest
from o3d.simulation_contact import SurfaceContact
from o3d.simulation_contact_stability import ContactStateAudit,closed_response,bulk_kernel
from o3d.simulation_models import Support
from o3d.simulation_quadratic import QuadraticSystem
from .test_canonical_contact import reference


def problem():
    _,domain,study,case=reference(refined=False)
    return QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials}),case


def point(audit,record,y,*,friction=False,sliding=False,boundary=False):
    normal=np.array([1.,0,0]);gap=0. if boundary else -2.5e-6
    relative=np.array([gap,.001 if sliding else 1e-5,0.])
    if not friction:relative[1]=0
    old=np.zeros(9)
    response=closed_response(normal,gap,relative,old,1e7,1e5,.3 if friction else 0.,1.)
    pressure=-1e7*gap;tau=response['force_n']+pressure*normal
    audit.point(record,normal,1.,gap,relative,pressure,tau,old,response['state'],np.array([10.,y,1]),np.array([10.,y,1]))


def test_every_native_bulk_strain_operator_keeps_six_rigid_and_24_strain_modes(monkeypatch):
    system,_=problem();value=bulk_kernel(system)
    assert value['checked'] and value['minimum_nonrigid_singular_ratio']>1e-10
    assert value['maximum_rigid_kernel_residual']<1e-12
    from o3d import simulation_contact_stability as module
    original=module.weights_and_gradients
    def corners_only(position):
        shape,_=original(position)
        gradient=np.vstack([-np.ones((1,3)),np.eye(3),np.zeros((6,3))])
        return shape,gradient
    monkeypatch.setattr(module,'weights_and_gradients',corners_only)
    assert not bulk_kernel(system)['checked']


def test_current_map_loss_exposes_a_real_bulk_rigid_null_direction(tmp_path):
    system,case=problem();contact=SurfaceContact(system);contact.validate_supports(case)
    audit=ContactStateAudit(system,case);audit.start({'step':2,'increment':1})
    point(audit,contact.records[0],1);audit.finish(.1)
    assert audit.history[-1]['normal_restraint_rank']==12
    assert audit.history[-1]['positive_frozen_operator_kernel_structure']
    # Deliberately remove the current contact map as a diagnostic fault. No
    # successful native equilibrium is claimed for this unsupported state.
    audit.start({'step':2,'increment':2});audit.finish(.2)
    assert audit.history[-1]['numerical_rigid_mechanism']
    assert audit.history[-1]['maximum_possible_rank']==11
    summary=audit.write(tmp_path)
    assert summary['numerical_rigid_mechanism_detected'] and not summary['full_contact_stability_qualified']
    witness=audit.snapshots['mechanism_witnesses'][:,0]
    displacement=np.array([audit.basis(system.mesh.nodes[n],system.component_of[n])@witness[6*system.component_of[n]:6*system.component_of[n]+6] for n in system.ids]).ravel()
    from o3d.simulation_eigen import QuadraticElasticSystem
    bulk=QuadraticElasticSystem(system.mesh,system.study.model_copy(update={'interfaces':[]}),{m.id:m for m in system.assignments.values()})
    assert np.linalg.norm(bulk.k@displacement)/(np.linalg.norm(bulk.k.data)*np.linalg.norm(displacement))<1e-12
    assert np.linalg.norm(audit.supports@witness)<1e-10


def test_stick_can_add_incremental_restraint_but_slip_cannot_fake_a_mount():
    system,case=problem()
    case.supports[-1].components=(False,False,True)
    system.study.interfaces[0].friction_coefficient=.3;system.study.interfaces[0].tangential_penalty_n_mm3=1e5
    system.study.interfaces[0].source='Declared frictional restraint diagnostic; not measured hardware'
    record=SurfaceContact(system).records[0]
    for sliding,expected in [(False,12),(True,11)]:
        audit=ContactStateAudit(system,case);audit.start({'step':2,'increment':1})
        for y in [0.,2.]:point(audit,record,y,friction=True,sliding=sliding)
        audit.finish(.5);row=audit.history[-1]
        assert row['normal_restraint_rank']==11
        assert row['guaranteed_constitutive_rank']==expected
        assert row['maximum_possible_rank']==expected
        assert row['numerical_rigid_mechanism']==sliding
        assert (row['maximum_local_tangent_asymmetry']>.1)==sliding
    # The public pre-solve rule still refuses counting friction as a missing mount.
    with pytest.raises(ValueError,match='initial unrestrained rigid mechanism'):
        SurfaceContact(system).validate_supports(case)


def test_zero_pressure_boundary_is_not_promoted_to_guaranteed_restraint():
    system,case=problem();record=SurfaceContact(system).records[0]
    audit=ContactStateAudit(system,case);audit.start({'step':2,'increment':1})
    point(audit,record,1,boundary=True);audit.finish(1.)
    row=audit.history[-1]
    assert row['normal_restraint_rank']==11 and row['maximum_possible_rank']==12
    assert row['point_states']['boundary']==1
    assert not row['positive_frozen_operator_kernel_structure']
    assert not row['numerical_rigid_mechanism'] # ambiguous, not a proved missing row


@pytest.mark.parametrize('angle',[0.,np.pi/7,-.31])
def test_rotated_sliding_projector_cannot_invent_a_shear_constraint(angle):
    _,domain,study,case=reference(refined=False)
    q=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
    mesh=domain.models[0].mesh.model_copy(update={'nodes':{n:tuple(q@p) for n,p in domain.models[0].mesh.nodes.items()}})
    system=QuadraticSystem(mesh,study,{m.id:m for m in domain.materials});case.supports[-1].components=(False,False,True)
    record=SurfaceContact(system).records[0]|{'friction_coefficient':.3,'tangential_penalty_n_mm3':1e5}
    audit=ContactStateAudit(system,case);audit.start({'step':2,'increment':1})
    normal=q@np.array([1.,0,0]);relative=q@np.array([-2.5e-6,.001,0]);old=np.zeros(9)
    response=closed_response(normal,-2.5e-6,relative,old,1e7,1e5,.3,1.)
    for y in [0.,2.]:
        location=q@np.array([10.,y,1.])
        audit.point(record,normal,1.,-2.5e-6,relative,25.,response['force_n']+25*normal,old,response['state'],location,location)
    audit.finish(.5)
    assert audit.history[-1]['maximum_possible_rank']==11
    assert audit.history[-1]['numerical_rigid_mechanism']


def test_nonlinear_bulk_never_inherits_linear_kernel_or_mechanism_claim():
    system,case=problem();system.study.settings.large_deformation=True
    assert not bulk_kernel(system)['checked']
    audit=ContactStateAudit(system,case);audit.start({'step':2,'increment':1});audit.finish(1.)
    assert not audit.history[-1]['numerical_rigid_mechanism']
    assert not audit.history[-1]['positive_frozen_operator_kernel_structure']
