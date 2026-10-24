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
