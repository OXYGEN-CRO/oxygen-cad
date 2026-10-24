"""Shared workspace acceptance: revision conflicts, process boundaries and identities."""
import json
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from o3d.api import create_app
from o3d.domains import finalize, save_domain
from o3d.identity import Identity, principal_context
from o3d.jobs import Jobs
from o3d.model import Command, Parameter
from o3d.store import Conflict, Store, write_json


def completed(jobs, job):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = jobs.get(job.id)
        if result.state not in {"running", "queued"}:
            return result
        time.sleep(.02)
    raise AssertionError("Workspace worker failed to finish")


def test_domain_edit_undo_and_stale_worker_never_overwrites_new_edit(store):
    snapshot = store.load()
    changed = save_domain(store, "drawings", {"schema_version": 1}, 1, "human")
    assert changed.revision == 2 and changed.domains["drawings"]["schema_version"] == 1
    with pytest.raises(Conflict):
        finalize(store, snapshot, "drawings", {"changed": True, "data": {"schema_version": 1}})
    assert store.load() == changed
    report = finalize(store, snapshot, "drawings", {"artifact": "drawings/old.svg"})
    assert report["stale"] is True and report["source_revision"] == 1
    undone = store.apply(Command(expected_revision=2, actor="human", action="undo"))
    assert undone.revision == 3 and "drawings" not in undone.domains


def test_workspace_api_and_project_windows_have_independent_stores(store):
    other = Store(store.root.parent / "other")
    other.init(store.load().model_copy(update={"name": "Other document"}))
    with TestClient(create_app(store.root)) as client:
        assert client.get("/api/domains").status_code == 200
        reply = client.put("/api/domains/drawings", json={"expected_revision": 1, "data": {"schema_version": 1}})
        assert reply.status_code == 200 and reply.json()["project"]["revision"] == 2
        assert client.put("/api/domains/drawings", json={"expected_revision": 1, "data": {}}).status_code == 409
        scoped = client.get("/p/other/api/project")
        assert scoped.status_code == 200 and scoped.json()["name"] == "Other document"
        edit = scoped.json() | {"name": "Other window edit"}
        reply = client.post("/p/other/api/commands", json={"expected_revision": 1, "actor": "human", "project": edit})
        assert reply.status_code == 200
        assert other.load().name == "Other window edit"
        assert store.load().revision == 2
        assert client.get("/p/missing/api/project").status_code == 404


def test_domain_job_runs_in_real_worker_with_snapshot_provenance(store):
    jobs = Jobs(store)
    try:
        job = jobs.submit_domain("drawings", "reopen", {"data": {"schema_version": 1}}, 1)
        result = completed(jobs, job)
        assert result.state == "succeeded", result.message
        assert result.result["data"]["schema_version"] == 1
        assert result.result["source_revision"] == 1 and not result.result["stale"]
        assert store.load().revision == 1
    finally:
        jobs.close()


@pytest.mark.parametrize("scenario", ["locked", "revoked", "audit"])
def test_background_publication_restores_agent_identity_and_rechecks_revocation(store, monkeypatch, scenario):
    initial = store.load()
    initial.parameters["span"] = Parameter(value=80, locked=True)
    initial.features[-1].distance = "$span"
    initial = store.apply(Command(expected_revision=initial.revision, actor="human", project=initial))
    identity = Identity(store)
    owner = identity.enable("Owner")
    administrator = identity.authenticate(owner["token"])
    issued = identity.issue(administrator, "Coding agent", "editor", "agent")
    agent = identity.authenticate(issued["token"])
    entered, release = Event(), Event()

    def computed_worker(args, *_args, **_kwargs):
        request = json.loads(args[-2].read_text())
        proposal = request["project"]
        if scenario == "locked":
            name = next(k for k, value in proposal["parameters"].items() if value["locked"])
            proposal["parameters"][name]["value"] += 1
            result = {"changed": True, "project": proposal}
        else:
            result = {"changed": True, "data": {"schema_version": 1}}
        entered.set()
        assert release.wait(5)
        write_json(args[-1], result)

    monkeypatch.setattr("o3d.jobs.run_process", computed_worker)
    jobs = Jobs(store)
    context = principal_context.set(agent)
    try:
        # A forged human label must not escape agent-owned lock enforcement.
        job = jobs.submit_domain("drawings", "reopen", {}, initial.revision, actor="human")
    finally:
        principal_context.reset(context)
    try:
        assert entered.wait(5)
        request = json.loads((jobs.path(job.id) / "request.json").read_text())
        assert request["workspace"]["actor"] == "agent"
        assert "token" not in json.dumps(request)
        if scenario == "revoked":
            identity.revoke(administrator, agent.id)
        release.set()
        result = completed(jobs, job)
        if scenario in {"locked", "revoked"}:
            assert result.state == "failed", result.message
            assert store.load().revision == initial.revision
        else:
            assert result.state == "succeeded", result.message
            assert store.load().revision == initial.revision + 1
            assert store.history()[-1]["principal"]["id"] == agent.id
            assert store.history()[-1]["actor"] == "agent"
    finally:
        release.set()
        jobs.close()
