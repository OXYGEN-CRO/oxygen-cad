import json
from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from o3d.api import create_app
from o3d.cli import app
from o3d import joints
from o3d.joint_models import Quantity, JointRequirement, ActuatorContract, Rating, Mount
from o3d.model import Command, Project
from o3d.store import Store, Conflict

ROOT = Path(__file__).resolve().parents[1]

def configuration():
    return json.loads((ROOT/'src/o3d/data/joint-example.json').read_text())

@pytest.fixture
def configured(store):
    data = configuration()
    data['joint_requirements'][0]['static_model_confirmed'] = True
    joints.configure(store, data, 1, 'human')
    return store

def task_and_part(store):
    brief=store.load().engineering_brief
    return brief.joint_requirements[0], brief.components[0]

def checks(report):return {r['code']:r for r in report['checks']}

@pytest.mark.parametrize('value,unit',[(float('nan'),'kg'),(float('inf'),'kg'),(True,'kg'),('1','kg'),(-1,'mm'),(1,'invented')])
def test_invalid_quantities_fail_instead_of_coercing(value,unit):
    with pytest.raises(ValidationError):Quantity(value=value,unit=unit)

def test_cold_temperature_valid_and_wrong_dimensions_rejected():
    assert Quantity(value=-10,unit='degC').si('temperature')==-10
    with pytest.raises(ValidationError):JointRequirement(id='x',statement='Task',provenance='test',payload_mass={'value':2,'unit':'mm'})

def test_official_library_variants_are_distinct_and_not_continuous():
    records=joints.library()
    assert len(records)==12 and len({c.id for c in records})==12
    t=next(c for c in records if c.mpn=='XM430-W350-T')
    r=next(c for c in records if c.mpn=='XM430-W350-R')
    assert t.bus=='TTL' and r.bus=='RS485' and t.pinout!=r.pinout
    assert t.ratings[0].torque.value==4.1 and t.ratings[0].kind=='stall'
    assert all(r.kind=='stall' for c in records for r in c.ratings)
    assert all(c.mount is None and c.catalog_part_id is None for c in records)

def test_independent_static_reference_and_exact_unit_conversion(configured):
    task,part=task_and_part(configured)
    report=joints.compare(task,part,configured.load())
    assert checks(report)['static_load']['value']==pytest.approx(3.5316)
    task.payload_mass=Quantity(value=1000,unit='g')
    task.payload_arm=Quantity(value=.30,unit='m')
    task.link_mass=Quantity(value=.4,unit='kg')
    task.link_com=Quantity(value=.15,unit='m')
    assert checks(joints.compare(task,part,configured.load()))['static_load']['value']==pytest.approx(3.5316)

def test_stall_never_becomes_sustained_or_fit_pass(configured):
    task,part=task_and_part(configured)
    report=joints.compare(task,part,configured.load())
    assert checks(report)['stall_screen']['status']=='passed'
    assert checks(report)['sustained']['status']=='unknown'
    assert checks(report)['mount']['status']=='unknown'
    assert report['status']=='unknown'

def test_longer_arm_fails_nominal_screen_and_bus_variant_is_rejected(configured):
    task,part=task_and_part(configured)
    task.payload_arm=Quantity(value=400,unit='mm')
    report=joints.compare(task,part,configured.load())
    assert checks(report)['static_load']['value']==pytest.approx(4.5126)
    assert checks(report)['stall_screen']['status']=='failed' and report['status']=='failed'
    other=configured.load().engineering_brief.components[1]
    assert checks(joints.compare(task,other,configured.load()))['bus']['status']=='failed'

def test_full_supply_range_must_be_within_limits(configured):
    task,part=task_and_part(configured)
    task.supply_min=Quantity(value=9,unit='V'); task.supply_max=Quantity(value=12,unit='V')
    report=checks(joints.compare(task,part,configured.load()))
    assert report['supply']['status']=='failed' # Ranges overlap; that is insufficient.
    assert report['stall_screen']['status']=='unknown' # Do not extrapolate 12 V to sagging supply.

@pytest.mark.parametrize('field',[ 'payload_mass','payload_arm','link_mass','link_com'])
def test_missing_required_load_inputs_do_not_become_zero(configured,field):
    task,part=task_and_part(configured);setattr(task,field,None)
    report=checks(joints.compare(task,part,configured.load()))
    assert report['static_load']['status']=='unknown' and report['sustained']['status']=='unknown'

def test_unconfirmed_model_missing_subject_and_parameter_are_explicit(configured):
    task,part=task_and_part(configured);task.static_model_confirmed=False
    task.subject='deleted';task.parameter_refs=['gone']
    report=checks(joints.compare(task,part,configured.load()))
    assert report['static_load']['status']=='unknown'
    assert report['subject']['status']=='unknown' and report['parameter']['status']=='unknown'

def test_missing_conflicting_or_wrong_voltage_ratings_do_not_pass(configured):
    task,part=task_and_part(configured)
    part.ratings.append(part.ratings[0].model_copy(update={'torque':Quantity(value=5,unit='N*m')}))
    assert checks(joints.compare(task,part,configured.load()))['stall_screen']['status']=='unknown'
    part.ratings[0].voltage=Quantity(value=14.8,unit='V');part.ratings=part.ratings[:1]
    assert checks(joints.compare(task,part,configured.load()))['stall_screen']['status']=='unknown'

def test_sustained_evidence_applies_only_inside_explicit_domain(configured):
    task,part=task_and_part(configured)
    # Deliberately synthetic acceptance data, never part of the vendor seed.
    rating=Rating(kind='hold_test',torque={'value':4,'unit':'N*m'},voltage={'value':12,'unit':'V'},
                  source_id='manual',duration={'value':60,'unit':'s'},ambient_min={'value':20,'unit':'degC'},ambient_max={'value':25,'unit':'degC'},
                  cooling=task.cooling,duty_cycle_max=1)
    part.ratings.append(rating)
    assert checks(joints.compare(task,part,configured.load()))['sustained']['status']=='passed'
    task.hold_duration=Quantity(value=61,unit='s')
    assert checks(joints.compare(task,part,configured.load()))['sustained']['status']=='unknown'
    task.hold_duration=Quantity(value=60,unit='s');task.ambient=Quantity(value=30,unit='degC')
    assert checks(joints.compare(task,part,configured.load()))['sustained']['status']=='unknown'
    task.ambient=Quantity(value=25,unit='degC');task.cooling='different cooling'
    assert checks(joints.compare(task,part,configured.load()))['sustained']['status']=='unknown'

def test_ambiguous_sources_and_duplicate_contract_ids_rejected(configured):
    task,part=task_and_part(configured);raw=part.model_dump(mode='json')
    raw['ratings'][0]['source_id']='missing'
    with pytest.raises(ValidationError):ActuatorContract.model_validate(raw)
    data=configuration();data['components'].append(data['components'][0])
    with pytest.raises(ValidationError):joints.configure(configured,data,2)

def test_reopen_revision_conflict_immutable_history_and_staleness(configured):
    result=joints.evaluate(configured,2)
    assert result['freshness']=='current'
    key=result['input_key'];path=configured.cache/'joints'/f'{key}.json';first=path.read_bytes()
    assert Store(configured.root).load().engineering_brief.joint_requirements[0].id=='elbow_hold'
    with pytest.raises(Conflict):joints.configure(configured,{},1)
    with pytest.raises(Conflict):joints.evaluate(configured,1)
    project=configured.load();project.engineering_brief.joint_requirements[0].payload_arm.value=400
    configured.apply(Command(expected_revision=2,actor='human',project=project))
    stale=joints.status(Store(configured.root))
    assert stale['freshness']=='stale' and stale['report'] is None and stale['previous_report']['input_key']==key
    updated=joints.evaluate(configured,3)
    assert updated['report']['state']=='failed' and path.read_bytes()==first
    assert json.loads(path.read_text())['snapshot']['revision']==2


def test_joint_evaluation_completes_without_reentering_the_project_lock(configured):
    import os
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, '-c',
         'import sys; from o3d.store import Store; from o3d.joints import evaluate; print(evaluate(Store(sys.argv[1]), 2)["freshness"])',
         str(configured.root)],
        env=dict(os.environ), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'current'

def test_geometry_and_component_edits_stale_but_name_change_does_not(configured):
    joints.evaluate(configured,2)
    project=configured.load();project.name='Rename only'
    configured.apply(Command(expected_revision=2,actor='human',project=project))
    assert joints.status(configured)['freshness']=='current'
    project=configured.load();project.features[0].width=81
    configured.apply(Command(expected_revision=3,actor='human',project=project))
    assert joints.status(configured)['freshness']=='stale'
    joints.evaluate(configured,4)
    project=configured.load();project.engineering_brief.components[0].revision='changed source revision'
    configured.apply(Command(expected_revision=4,actor='human',project=project))
    assert joints.status(configured)['freshness']=='stale'

def test_agents_preserve_accepted_conditions_but_can_select_another_candidate(configured):
    project=configured.load();project.engineering_brief.joint_requirements[0].state='accepted'
    configured.apply(Command(expected_revision=2,actor='human',project=project))
    project=configured.load();project.engineering_brief.joint_requirements[0].payload_mass.value=.1
    with pytest.raises(Conflict,match='user-owned'):configured.apply(Command(expected_revision=3,actor='agent',project=project))
    project=configured.load();project.engineering_brief=None
    with pytest.raises(Conflict):configured.apply(Command(expected_revision=3,actor='agent',project=project))
    project=configured.load();project.engineering_brief.joint_requirements[0].selected_component_id='XM540-W270-T'
    configured.apply(Command(expected_revision=3,actor='agent',project=project))
    assert configured.load().revision==4

def test_missing_selection_never_uses_best_candidate_implicitly(configured):
    project=configured.load();project.engineering_brief.joint_requirements[0].selected_component_id='missing'
    assert joints.evaluate_project(project)['state']=='unknown'

def test_catalog_adapter_preserves_exact_identity_and_is_not_a_new_database():
    contract=joints.library()[0]
    row={'id':'00000000-0000-0000-0000-000000000001','manufacturer':contract.manufacturer,'mpn':contract.mpn,
         'attributes':{'engineering_contract':contract.model_dump(mode='json')}}
    assert joints.contract_from_catalog(row).catalog_part_id==row['id']
    row['mpn']='other variant'
    with pytest.raises(ValueError,match='identity'):joints.contract_from_catalog(row)

def test_http_and_cli_share_project_revisions_and_results(store,tmp_path):
    with TestClient(create_app(store.root)) as client:
        assert len(client.get('/api/joints/library').json()['components'])==12
        assert client.get('/api/joints/example').json()['joint_requirements'][0]['bindings']=={}
        data=configuration();data['joint_requirements'][0]['static_model_confirmed']=True
        response=client.post('/api/joints/configure',json={**data,'expected_revision':1,'actor':'human'})
        assert response.status_code==200 and response.json()['project']['revision']==2
        assert client.post('/api/joints/evaluate',json={'expected_revision':True}).status_code==422
        assert client.post('/api/joints/evaluate',json={'expected_revision':1}).status_code==409
        response=client.post('/api/joints/evaluate',json={'expected_revision':2})
        assert response.status_code==200 and response.json()['report']['state']=='unknown'
        runner=CliRunner()
        result=runner.invoke(app,['--project',str(store.root),'joint','status','--json'])
        assert result.exit_code==0,result.output
        assert json.loads(result.output)['input_key']==response.json()['input_key']
        result=runner.invoke(app,['--project',str(store.root),'joint','evaluate','--expected-revision','2','--json'])
        assert result.exit_code==2 and json.loads(result.output)['report']['state']=='unknown'
        assert client.post('/api/joints/configure',json={**data,'expected_revision':2,'unexpected':1}).status_code==422
        assert client.post('/api/joints/configure',content=b'x'*(1024*1024+1)).status_code==413
        # Source edits use the ordinary command path too.
        project=client.get('/api/project').json();project['engineering_brief']['joint_requirements'][0]['payload_arm']['value']=400
        assert client.post('/api/commands',json={'expected_revision':2,'actor':'human','project':project}).status_code==200
        assert client.get('/api/joints/status').json()['freshness']=='stale'
        assert client.get('/api/state').json()['joint_selection']['freshness']=='stale'


def test_native_parameter_bindings_drive_calculation_and_reject_unit_errors(configured):
    from o3d.model import Parameter
    project=configured.load()
    task,part=task_and_part(configured)
    task.payload_arm=None;task.bindings={'payload_arm':'reach'}
    project.parameters['reach']=Parameter(value=400,unit='mm')
    assert checks(joints.compare(task,part,project))['static_load']['value']==pytest.approx(4.5126)
    project.parameters['reach']=Parameter(value=400,unit='g')
    assert checks(joints.compare(task,part,project))['static_load']['status']=='unknown'
    task.payload_arm=Quantity(value=300,unit='mm')
    with pytest.raises(ValidationError):JointRequirement.model_validate(task.model_dump())


def test_modified_cached_verdict_cannot_become_current_evidence(configured):
    result=joints.evaluate(configured,2)
    path=configured.cache/'joints'/f"{result['input_key']}.json"
    record=json.loads(path.read_text());record['report']['state']='passed'
    path.write_text(json.dumps(record))
    assert joints.status(configured)['report'] is None
    assert joints.status(configured)['freshness']=='invalid'
    with pytest.raises(ValueError,match='integrity'):joints.evaluate(configured,2)
