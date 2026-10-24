"""Dense real CAD results must not be repeated in background job polling."""
import io
import json
from pathlib import Path
import uuid
import zipfile

from fastapi.testclient import TestClient

from o3d.api import create_app
from o3d.artifacts import export_bundle
from o3d.geometry import build
from o3d.jobs import Jobs
from o3d.model import Job, Project
from o3d.store import Store, now


def test_dense_build_history_has_compact_polling_and_intact_detail(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    store = Store(tmp_path / "project")
    project = Project.model_validate_json((root / "examples/bracket.json").read_text())
    store.init(project)
    geometry = build(store, project)
    assert len(json.dumps(geometry)) > 100_000  # Actual native tessellation, not a stub blob.
    exported = export_bundle(store, project)
    app = create_app(store.root, project_windows=False)
    with TestClient(app) as client:
        jobs = app.state.jobs
        ids = []
        for _ in range(6):
            job = Job(id=uuid.uuid4().hex, kind="build", revision=project.revision,
                      state="succeeded", created_at=now(), message="Completed", result=geometry)
            jobs._save(job)
            ids.append(job.id)
        export_job = Job(id=uuid.uuid4().hex, kind="export", revision=project.revision,
                         state="succeeded", created_at=now(), message="Completed", result=exported)
        jobs._save(export_job)
        original_read = Path.read_text

        def no_dense_poll_reads(path, *args, **kwargs):
            if path.name == "job.json" and path.parent.parent == store.cache / "jobs":
                raise AssertionError("Polling reread a dense completed job result")
            return original_read(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "read_text", no_dense_poll_reads)
            response = client.get("/api/state")
            assert response.status_code == 200
            state = response.json()
            assert len(json.dumps(state["jobs"])) < 12_000
            assert all("faces" not in (job["result"] or {}) for job in state["jobs"])
            assert state["build"]["faces"] == geometry["faces"]
            assert len(jobs.list()) == 7  # Event-stream polling uses the same summaries.

        detail = client.get(f"/api/jobs/{ids[0]}").json()
        assert detail["result"] == geometry
        saved_export = next(job for job in state["jobs"] if job["id"] == export_job.id)
        artifact = saved_export["result"]["artifact"]
        download = client.get("/api/artifacts/" + artifact)
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            source = json.loads(archive.read("o3d.json"))
            assert source["name"] == project.name
            assert source["features"] == project.model_dump(mode="json")["features"]

    # Older caches gain compact sidecars once when the application opens.
    summary = store.cache / "jobs" / ids[0] / "summary.json"
    summary.unlink()
    recovered = Jobs(store)
    try:
        assert summary.is_file()
        assert recovered.get(ids[0]).result == geometry
        recovered_job = next(job for job in recovered.list() if job["id"] == ids[0])
        assert "faces" not in recovered_job["result"]
    finally:
        recovered.close()
