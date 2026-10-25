"""Independent load schedules measured against static, flight and slip limits."""
import json
import numpy as np
import pytest
from pydantic import ValidationError
from o3d.extended_solver import calculix_solve,write_calculix
from o3d.simulation_fem import event_solve
from o3d.simulation_loading import LoadPath
from o3d.simulation_models import Load,LoadCase
from o3d.simulation_solver_profile import validated_build
from o3d.solver import solver_binary
from .test_simulation_domain import fixture
from .test_contact_work import fixture as contact_fixture


@pytest.mark.parametrize('history',[[(0,0)],[(1,0),(2,1)],[(0,0),(0,1)],[(0,0),(1,float('nan'))]])
def test_invalid_load_schedule_is_not_normalized_into_a_guess(history):
    with pytest.raises(ValidationError):Load(id='test',source='Declared schedule',history=history)


def test_schedule_coverage_and_shared_fallback():
    first=Load(id='first',source='Declared independent schedule',history=[(0,0),(1,1),(2,1)])
    other=Load(id='other',source='Declared reference force')
    with pytest.raises(ValidationError,match='every load'):LoadCase(id='case',name='Incomplete',loads=[first,other])
    with pytest.raises(ValidationError,match='same declared case time'):
        LoadCase(id='case',name='Mismatched',loads=[first,other.model_copy(update={'history':[(0,0),(3,1)]})])
    case=LoadCase(id='case',name='Shared fallback',loads=[first,other],history=[(0,0),(2,-1)])
    _,_,_,_,system=fixture(divisions=(1,1,1),kind='nonlinear_static')
    for load in case.loads:load.region_id='part:xmax';load.vector=(100,0,0)
    path=LoadPath(system,case)
    assert path.times==[0,1,2]
    assert path.factors(1)=={'first':1,'other':-.5}
    assert path.at(1).reshape(-1,3).sum(axis=0)==pytest.approx([50,0,0])
    with pytest.raises(ValueError,match='outside'):path.at(2.01)


def test_cancelling_reference_loads_still_apply_and_clear_real_nodal_forces(tmp_path):
    _,_,_,case,system=fixture(divisions=(2,1,1),kind='nonlinear_static')
    case.loads[0].history=[(0,0),(1,1),(2,1)]
    case.loads.append(Load(id='opposite',region_id='part:xmax',vector=(-100,0,0),source='Declared delayed opposite force',history=[(0,0),(1,0),(2,1)]))
    result=calculix_solve(system,case,tmp_path)
    # Include lateral Poisson contraction at the (10,2,2) mm corner.
    expected=np.sqrt(.00125**2+2*(.3*25/200000*2)**2)
    assert result['values']['max_displacement_mm']==pytest.approx(expected,rel=1e-5)
    assert result['values']['final_displacement_mm']<1e-10
    assert result['checks']['force_balance_error']<1e-8
    phase=next(r for r in result['checks']['increment_equilibrium'] if r['time_s']==1)
    assert phase['load_factor'] is None
    assert phase['load_multipliers']=={'pull':1,'opposite':0}
    assert phase['applied_resultant_n']==pytest.approx([100,0,0])
    assert result['checks']['increment_equilibrium'][-1]['applied_resultant_n']==pytest.approx([0,0,0])
    path=json.loads((tmp_path/'load-path.json').read_text())
    assert path['independent_loads'] and path['force_scale_n']==pytest.approx(200)
    assert path['loads']['opposite']['history_origin']=='load'
    assert len(path['loads']['opposite']['spatial_force_sha256'])==64


def test_event_independent_uniform_acceleration_matches_free_flight():
    _,_,study,case,system=fixture(divisions=(1,1,1),kind='dynamic_event')
    duration=1e-5;acceleration=1e8
    case.supports=[]
    case.loads=[Load(id='forward',kind='gravity',vector=(acceleration,0,0),source='Declared uniform flight acceleration',history=[(0,0),(duration/2,1),(duration,1)]),
        Load(id='opposite',kind='gravity',vector=(-acceleration,0,0),source='Declared delayed opposite acceleration',history=[(0,0),(duration/2,0),(duration,1)])]
    study.settings.duration_s=duration;study.settings.time_step_s=duration/200
    result=event_solve(system,case)
    expected=acceleration*duration**2/4
    displacements=np.array(list(result['field']['displacements_mm'].values()))
    assert displacements[:,0]==pytest.approx(np.full(len(displacements),expected),rel=1e-7)
    assert np.max(abs(displacements[:,1:]))<1e-10
    assert result['checks']['energy_balance_error']<1e-4
    assert result['load_path']['independent_loads']
    assert result['time_history'][-1]['load_multipliers']=={'forward':1,'opposite':1}


@pytest.fixture(scope='module')
def cycle(tmp_path_factory):
    if validated_build(solver_binary()) is None:pytest.skip('Pinned contact state exporter is not configured')
    root=tmp_path_factory.mktemp('contact-cycle');results=[]
    for name,increment in [('coarse',.1),('fine',.05)]:
        system,case=contact_fixture('slide',increment)
        case.loads[0].history=[(0,0),(1,1),(4,1)]
        case.loads[1].history=[(0,0),(1,0),(2,1),(3,-1),(4,0)]
        case.loads[0].source='Declared 100 N normal preload held after parameter1'
        case.loads[1].source='Declared symmetric 100 N shear cycle, not measured friction'
        directory=root/name;result=calculix_solve(system,case,directory)
        results.append((system,result,directory))
    return results


def test_constant_preload_and_reversed_slip_match_independent_slider(cycle):
    # k_bulk=40000 N/mm, k_t=400000 N/mm, mu*N=30 N.
    # Plastic positions at S=+100,-100,0 N: .001675,-.001675,-.000825 mm.
    # Dissipation=30*(.001675+.00335+.00085)=.17625 N mm.
    system,result,directory=cycle[-1]
    phases={round(row['time_s']):row for row in result['contact_work_history'] if abs(row['time_s']-round(row['time_s']))<1e-7}
    for time,shear in [(1,0),(2,30),(3,-30),(4,30)]:
        assert phases[time]['secondary_force_n']['contact']==pytest.approx([100,shear,0],abs=1e-5)
        assert phases[time]['load_multipliers']['force']==1
    assert phases[1]['frictional_dissipation_n_mm']==0
    assert result['values']['frictional_dissipation_n_mm']==pytest.approx(.17625,rel=1e-3)
    from o3d.quadratic import surface_integrals
    region=system.regions['first:xmax'];weights,area,_=surface_integrals({'nodes':system.mesh.nodes},region.triangles)
    mean=sum(w*result['field']['displacements_mm'][n][1] for triangle,ws in zip(region.triangles,weights) for n,w in zip(triangle,ws))/area
    assert mean==pytest.approx(-.00075,rel=1e-3)
    assert all(b['frictional_dissipation_n_mm']>=a['frictional_dissipation_n_mm']-1e-12 for a,b in zip(result['contact_work_history'],result['contact_work_history'][1:]))
    assert result['checks']['contact_active_set_stability_qualified'] is False
    assert json.loads((directory/'load-path.json').read_text())['time_knots']==[0,1,2,3,4]


def test_cycle_work_and_external_energy_survive_actual_refinement(cycle):
    from o3d.simulation_contact_evidence import work_sensitivity
    coarse=cycle[0][1];fine=cycle[1][1]
    assert work_sensitivity(coarse,fine)<.02
    assert fine['checks']['energy_balance_error']<coarse['checks']['energy_balance_error']
    assert fine['checks']['energy_balance_error']<.02


def test_nonzero_initial_static_force_is_not_hidden_as_initialization(tmp_path):
    _,_,_,case,system=fixture(kind='nonlinear_static')
    case.loads[0].history=[(0,1),(1,1)]
    with pytest.raises(ValueError,match='zero multiplier'):write_calculix(tmp_path/'analysis.inp',system,case)
    assert not (tmp_path/'analysis.inp').exists()


def test_fractional_schedule_endpoints_retain_native_times_and_exact_loads(tmp_path):
    _,_,_,case,system=fixture(divisions=(1,1,1),kind='nonlinear_static')
    case.loads[0].history=[(0,0),(.123456789,1),(.345678912,0)]
    result=calculix_solve(system,case,tmp_path)
    assert result['values']['final_displacement_mm']<1e-10
    final=result['checks']['increment_equilibrium'][-1]
    assert final['load_evaluation_time']==.345678912
    assert final['time_s']==pytest.approx(.345678912,rel=1e-6)
    assert final['load_multipliers']=={'pull':0}
    assert result['checks']['residual']<1e-5
    assert 'Free-component nodal equilibrium' in result['checks']['residual_method']
