"""Storage faults must not leave dead workers advertised as running.

The native build case runs the real worker; other cases inject only lifecycle/IO
faults and make no geometry or physics qualification claim. No disk is filled.
"""
import errno
import hashlib
import json
from pathlib import Path
from threading import Event
import time
import uuid

from fastapi.testclient import TestClient
import pytest

from o3d.api import create_app
from o3d import jobs as module
from o3d.jobs import Jobs
from o3d.model import Job
from o3d.solver import Cancelled
from o3d.store import now


def terminal(client, identifier, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/jobs/' + identifier)
        assert response.status_code == 200, response.text
        value = response.json()
        if value['state'] not in {'queued', 'running'}:
            return value
        time.sleep(.025)
    pytest.fail('Worker stopped but its job never became terminal')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_native_worker_final_record_failure_preserves_outputs_and_recovers(store, monkeypatch):
    before = digest(store.root/'o3d.json')
    write = module.write_json
    run = module.run_process
    failure = {'enabled':False}
    outputs = {}
    def completed_worker(*args, **kwargs):
        run(*args, **kwargs)
        directory = Path(args[1])
        result = json.loads((directory/'result.json').read_text())
        assert result['volume_mm3'] > 0
        outputs['result'] = result
        outputs['hashes'] = {name:digest(store.artifact(path)) for name,path in result['artifacts'].items()}
        failure['enabled'] = True
    def full_status_disk(path, value):
        if failure['enabled'] and path.name in {'job.json', 'summary.json'}:
            raise OSError(errno.ENOSPC, 'Injected full status storage')
        return write(path, value)
    monkeypatch.setattr(module, 'run_process', completed_worker)
    monkeypatch.setattr(module, 'write_json', full_status_disk)
    with TestClient(create_app(store.root, project_windows=False)) as client:
        response = client.post('/api/jobs/build')
        assert response.status_code == 200, response.text
        identifier = response.json()['id']
        job = terminal(client, identifier, timeout=60)
        assert failure['enabled']
        assert job['state'] == 'failed' and job['result'] is None
        assert job['finished_at'] and 'held in memory' in job['message']
        assert 'inspect the project' in job['message']
        jobs = client.app.state.jobs
        assert identifier not in jobs.cancellations
        # This is the actual stale on-disk record that caused the reported bug.
        disk = jobs.path(identifier)/'job.json'
        assert json.loads(disk.read_text())['state'] == 'running'
        original_read = Path.read_text
        def no_stale_detail(path, *args, **kwargs):
            if path == disk:raise AssertionError('Polling read the stale running detail')
            return original_read(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_text', no_stale_detail)
            assert client.get('/api/jobs/'+identifier).json()['state'] == 'failed'
            listed = client.get('/api/state').json()['jobs']
            assert next(row for row in listed if row['id']==identifier)['state'] == 'failed'
        assert digest(store.root/'o3d.json') == before
        for name,path in outputs['result']['artifacts'].items():
            assert digest(store.artifact(path)) == outputs['hashes'][name]
    # Opening the app under the same fault must not launch the worker again.
    monkeypatch.setattr(module, 'run_process', lambda *a, **kw:pytest.fail('Recovery reran a worker'))
    recovered = Jobs(store)
    try:
        assert recovered.get(identifier).state == 'failed'
        assert 'Application restarted' in recovered.get(identifier).message
        assert recovered.list()[0]['state'] == 'failed'
    finally:recovered.close()
    # Once storage returns, normal restart persists the interrupted failure.
    failure['enabled'] = False
    repaired = Jobs(store)
    try:
        assert repaired.get(identifier).state == 'failed'
        assert not repaired.volatile_jobs and not repaired.volatile_summaries
        assert json.loads(disk.read_text())['state'] == 'failed'
    finally:repaired.close()
    assert digest(store.root/'o3d.json') == before


def test_summary_failure_preserves_durable_success_and_compact_polling(store, monkeypatch):
    jobs = Jobs(store)
    write = module.write_json
    def readonly_summary(path, value):
        if path.name == 'summary.json':raise OSError(errno.EROFS, 'Injected read-only derived cache')
        return write(path, value)
    monkeypatch.setattr(module, 'write_json', readonly_summary)
    job = Job(id=uuid.uuid4().hex, kind='simulate', revision=1, state='succeeded',
              created_at=now(), finished_at=now(), result={'dense':[1.]*100000,'artifact':'existing-result.json'})
    try:
        jobs._save(job)
        assert jobs.get(job.id).result == job.result
        read = Path.read_text
        detail = jobs.path(job.id)/'job.json'
        def forbid_dense(path, *args, **kwargs):
            if path == detail:raise AssertionError('Repeated polling reread dense result')
            return read(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_text', forbid_dense)
            for _ in range(2):
                value = jobs.list()[0]
                assert value['state'] == 'succeeded'
                assert value['result'] == {'artifact':'existing-result.json'}
                assert len(json.dumps(value)) < 1000
    finally:jobs.close()
    # The full result was persisted, so restart retains success even read-only.
    recovered = Jobs(store)
    try:
        assert recovered.get(job.id).result == job.result
        assert recovered.list()[0]['state'] == 'succeeded'
    finally:recovered.close()


@pytest.mark.parametrize('target', ['job.json','request.json'])
@pytest.mark.parametrize('endpoint', ['build','domain'])
@pytest.mark.parametrize('error_number,status', [(errno.ENOSPC,507),(errno.EDQUOT,507),(errno.EROFS,503)])
def test_submission_storage_failure_is_visible_and_dispatches_nothing(store, monkeypatch, target, endpoint, error_number, status):
    before = digest(store.root/'o3d.json')
    write = module.write_json
    def fail_storage(path, value):
        if path.name == target:raise OSError(error_number, 'Injected submission storage fault')
        return write(path, value)
    monkeypatch.setattr(module, 'write_json', fail_storage)
    monkeypatch.setattr(module, 'run_process', lambda *a, **kw:pytest.fail('Failed submission launched a worker'))
    with TestClient(create_app(store.root, project_windows=False)) as client:
        if endpoint=='build':response=client.post('/api/jobs/build')
        else:response=client.post('/api/domains/simulation/run',json={'operation':'solve','input':{},'actor':'human','expected_revision':1})
        assert response.status_code == status, response.text
        identifier = response.json()['job_id']
        job = client.get('/api/jobs/'+identifier).json()
        assert job['state']=='failed' and job['result'] is None and job['finished_at']
        assert next(j for j in client.get('/api/state').json()['jobs'] if j['id']==identifier)['state']=='failed'
        assert identifier not in client.app.state.jobs.cancellations
    assert digest(store.root/'o3d.json') == before


def test_failure_before_running_status_is_saved_never_launches_worker(store, monkeypatch):
    write = module.write_json
    def no_transition(path, value):
        if path.name == 'job.json' and value['state']!='queued':raise OSError(errno.ENOSPC,'Injected transition fault')
        return write(path,value)
    monkeypatch.setattr(module,'write_json',no_transition)
    monkeypatch.setattr(module,'run_process',lambda *a,**kw:pytest.fail('Worker launched without running record'))
    with TestClient(create_app(store.root,project_windows=False)) as client:
        response=client.post('/api/jobs/build');assert response.status_code==200
        identifier=response.json()['id'];job=terminal(client,identifier)
        assert job['state']=='failed' and job['result'] is None
        assert identifier not in client.app.state.jobs.cancellations


def test_cancelled_worker_with_failed_status_write_releases_cancellation(store, monkeypatch):
    started=Event();stopped=Event();write=module.write_json
    def worker(command,directory,log,cancel,**kwargs):
        started.set()
        assert cancel.wait(5),'Cancellation was not delivered'
        stopped.set();raise Cancelled('Injected cancelled lifecycle worker; no numerical result')
    def no_final_record(path,value):
        if stopped.is_set() and path.name in {'job.json','summary.json'}:raise OSError(errno.ENOSPC,'Injected final status fault')
        return write(path,value)
    monkeypatch.setattr(module,'run_process',worker);monkeypatch.setattr(module,'write_json',no_final_record)
    with TestClient(create_app(store.root,project_windows=False)) as client:
        response=client.post('/api/jobs/build');identifier=response.json()['id'];assert started.wait(5)
        assert client.post('/api/jobs/'+identifier+'/cancel').status_code==200
        job=terminal(client,identifier)
        assert stopped.is_set() and job['state']=='cancelled' and job['result'] is None
        assert 'held in memory' in job['message']
        assert identifier not in client.app.state.jobs.cancellations


def test_failed_status_publication_does_not_undo_committed_domain_revision(store, monkeypatch):
    # A data-only proposal uses the real guarded finalizer/Store transaction.
    # It is not a synthetic geometry or numerical passing result.
    write=module.write_json
    def proposal(args,*unused,**kwargs):
        write(Path(args[-1]),{'changed':True,'data':{'schema_version':1}})
    def no_terminal_status(path,value):
        if path.name=='job.json' and value['state'] in {'succeeded','failed'}:
            raise OSError(errno.ENOSPC,'Injected terminal publication fault')
        return write(path,value)
    monkeypatch.setattr(module,'run_process',proposal);monkeypatch.setattr(module,'write_json',no_terminal_status)
    with TestClient(create_app(store.root,project_windows=False)) as client:
        body={'operation':'reopen','input':{},'actor':'human','expected_revision':1}
        response=client.post('/api/domains/drawings/run',json=body);assert response.status_code==200
        identifier=response.json()['id'];job=terminal(client,identifier)
        assert job['state']=='failed' and job['result'] is None
        project=client.get('/api/project').json()
        assert project['revision']==2 and project['domains']['drawings']['schema_version']==1
        assert len(store.history())==2
        # Blindly retrying the stale revision cannot duplicate the edit.
        assert client.post('/api/domains/drawings/run',json=body).status_code==409
        assert client.get('/api/project').json()['revision']==2


@pytest.mark.parametrize('broken', ['missing','invalid-json','wrong-identity'])
def test_derived_summary_repairs_once_even_while_readonly(store, monkeypatch, broken):
    jobs=Jobs(store);write=module.write_json
    job=Job(id=uuid.uuid4().hex,kind='check',revision=1,state='succeeded',created_at=now(),result={'value':3})
    try:
        jobs._save(job);summary=jobs.path(job.id)/'summary.json'
        if broken=='missing':summary.unlink()
        elif broken=='invalid-json':summary.write_text('null')
        else:summary.write_text(json.dumps(jobs._summary(job)|{'id':'different'}))
        def readonly(path,value):
            if path==summary:raise OSError(errno.EROFS,'Injected derived-cache write failure')
            return write(path,value)
        monkeypatch.setattr(module,'write_json',readonly)
        assert jobs.list()[0]['id']==job.id
        read=Path.read_text
        def no_repeat(path,*args,**kwargs):
            if path.name=='job.json':raise AssertionError('Polling repeated detail fallback')
            return read(path,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path,'read_text',no_repeat)
            assert jobs.list()[0]['state']=='succeeded'
    finally:jobs.close()
