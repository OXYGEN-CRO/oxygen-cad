import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from .model import Job, Project
from .store import Conflict
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
                self._save(job)
            else:
                self._write_summary(job)

    @staticmethod
    def _summary(job):
        """Polling metadata; dense results remain available through get()."""
        value = job.model_dump(exclude={"result"})
        result = job.result
        value["result"] = None if result is None else {
            key: item for key, item in result.items()
            if item is None or isinstance(item, (bool, int, float))
            or (isinstance(item, str) and len(item) <= 4096)
        }
        if result is not None:
            for key in ("artifacts", "artifact_hashes"):
                if isinstance(result.get(key), dict):
                    value["result"][key] = {
                        name: path for name, path in result[key].items()
                        if isinstance(path, str) and len(path) <= 4096
                    }
        return value

    def _write_summary(self, job):
        write_json(self.path(job.id) / "summary.json", self._summary(job))

    def _save(self, job):
        # Publish detail before advertising its completed state to polling clients.
        write_json(self.path(job.id) / "job.json", job.model_dump())
        self._write_summary(job)

    def path(self, job_id):
        if not job_id.isalnum() or len(job_id) != 32:
            raise ValueError("Invalid job ID")
        return self.store.cache / "jobs" / job_id

    def submit(self, kind, *, request=None, expected_revision=None):
        project = self.store.load()
        if expected_revision is not None and project.revision != expected_revision:
            raise Conflict("Project changed; inspect before submitting an operation")
        job = Job(id=uuid.uuid4().hex, kind=kind, revision=project.revision, created_at=now(),
                  study_id=project.active_study_id if kind == "simulate" else None)
        path = self.path(job.id)
        self._save(job)
        write_json(path / "request.json", {"root": str(self.store.root), "kind": kind, "project": project.model_dump(mode="json"), "workspace": request})
        self.cancellations[job.id] = Event()
        self.pool.submit(self._execute, job)
        return job

    def submit_domain(self, domain, operation, payload, expected_revision, actor="agent"):
        from .domains import module_for
        from .identity import effective_actor, principal_context
        module_for(domain)
        principal = principal_context.get()
        return self.submit("domain", expected_revision=expected_revision,
                           request={"domain": domain, "operation": operation,
                                    "input": payload, "actor": effective_actor(actor),
                                    "principal": principal.public() if principal else None})

    def get(self, job_id):
        return Job.model_validate_json((self.path(job_id) / "job.json").read_text())

    def list(self):
        values = []
        for path in (self.store.cache / "jobs").glob("*/job.json"):
            summary = path.with_name("summary.json")
            # Missing during an initial write or for older cached jobs.
            values.append(json.loads(summary.read_text()) if summary.exists()
                          else self._summary(Job.model_validate_json(path.read_text())))
        return sorted(values, key=lambda value: value["created_at"], reverse=True)[:30]

    def cancel(self, job_id):
        job = self.get(job_id)
        with self.mutex:
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
            self._save(job)
            run_process([sys.executable, "-m", "o3d.worker", path / "request.json", path / "result.json"], path, path / "worker.log", cancel, timeout=1800 if job.kind == "domain" else (900 if job.kind == "simulate" else 180))
            with self.mutex:
                if cancel.is_set():
                    raise Cancelled("Job cancelled")
                job.result = json.loads((path / "result.json").read_text())
                if job.kind == "domain":
                    from .domains import finalize
                    request = json.loads((path / "request.json").read_text())
                    workspace = request["workspace"]
                    from .job_identity import job_identity
                    with job_identity(self.store, workspace.get("principal")):
                        job.result = finalize(self.store, Project.model_validate(request["project"]),
                                              workspace["domain"], job.result, workspace["actor"])
                job.state, job.message = "succeeded", "Completed"
        except Cancelled as error:
            job.state, job.message = "cancelled", str(error)
        except Exception as error:
            job.state, job.message = "failed", str(error)
        finally:
            job.finished_at = now()
            self._save(job)
            self.cancellations.pop(job.id, None)

    def close(self):
        for event in list(self.cancellations.values()):
            event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
