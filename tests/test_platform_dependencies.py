"""Cross-domain immutable dependency and source-engine portability regressions."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from o3d.model import Command, NativeFeature, Parameter, Project
from o3d.native import NativeSession
from o3d.native_document import execute_native
from o3d.projects import portable_archive, restore_archive
from o3d.store import Conflict, Store


def _asset(store, content, extension, **metadata):
    sha = hashlib.sha256(content).hexdigest()
    path = store.root / "assets" / f"{sha}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"id": f"asset_{sha[:24]}", "name": path.name, "file": f"assets/{path.name}", "sha256": sha, "format": extension, **metadata}


def _register(store, assets, native=None):
    project = store.load().model_copy(deep=True)
    project.domains["interop"] = {"schema_version": 1, "assets": assets}
    if native is not None:
        project.native_document = native
    return store.apply(Command(expected_revision=project.revision, actor="human", project=project))


def test_real_interop_registry_missing_and_corrupt_sources_block_backup(tmp_path):
    interop = pytest.importorskip("o3d.domains.interop")
    store = Store(tmp_path / "source"); store.init(Project())
    with NativeSession() as session:
        session.execute("solid.box", {"length": 8, "width": 5, "height": 3})
        target = tmp_path / "source.stl"
        session.execute("file.export", {"path": str(target)})
    asset = interop._put_asset(store, target.read_bytes(), "source.stl", "stl", source_units="mm")
    proposed = interop._with_assets(store.load(), asset)
    store.apply(Command(expected_revision=1, actor="human", project=proposed))
    backup = portable_archive(store)
    restored = restore_archive(Path(backup["path"]), tmp_path / "restored")
    assert (Path(restored["path"]) / asset.file).read_bytes() == target.read_bytes()
    path = store.root / asset.file
    path.write_bytes(b"corrupt source bytes")
    with pytest.raises(ValueError, match="hash mismatch"):
        portable_archive(store)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        portable_archive(store)


def test_original_native_json_derived_container_and_link_require_complete_registry(tmp_path):
    store = Store(tmp_path / "source"); store.init(Project())
    with NativeSession() as session:
        session.execute("solid.box", {"length": 7, "width": 6, "height": 2})
        snapshot = session.execute("oxygen.document.get")
        target = tmp_path / "source.solidcraft"
        session.execute("file.save_as", {"path": str(target), "cache": False, "preview": False})
    original = _asset(store, json.dumps(snapshot["document"]).encode(), "json")
    derived = _asset(store, target.read_bytes(), "solidcraft", source_asset=original["id"])
    _register(store, [original, derived])
    execute_native(store, 2, "human", "component.insert_design", {"asset_id": derived["id"], "name": "Linked source"})
    assert store.load().native_document["links"]
    backup = portable_archive(store)
    restored = restore_archive(Path(backup["path"]), tmp_path / "restored")
    assert (Path(restored["path"]) / original["file"]).is_file()
    assert (Path(restored["path"]) / derived["file"]).is_file()
    (store.root / original["file"]).unlink()
    with pytest.raises(ValueError, match="missing"):
        portable_archive(store)


def test_unregistered_native_link_and_missing_historical_image_block_backup(tmp_path):
    store = Store(tmp_path / "source"); store.init(Project())
    output = io.BytesIO(); Image.new("RGB", (2, 2), "red").save(output, "PNG")
    asset = _asset(store, output.getvalue(), "png")
    original_image = store.root / asset["file"]
    canonical_image = store.root / "assets/visualization" / original_image.name
    canonical_image.parent.mkdir(parents=True, exist_ok=True)
    original_image.rename(canonical_image)
    asset["file"] = canonical_image.relative_to(store.root).as_posix()
    project = store.load().model_copy(deep=True)
    project.domains["visualization"] = {"schema_version": 1, "assets": {asset["sha256"]: {"sha256": asset["sha256"], "file": asset["file"], "width": 2, "height": 2}}}
    store.apply(Command(expected_revision=1, actor="human", project=project))
    # Removing a declaration from latest source does not erase its retained-history dependency.
    project = store.load().model_copy(deep=True); project.domains.pop("visualization")
    store.apply(Command(expected_revision=2, actor="human", project=project))
    (store.root / asset["file"]).unlink()
    with pytest.raises(ValueError, match="missing"):
        portable_archive(store)
    (store.root / asset["file"]).write_bytes(output.getvalue())
    with NativeSession() as session:
        document = session.execute("oxygen.document.get")["document"]
    document["links"] = {"1": {"path": asset["file"], "feature": 1}}
    _register(store, [], document)
    with pytest.raises(ValueError, match="registered immutable"):
        portable_archive(store)


@pytest.mark.parametrize("include_results", [True, False])
def test_actual_assembly_report_survives_archive_and_remains_openable(tmp_path, include_results):
    assemblies = pytest.importorskip("o3d.domains.assemblies")
    store = Store(tmp_path / "source"); store.init(Project())
    execute_native(store, 1, "human", "solid.box", {"length": 9, "width": 6, "height": 3})
    result = assemblies.execute(store, store.load(), "report", {"command": "inspect.clearance", "params": {}})
    project = store.load().model_copy(deep=True); project.domains["assemblies"] = result["data"]
    store.apply(Command(expected_revision=project.revision, actor="human", project=project))
    backup = portable_archive(store, include_results=include_results)
    other = Store(restore_archive(Path(backup["path"]), tmp_path / "restored")["path"])
    reopened = assemblies.execute(other, other.load(), "report.open", {"report_artifact": result["report_artifact"]})
    assert reopened["command"] == "inspect.clearance"
    assert reopened["result"] == result["result"]
    (store.cache / result["report_artifact"]).unlink()
    with pytest.raises(ValueError, match="missing"):
        portable_archive(store, include_results=include_results)


def test_legacy_alias_lock_unlock_and_agent_edit_use_source_engine(tmp_path):
    project = Project(parameters={"bad-name": Parameter(value=10, locked=True)}, features=[NativeFeature(id="base", command="solid.box", params={"length": "$bad-name", "width": 4, "height": 2})])
    store = Store(tmp_path / "source"); store.init(project)
    execute_native(store, 1, "human", "solid.box", {"length": 2, "width": 2, "height": 2, "corner": [20, 0, 0]})
    project = store.load().model_copy(deep=True)
    alias = project.domains["modeling"]["legacy_parameter_map"]["bad-name"]
    assert alias != "bad-name" and project.domains["modeling"]["parameter_locks"][alias]
    project.domains["modeling"]["parameter_locks"][alias] = False
    store.apply(Command(expected_revision=2, actor="human", project=project))
    assert store.load().parameters["bad-name"].locked is False
    execute_native(store, 3, "agent", "parameters.change", {"name": alias, "expression": "12 mm"})
    assert store.load().parameters["bad-name"].value == 12
    project = store.load().model_copy(deep=True); project.domains["modeling"]["parameter_locks"][alias] = True
    store.apply(Command(expected_revision=4, actor="human", project=project))
    assert store.load().parameters["bad-name"].locked is True
    with pytest.raises(Conflict, match="locked"):
        execute_native(store, 5, "agent", "parameters.change", {"name": alias, "expression": "14 mm"})
    assert store.load().revision == 5


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_restore_checks_declared_asset_hashes_even_when_archive_manifest_is_recomputed(tmp_path, damage):
    store = Store(tmp_path / "source"); store.init(Project())
    with NativeSession() as session:
        session.execute("solid.box", {"length": 4, "width": 3, "height": 2})
        content = json.dumps(session.execute("oxygen.document.get")["document"]).encode()
    asset = _asset(store, content, "json")
    _register(store, [asset])
    exported = portable_archive(store)
    modified = io.BytesIO()
    with zipfile.ZipFile(exported["path"]) as source, zipfile.ZipFile(modified, "w") as archive:
        manifest = json.loads(source.read("manifest.json"))
        for name in source.namelist():
            if name == "manifest.json":
                continue
            value = source.read(name)
            if name == asset["file"]:
                if damage == "missing":
                    manifest["files"].pop(name)
                    continue
                value = b'{"different":"source"}'
                manifest["files"][name] = {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            archive.writestr(name, value)
        archive.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="missing|hash mismatch"):
        restore_archive(modified.getvalue(), tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_concurrent_cache_replacement_keeps_archive_manifest_consistent(tmp_path, monkeypatch):
    store = Store(tmp_path / "source"); store.init(Project())
    path = store.cache / "domains/simulation/latest/study.json"
    path.parent.mkdir(parents=True)
    original = b'{"result":"completed A"}'
    replacement = b'{"result":"completed B"}'
    path.write_bytes(original)
    real_read = Path.read_bytes
    reads = []
    def cache_published_after_read(self):
        content = real_read(self)
        if self == path:
            reads.append(content)
            path.write_bytes(replacement)
        return content
    monkeypatch.setattr(Path, "read_bytes", cache_published_after_read)
    backup = portable_archive(store)
    restored = restore_archive(Path(backup["path"]), tmp_path / "restored")
    assert real_read(Path(restored["path"]) / path.relative_to(store.root)) == original
    assert reads == [original]
    assert real_read(path) == replacement


def test_legacy_alias_bindings_stay_locked_when_older_canonical_metadata_omits_native_lock_map(tmp_path):
    legacy = Project(parameters={"bad-name": Parameter(value=10, locked=True)}, features=[NativeFeature(id="base", command="solid.box", params={"length": "$bad-name", "width": 4, "height": 2})])
    original = Store(tmp_path / "original"); original.init(legacy)
    execute_native(original, 1, "human", "solid.box", {"length": 2, "width": 2, "height": 2, "corner": [20, 0, 0]})
    canonical = original.load().model_copy(deep=True)
    canonical.revision = 1
    canonical.domains["modeling"].pop("parameter_locks")
    older = Store(tmp_path / "older-canonical"); older.init(canonical)
    feature = canonical.native_document["features"][0]["id"]
    with pytest.raises(Conflict, match="binding"):
        execute_native(older, 1, "agent", "timeline.edit", {"feature": feature, "set": {"length": "14 mm"}})
    assert older.load().revision == 1


def test_archive_size_limit_counts_its_own_manifest(tmp_path, monkeypatch):
    import o3d.projects as projects
    store = Store(tmp_path / "source"); store.init(Project())
    exported = portable_archive(store)
    with zipfile.ZipFile(exported["path"]) as archive:
        total = sum(entry.file_size for entry in archive.infolist())
        assert archive.getinfo("manifest.json").file_size > 0
    monkeypatch.setattr(projects, "MAX_ARCHIVE_BYTES", total - 1)
    with pytest.raises(ValueError, match="manifest"):
        portable_archive(store)
