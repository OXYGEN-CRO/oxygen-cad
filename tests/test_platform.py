"""Platform contracts exercised on disposable directories, without a CAD worker."""
import hashlib
import io
import json
import multiprocessing
import zipfile
from pathlib import Path

import pytest

from o3d.model import Command, Project
from o3d.projects import ProjectCatalog, portable_archive, restore_archive, migrate_document
from o3d.store import Conflict, Store


@pytest.mark.parametrize("boundary", ["journal", "snapshot", "source", "history", "undo"])
def test_interrupted_save_recovers_one_complete_transaction(store, boundary):
    proposed = store.load().model_copy(update={"name": "Acknowledged journal"})
    def crash(step):
        if step == boundary:
            raise OSError("injected process termination")
    store._fault = crash
    with pytest.raises(OSError):
        store.apply(Command(expected_revision=1, actor="human", project=proposed))
    reopened = Store(store.root)
    assert reopened.load().revision == 2
    assert reopened.load().name == "Acknowledged journal"
    assert [e["revision"] for e in reopened.history()] == [1, 2]
    assert json.loads((reopened.cache / "undo.json").read_text()) == {"undo": [1], "redo": []}
    assert not (reopened.cache / "transaction.json").exists()
    assert reopened.apply(Command(expected_revision=2, actor="human", action="undo")).revision == 3


def test_failed_journal_write_retains_previous_revision(store, monkeypatch):
    import o3d.recovery as recovery
    real_write = recovery.write_json
    def full_disk(path, value):
        if path.name == "transaction.json":
            raise OSError(28, "No space left on device")
        real_write(path, value)
    monkeypatch.setattr(recovery, "write_json", full_disk)
    with pytest.raises(OSError):
        store.apply(Command(expected_revision=1, project=store.load().model_copy(update={"name": "unsaved"})))
    assert store.load().revision == 1
    assert len(store.history()) == 1


def test_idempotent_retry_does_not_duplicate_edit(store):
    command = Command(expected_revision=1, actor="human", project=store.load().model_copy(update={"name": "retry"}))
    first = store.apply(command, request_id="request_1")
    second = store.apply(command, request_id="request_1")
    assert first == second and len(store.history()) == 2
    with pytest.raises(Conflict, match="different edit"):
        store.apply(command.model_copy(update={"summary": "changed"}), request_id="request_1")


def _concurrent_edit(root, name, queue):
    store = Store(root)
    project = store.load().model_copy(update={"name": name})
    try:
        queue.put(store.apply(Command(expected_revision=1, project=project)).revision)
    except Conflict:
        queue.put("conflict")


def test_two_processes_never_overwrite_stale_revision(store):
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    processes = [ctx.Process(target=_concurrent_edit, args=(store.root, str(i), queue)) for i in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(str(queue.get(timeout=1)) for _ in processes) == ["2", "conflict"]
    assert store.load().revision == 2


def test_portable_archive_restores_history_and_assets_without_credentials(store, tmp_path):
    content = b"ISO-10303-21;\nEND-ISO-10303-21;"
    store.import_step(content, "motor.step", 1)
    store.apply(Command(expected_revision=2, actor="human", project=store.load().model_copy(update={"name": "third"})))
    (store.cache / "identity.json").write_text('{"secret":"excluded"}')
    backup = portable_archive(store)
    result = restore_archive(Path(backup["path"]), tmp_path / "empty-machine" / "restored")
    restored = Store(result["path"])
    assert restored.load() == store.load()
    assert restored.history() == store.history()
    reference = restored.load().references[0]
    assert hashlib.sha256((restored.root / reference.file).read_bytes()).hexdigest() == reference.sha256
    assert not (restored.cache / "identity.json").exists()
    assert restored.apply(Command(expected_revision=3, actor="human", action="undo")).revision == 4
    assert restored.load().name != "third"


def test_archive_rejects_missing_dependency_and_tampering(store, tmp_path):
    store.import_step(b"ISO-10303-21;\n", "part.step", 1)
    backup = portable_archive(store)
    data = io.BytesIO()
    with zipfile.ZipFile(backup["path"]) as original, zipfile.ZipFile(data, "w") as corrupt:
        for name in original.namelist():
            corrupt.writestr(name, b"corrupt" if name == "o3d.json" else original.read(name))
    with pytest.raises(ValueError, match="checksum"):
        restore_archive(data.getvalue(), tmp_path / "target")
    assert not (tmp_path / "target").exists()
    (store.root / store.load().references[0].file).unlink()
    with pytest.raises(ValueError, match="missing"):
        portable_archive(store)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "bad\\path", "CON", "bad name", ".hidden"])
def test_catalog_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError):
        ProjectCatalog(tmp_path).store(name)


def test_project_catalog_lifecycle(store, tmp_path):
    catalog = ProjectCatalog(tmp_path / "catalog")
    original = catalog.create("one", store.load())
    duplicate = catalog.duplicate("one", "two")
    assert duplicate.load() == original.load()
    entries = catalog.list()
    assert len(entries) == 2 and entries[0]["id"] != entries[1]["id"]
    catalog.archive("one")
    assert [entry["directory"] for entry in catalog.list()] == ["two"]
    catalog.archive("one", False)
    assert len(catalog.list(query="one")) == 1
    (catalog.root / "escape").symlink_to(store.root)
    with pytest.raises(ValueError, match="symbolic"):
        catalog.store("escape")


def test_recovery_retains_corrupt_evidence(store):
    original = store.load()
    (store.root / "o3d.json").write_text("{broken")
    report = store.recover()
    assert store.load() == original
    assert report["events"]
    evidence = store.root / report["events"][-1]["evidence"]
    assert evidence.read_text() == "{broken"


def test_future_schema_is_rejected_without_rewrite(store):
    path = store.root / "o3d.json"
    value = json.loads(path.read_text()) | {"schema_version": 999}
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="compatible"):
        store.load()
    assert path.read_bytes() == before
    assert migrate_document({"name": "old"})[0]["schema_version"] == 1


def _platform_app(store):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from o3d.domains.platform import mount
    app = FastAPI()
    @app.exception_handler(ValueError)
    async def invalid(_, error):
        return JSONResponse({"detail": str(error)}, status_code=422)
    @app.exception_handler(Conflict)
    async def conflict(_, error):
        return JSONResponse({"detail": str(error)}, status_code=409)
    @app.get("/api/project")
    def inspect():
        return store.load().model_dump(mode="json")
    @app.post("/api/commands")
    def apply(command: Command):
        return store.apply(command).model_dump(mode="json")
    mount(app, store, None)
    return app


def test_authenticated_roles_revocation_and_agent_spoofing(store):
    from fastapi.testclient import TestClient
    from o3d.identity import Identity
    from o3d.model import Parameter
    identity = Identity(store)
    owner = identity.enable("Owner")
    admin = identity.authenticate(owner["token"])
    credentials = {role: identity.issue(admin, role, role) for role in ("viewer", "reader", "editor", "manager")}
    agent = identity.issue(admin, "Locked agent", "editor", "agent")
    p = store.load()
    p.parameters["protected"] = Parameter(value=5, locked=True)
    store.apply(Command(expected_revision=1, actor="human", project=p))
    client = TestClient(_platform_app(store))
    assert client.get("/api/project").status_code == 401
    proposed = store.load().model_dump(mode="json")
    proposed["parameters"]["protected"]["value"] = 6
    body = {"expected_revision": 2, "actor": "human", "project": proposed}
    for role in ("viewer", "reader"):
        headers = {"Authorization": "Bearer " + credentials[role]["token"]}
        assert client.get("/api/project", headers=headers).status_code == 200
        assert client.post("/api/commands", headers=headers, json=body).status_code == 403
        assert client.post("/api/platform/identity/issue", headers=headers, json={"name": "escalate", "role": "administrator"}).status_code == 403
    headers = {"Authorization": "Bearer " + agent["token"]}
    # The authenticated credential kind wins over an arbitrary human actor label.
    assert client.post("/api/commands", headers=headers, json=body).status_code == 409
    headers = {"Authorization": "Bearer " + credentials["editor"]["token"]}
    assert client.post("/api/commands", headers=headers, json=body).status_code == 200
    assert store.history()[-1]["principal"]["id"] == credentials["editor"]["principal"]["id"]
    identity.revoke(admin, credentials["editor"]["principal"]["id"])
    assert client.get("/api/project", headers=headers).status_code == 403
    with pytest.raises(ValueError, match="final administrator"):
        identity.revoke(admin, admin.id)
    serialized = identity.path.read_text()
    assert owner["token"] not in serialized and agent["token"] not in serialized
    assert identity.path.stat().st_mode & 0o777 == 0o600


def test_role_export_review_and_scoped_browser_session(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from o3d.identity import Identity
    identity = Identity(store)
    owner = identity.enable("Owner")
    admin = identity.authenticate(owner["token"])
    viewer = identity.issue(admin, "Viewer", "viewer")
    reader = identity.issue(admin, "Reader", "reader")
    outer = FastAPI()
    outer.mount("/p/project", _platform_app(store))
    client = TestClient(outer)
    assert client.get("/p/project/api/project").status_code == 401
    assert client.post("/p/project/api/platform/session", json={"token": viewer["token"]}).status_code == 200
    assert client.get("/p/project/api/project").status_code == 200
    assert client.get("/p/project/api/platform/portable").status_code == 403
    assert client.post("/p/project/api/platform/reviews", json={"revision": 1, "text": "approved"}).status_code == 403
    assert client.post("/p/project/api/platform/session", json={"token": reader["token"]}).status_code == 200
    assert client.get("/p/project/api/platform/portable").status_code == 200
    assert client.post("/p/project/api/platform/comments", json={"revision": 1, "text": "Please verify mounting clearance"}).status_code == 200
    assert client.post("/p/project/api/platform/session", json={"token": owner["token"]}).status_code == 200
    assert client.post("/p/project/api/platform/reviews", json={"revision": 1, "text": "Approved", "decision": "approved"}).status_code == 200
    store.apply(Command(expected_revision=1, actor="human", project=store.load().model_copy(update={"name": "changed"})))
    reviews = client.get("/p/project/api/platform/reviews").json()
    assert reviews["reviews"][0]["state"] == "stale"


def test_three_way_reconcile_preserves_both_edits_and_reports_conflicts(store):
    from o3d.collaboration import Collaboration
    from o3d.model import Parameter
    base = store.load()
    base.parameters["L"] = Parameter(value=80)
    store.apply(Command(expected_revision=1, actor="human", project=base))
    base = store.load()
    offline = base.model_copy(deep=True)
    offline.name = "Offline title"
    online = base.model_copy(deep=True)
    online.parameters["L"].value += 3
    store.apply(Command(expected_revision=2, actor="human", project=online))
    collaboration = Collaboration(store)
    merged = collaboration.reconcile(2, offline.model_dump(mode="json"))
    assert merged["conflicts"] == []
    assert merged["project"]["name"] == "Offline title"
    assert merged["project"]["parameters"]["L"]["value"] == online.parameters["L"].value
    offline.parameters["L"].value += 8
    conflict = collaboration.reconcile(2, offline.model_dump(mode="json"))
    assert conflict["project"] is None
    assert conflict["conflicts"][0]["path"] == "/parameters/L/value"
    resolved = collaboration.reconcile(2, offline.model_dump(mode="json"), {"/parameters/L/value": "proposed"})
    assert not resolved["conflicts"]
    assert resolved["project"]["parameters"]["L"]["value"] == offline.parameters["L"].value
    # Merge proposes a validated model; applying remains a revision-checked command.
    assert store.load().revision == 3


def test_server_draft_survives_restart(store):
    from o3d.domains.platform import execute
    candidate = store.load().model_copy(update={"name": "Unsaved offline work"})
    payload = {"id": "window-1", "base_revision": 1, "project": candidate.model_dump(mode="json")}
    execute(store, store.load(), "draft_save", payload)
    reopened = Store(store.root)
    draft = execute(reopened, reopened.load(), "draft_load", {"id": "window-1"})
    assert draft["project"]["name"] == "Unsaved offline work"
    assert reopened.load().revision == 1
