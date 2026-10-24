import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from .model import Job
from .solver import Cancelled, run_process
from .store import now, write_json


class Jobs:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.cancellations = {}
        self.mutex = Lock()
        for path in (store.cache / "jobs").glob("*/job.json"):
            job = Job.model_validate_json(path.read_text())
            if job.state in ("queued", "running"):
                job.state = "failed"
                job.message = "Application restarted before job completed; resubmit to retry"
                job.finished_at = now()
                write_json(path, job.model_dump())

    def path(self, job_id):
        if not job_id.isalnum() or len(job_id) != 32:
            raise ValueError("Invalid job ID")
        return self.store.cache / "jobs" / job_id

    def submit(self, kind):
        project = self.store.load()
        job = Job(id=uuid.uuid4().hex, kind=kind, revision=project.revision, created_at=now(),
                  study_id=project.active_study_id if kind == "simulate" else None)
        path = self.path(job.id)
        write_json(path / "job.json", job.model_dump())
        write_json(path / "request.json", {"root": str(self.store.root), "kind": kind, "project": project.model_dump(mode="json")})
        self.cancellations[job.id] = Event()
        self.pool.submit(self._execute, job)
        return job

    def get(self, job_id):
        return Job.model_validate_json((self.path(job_id) / "job.json").read_text())

    def list(self):
        return sorted([Job.model_validate_json(p.read_text()).model_dump() for p in (self.store.cache / "jobs").glob("*/job.json")], key=lambda v: v["created_at"], reverse=True)[:30]

    def cancel(self, job_id):
        job = self.get(job_id)
        if job.id in self.cancellations:
            self.cancellations[job.id].set()
        return job

    def _execute(self, job):
        path = self.path(job.id)
        cancel = self.cancellations[job.id]
        try:
            if cancel.is_set():
                raise Cancelled("Job cancelled before starting")
            job.state, job.started_at = "running", now()
            write_json(path / "job.json", job.model_dump())
            run_process([sys.executable, "-m", "o3d.worker", path / "request.json", path / "result.json"], path, path / "worker.log", cancel, timeout=900 if job.kind == "simulate" else 180)
            if cancel.is_set():
                raise Cancelled("Job cancelled")
            job.result = json.loads((path / "result.json").read_text())
            job.state, job.message = "succeeded", "Completed"
        except Cancelled as error:
            job.state, job.message = "cancelled", str(error)
        except Exception as error:
            job.state, job.message = "failed", str(error)
        finally:
            job.finished_at = now()
            write_json(path / "job.json", job.model_dump())
            self.cancellations.pop(job.id, None)

    def close(self):
        for event in list(self.cancellations.values()):
            event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
