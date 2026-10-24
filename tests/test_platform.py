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


def test_corrupt_snapshot_repaired_from_verified_current_copy(store):
    p = store.load().model_copy(update={"name": "Latest"})
    store.apply(Command(expected_revision=1, actor="human", project=p))
    snapshot = store.cache / "history/00000002.json"
    snapshot.write_text("corrupt snapshot")
    result = store.recover()
    assert result["revision"] == 2
    assert json.loads(snapshot.read_text())["name"] == "Latest"
    assert any(event.get("repaired_snapshot") for event in result["events"])
    assert store.apply(Command(expected_revision=2, actor="human", action="undo")).revision == 3


def test_corrupt_latest_copies_are_quarantined_and_editing_can_continue(store):
    store.apply(Command(expected_revision=1, actor="human", project=store.load().model_copy(update={"name": "Damaged"})))
    (store.root / "o3d.json").write_text("broken source")
    (store.cache / "history/00000002.json").write_text("broken snapshot")
    result = store.recover()
    assert result["revision"] == 1
    assert [entry["revision"] for entry in store.history()] == [1]
    assert (store.root / result["evidence_directory"] / "00000002.json").read_text() == "broken snapshot"
    assert store.apply(Command(expected_revision=1, actor="human", project=store.load().model_copy(update={"name": "Recovered edit"}))).revision == 2


def test_recursive_native_parameter_bindings_and_lock_flags_are_user_owned():
    from types import SimpleNamespace
    from copy import deepcopy
    from o3d.store import enforce_agent_guards
    before = SimpleNamespace(parameters={}, features=[], requirements=[], domains={"modeling": {"parameter_locks": {"width": True}}}, native_document={"params": [{"name": "width", "expr": "40", "unit": "mm"}], "features": [{"id": "one", "nested": {"sizes": ["width * 2", "height"]}}]})
    changed = deepcopy(before)
    changed.native_document["features"][0]["nested"]["sizes"][0] = "80"
    with pytest.raises(Conflict, match="binding"):
        enforce_agent_guards(before, changed)
    changed = deepcopy(before)
    changed.domains["modeling"]["parameter_locks"]["width"] = False
    with pytest.raises(Conflict, match="user-owned"):
        enforce_agent_guards(before, changed)
    changed = deepcopy(before)
    changed.native_document["params"][0]["expr"] = "50"
    with pytest.raises(Conflict, match="locked"):
        enforce_agent_guards(before, changed)
    # An unrelated identifier containing the same letters is not a locked reference.
    changed = deepcopy(before)
    changed.native_document["features"][0]["nested"]["sizes"][1] = "height + 1"
    enforce_agent_guards(before, changed)


def _kill_save(root, boundary):
    import os
    store = Store(root)
    store._fault = lambda step: os._exit(73) if step == boundary else None
    store.apply(Command(expected_revision=1, actor="human", project=store.load().model_copy(update={"name": "After hard termination"})))


@pytest.mark.parametrize("boundary", ["journal", "snapshot", "source", "history", "undo"])
def test_real_process_death_during_save_reopens_complete_revision(store, boundary):
    ctx = multiprocessing.get_context("fork")
    child = ctx.Process(target=_kill_save, args=(store.root, boundary))
    child.start(); child.join(10)
    assert child.exitcode == 73
    reopened = Store(store.root)
    assert reopened.load().name == "After hard termination"
    assert reopened.load().revision == 2
    assert len(reopened.history()) == 2
    assert json.loads((reopened.cache / "undo.json").read_text())["undo"] == [1]


def test_readonly_save_reports_failure_without_losing_saved_source(store):
    import os
    if os.geteuid() == 0:
        pytest.skip("Root bypasses directory permissions")
    before = (store.root / "o3d.json").read_bytes()
    store.cache.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            store.apply(Command(expected_revision=1, project=store.load().model_copy(update={"name": "Blocked"})))
    finally:
        store.cache.chmod(0o755)
    assert (store.root / "o3d.json").read_bytes() == before
    assert store.load().revision == 1


def test_complete_manufacturing_release_reopens_as_editable_project(store, tmp_path):
    from o3d.geometry import build
    from o3d.artifacts import export_bundle
    build(store, store.load())
    release = export_bundle(store, store.load())
    restored = restore_archive(store.artifact(release["artifact"]), tmp_path / "new-runtime" / "project")
    other = Store(restored["path"])
    assert other.load() == store.load()
    assert other.history() == store.history()
    assert other.apply(Command(expected_revision=1, actor="human", project=other.load().model_copy(update={"name": "Editable after transfer"}))).revision == 2


def test_store_revalidates_revocation_at_mutation_boundary(store):
    from o3d.identity import Identity, principal_context
    identity = Identity(store)
    owner = identity.enable("owner")
    administrator = identity.authenticate(owner["token"])
    credential = identity.issue(administrator, "editor", "editor")
    cached_principal = identity.authenticate(credential["token"])
    identity.revoke(administrator, cached_principal.id)
    context = principal_context.set(cached_principal)
    try:
        with pytest.raises(PermissionError, match="revoked"):
            store.apply(Command(expected_revision=1, actor="human", project=store.load().model_copy(update={"name": "unauthorized"})))
    finally:
        principal_context.reset(context)
    assert store.load().revision == 1


def test_domain_transition_guards_receive_removed_data_and_effective_actor(monkeypatch):
    import sys
    from types import SimpleNamespace
    from o3d.store import validate_domain_transitions
    seen = []
    module = SimpleNamespace(validate_transition=lambda before, after, actor: seen.append((before, after, actor)))
    monkeypatch.setitem(sys.modules, "o3d.domains.electronics", module)
    current = SimpleNamespace(domains={"electronics": {"schema_version": 1, "locked": True}, "future-domain": {"unknown": 1}})
    updated = SimpleNamespace(domains={})
    validate_domain_transitions(current, updated, "agent")
    assert seen == [({"schema_version": 1, "locked": True}, {}, "agent")]


def test_release_rejects_source_advanced_during_export(store):
    from o3d.geometry import build
    from o3d.artifacts import export_bundle
    original = store.load()
    build(store, original)
    store.apply(Command(expected_revision=1, actor="human", project=original.model_copy(update={"name": "Advanced"})))
    with pytest.raises(ValueError, match="changed during export"):
        export_bundle(store, original)
    assert not (store.cache / "exports/1/release.zip").exists()


def test_authenticated_project_health_does_not_expose_saved_data(store):
    from fastapi.testclient import TestClient
    from o3d.identity import Identity
    Identity(store).enable("owner")
    client = TestClient(_platform_app(store))
    assert client.get("/api/project").status_code == 401
    assert client.get("/api/platform/health").json() == {"service": "oxygen-cad", "api_version": 1}


def test_native_and_host_lock_metadata_round_trips_without_changing_dimensions():
    from copy import deepcopy
    from types import SimpleNamespace
    from o3d.model import Parameter
    from o3d.store import synchronize_parameter_locks, canonical_parameter_values, enforce_agent_guards
    before = SimpleNamespace(parameters={"width": Parameter(value=40, locked=True)}, features=[], requirements=[], domains={"modeling": {"schema_version": 1, "native_context": {"active_sketch": "sketch_1"}, "parameter_locks": {"width": True}}}, native_document={"params": [{"name": "width", "expr": "40", "unit": "mm"}]})
    # The native Parameters UI toggles the domain map; stale legacy metadata must follow.
    native_toggle = deepcopy(before)
    native_toggle.domains["modeling"]["parameter_locks"]["width"] = False
    unlocked = synchronize_parameter_locks(before, native_toggle)
    assert unlocked.parameters["width"].locked is False
    assert unlocked.domains["modeling"]["native_context"] == before.domains["modeling"]["native_context"]
    assert canonical_parameter_values(unlocked) == canonical_parameter_values(before)
    with pytest.raises(Conflict, match="locked"):
        enforce_agent_guards(before, unlocked)
    # The existing host Parameters UI may toggle only locked, with the same result.
    host_toggle = deepcopy(before)
    host_toggle.parameters["width"].locked = False
    unlocked = synchronize_parameter_locks(before, host_toggle)
    assert unlocked.domains["modeling"]["parameter_locks"]["width"] is False
    assert unlocked.parameters["width"].value == 40
    # Relocking from either UI makes both representations agree again.
    native_relock = deepcopy(unlocked)
    native_relock.domains["modeling"]["parameter_locks"]["width"] = True
    relocked = synchronize_parameter_locks(unlocked, native_relock)
    assert relocked.parameters["width"].locked is True
    assert relocked.native_document == before.native_document
