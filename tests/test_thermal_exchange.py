import json
import math
from pathlib import Path
import numpy as np
import pytest
from scipy.optimize import brentq

from o3d.model import ConvectionBoundary, RadiationBoundary, HeatSource, TemperatureBoundary, Requirement
from o3d.analysis import validate_study
from o3d.solver import simulate
from o3d.thermal_exchange import heat_exchange, internal_heat_loads, element_surfaces, exchange_deck


def tetra():
    points = [(0,0,0),(1,0,0),(0,1,0),(0,0,1),(.5,0,0),(.5,.5,0),
              (0,.5,0),(0,0,.5),(.5,0,.5),(0,.5,.5)]
    return {'nodes': {str(i+1): p for i,p in enumerate(points)}, 'elements': [list(range(1,11))],
            'surfaces': {'radiation_0': [[1,2,3,5,6,7]], 'second': [[1,2,4,5,9,8]],
                         'third': [[2,3,4,6,10,9]], 'fourth': [[1,3,4,7,10,8]]}}


def setup(store, mode, mesher='netgen'):
    p=store.load();p.study.type='thermal';p.study.forces=[];p.study.fixed_faces=[]
    p.study.stress_probe_mm=None;p.study.probe_point_mm=(40,0,0)
    p.study.mesh_size_mm=4;p.study.mesher=mesher;p.requirements=[]
    p.material.thermal_conductivity_w_m_k=20
    p.study.heat_sources=[HeatSource(face='part:xmin',power_w=.02)]
    if mode in ('convection','combined','internal'):
        p.study.convection=[ConvectionBoundary(face='part:xmax',coefficient_w_m2_k=25,ambient_temperature_c=20,
                                               source='Synthetic verification film/environment, not measured airflow')]
    if mode in ('radiation','combined','cold'):
        p.study.radiation=[RadiationBoundary(face='part:xmax',emissivity=.8,ambient_temperature_c=-50 if mode=='cold' else 20,
                                             source='Synthetic gray diffuse unit-view-factor verification environment')]
    if mode=='cold':
        p.study.heat_sources[0].power_w=.002
        p.requirements=[Requirement(id='cold_limit',kind='max_temperature',value=-20)]
    if mode in ('internal','thermostat'):
        p.study.heat_sources=[];p.study.internal_heat_w=.02
        p.study.internal_heat_source='Synthetic uniform volumetric generation for exact 1D solution'
    if mode=='thermostat':p.study.temperatures=[TemperatureBoundary(face='part:xmax',temperature_c=20)]
    return p


def test_exact_quadratic_volume_load_conserves_power_and_first_moment():
    mesh=tetra();loads,volume=internal_heat_loads(mesh,1)
    assert volume==pytest.approx(1/6,rel=1e-13)
    assert sum(loads.values())==pytest.approx(1000,rel=1e-13)
    assert [loads[n] for n in range(1,5)]==pytest.approx([-50]*4,rel=1e-12)
    assert [loads[n] for n in range(5,11)]==pytest.approx([200]*6,rel=1e-12)
    moment=sum((np.asarray(mesh['nodes'][str(n)])*v for n,v in loads.items()),np.zeros(3))
    assert moment==pytest.approx([250]*3,rel=1e-12)
    assert [element_surfaces(mesh,name)[0][1] for name in mesh['surfaces']]==[1,2,3,4]


def test_radiation_integrates_quartic_physical_temperature_on_curved_planar_face(beam):
    mesh=tetra();mesh['nodes']['5']=(.45,0,0)
    beam.study.radiation=[RadiationBoundary(face='plane',emissivity=.7,ambient_temperature_c=20,source='Analytical fixture')]
    temperatures={int(n):20+100*p[0] for n,p in mesh['nodes'].items()}
    result=heat_exchange(mesh,beam.study,temperatures)
    expected=.7*5.670374419e-8*1e-6*sum(math.comb(4,k)*293.15**(4-k)*100**k/((k+1)*(k+2)) for k in range(1,5))
    assert result['radiation_w']==pytest.approx(expected,rel=1e-12)
    assert result['quadrature_passed']
    assert result['boundaries'][0]['area_mm2']==pytest.approx(.5,rel=1e-13)


def test_missing_boundary_inputs_and_incompatible_physics_refuse(store):
    p=setup(store,'convection')
    p.study.internal_heat_w=1
    assert any('source' in error for error in validate_study(p))
    p.study.type='modal'
    assert any('thermal boundary' in error for error in validate_study(p))
    for field,value in [('coefficient_w_m2_k',0),('ambient_temperature_c',-273.15),('source',' ')]:
        d={'face':'part:xmax','coefficient_w_m2_k':25,'ambient_temperature_c':20,'source':'fixture'};d[field]=value
        with pytest.raises(ValueError):ConvectionBoundary(**d)
    with pytest.raises(ValueError):RadiationBoundary(face='part:xmax',emissivity=1.1,ambient_temperature_c=20,source='fixture')


def test_distinct_selectors_on_the_same_physical_exchange_face_refuse(beam):
    mesh=tetra();triangle=mesh['surfaces']['radiation_0']
    mesh['surfaces']['convection_0']=triangle;mesh['surfaces']['convection_1']=triangle
    beam.study.convection=[ConvectionBoundary(face=name,coefficient_w_m2_k=25,ambient_temperature_c=20,source='fixture') for name in ['semantic-face','strict-face']]
    with pytest.raises(ValueError,match='Overlapping convection'):exchange_deck(mesh,beam.study)


@pytest.mark.parametrize('mode,mesher',[('convection','netgen'),('radiation','gmsh'),('combined','netgen'),
                                        ('cold','gmsh'),('internal','netgen'),('thermostat','gmsh')])
def test_real_thermal_exchange_against_one_dimensional_energy_and_temperature(store,mode,mesher):
    p=setup(store,mode,mesher)
    result=simulate(store,p)
    area=40e-6;power=.002 if mode=='cold' else .02;ambient=-50 if mode=='cold' else 20
    def reject(t):
        convection=25*area*(t-20) if p.study.convection else 0
        radiation=.8*5.670374419e-8*area*((t+273.15)**4-(ambient+273.15)**4) if p.study.radiation else 0
        return convection+radiation-power
    end=20 if mode=='thermostat' else brentq(reject,ambient,1000)
    rise=power*.08/(20*area)
    expected_max=end+rise/(2 if mode in ('internal','thermostat') else 1)
    expected_probe=end+rise*(.375 if mode in ('internal','thermostat') else .5)
    assert result['state']=='passed',json.dumps({k:v for k,v in result.items() if k in ('warnings','heat_balance_error','max_temperature_c','prescribed_heat_w','thermal_exchange')},indent=2)
    assert result['max_temperature_c']==pytest.approx(expected_max,abs=.02)
    assert result['probe_temperature_c']==pytest.approx(expected_probe,abs=.02)
    assert result['input_heat_w']==pytest.approx(power,rel=1e-10)
    assert result['heat_balance_error']<.002
    if mode=='cold':
        assert result['max_temperature_c']<0
        assert result['findings'][0]['status']=='passed'
    if mode=='thermostat':assert result['prescribed_heat_w']==pytest.approx(-power,rel=.002)
    else:assert result['heat_rejected_w']==pytest.approx(power,rel=.002)
    report=Path('/tmp/oxygen-thermal-exchange-evidence');report.mkdir(exist_ok=True)
    (report/f'{mode}-{mesher}.json').write_text(json.dumps({'result':result,'expected_max_c':expected_max,'expected_probe_c':expected_probe},indent=2))


def test_prescribed_face_exchange_reports_actual_external_thermostat_power(store):
    p=setup(store,'convection');p.study.heat_sources=[]
    p.study.temperatures=[TemperatureBoundary(face='part:xmin',temperature_c=80),TemperatureBoundary(face='part:xmax',temperature_c=80)]
    result=simulate(store,p)
    assert result['state']=='passed',result['warnings']
    assert result['min_temperature_c']==pytest.approx(80,abs=1e-4)
    assert result['max_temperature_c']==pytest.approx(80,abs=1e-4)
    assert result['prescribed_heat_w']==pytest.approx(25*40e-6*60,rel=.001)
    assert result['heat_balance_error']<.001


def test_internal_heating_convection_transfers_to_thermal_expansion(store):
    p=setup(store,'internal');p.study.type='thermal_stress';p.study.fixed_faces=['part:xmin']
    p.study.reference_temperature_c=20;p.material.thermal_expansion_per_k=12e-6
    p.study.probe_point_mm=(80,0,0)
    result=simulate(store,p)
    mean_rise=20+2/3
    assert result['state']=='passed',result['warnings']
    assert result['probe_displacement_mm']==pytest.approx(12e-6*80*mean_rise,rel=.02)
    assert result['heat_balance_error']<.002


def test_finished_solver_with_incomplete_temperature_observation_refuses(store,monkeypatch):
    import o3d.analysis as analysis
    real=analysis.run_solver
    def altered(*args,**kwargs):
        boundary,frames=real(*args,**kwargs)
        for f in frames:
            if f['name']=='NDTEMP':f['time']=.5
        return boundary,frames
    monkeypatch.setattr(analysis,'run_solver',altered)
    with pytest.raises(RuntimeError,match='completed steady load step'):simulate(store,setup(store,'convection'))


def test_temperature_limits_allow_celsius_below_zero_without_allowing_invalid_mechanical_limits():
    assert Requirement(id='cold',kind='max_temperature',value=-20).value==-20
    with pytest.raises(ValueError,match='absolute zero'):Requirement(id='invalid',kind='max_temperature',value=-274)
    for kind in ['max_mass','max_dimension','min_clearance','max_displacement','min_frequency','min_buckling_factor','max_stress']:
        with pytest.raises(ValueError,match='positive'):Requirement(id='invalid',kind=kind,value=0)
