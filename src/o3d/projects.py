"""Local project catalog, safe paths, explicit migrations and portable archives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .recovery import atomic_bytes, checksum, sync_directory, write_json

SCHEMA_VERSION = 1
ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
RESULT_ROOTS = ("builds", "studies", "assemblies", "drawings", "manufacturing", "electronics", "visualization", "visualization-assets", "interop", "domains/simulation", "domains/drawings", "domains/manufacturing", "domains/electronics", "domains/visualization", "domains/assemblies", "domains/interop")


def _result_path(root: Path, relative: str):
    if not any(relative.startswith(prefix + "/") for prefix in RESULT_ROOTS):
        raise ValueError(f"Saved artifact is outside portable result scope: {relative}")
    return contained(root, ".o3d/" + relative)


def declared_artifacts(document):
    """Saved artifact pointers are dependencies even when bulk cached results are omitted."""
    paths = set()
    def visit(value, parent=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and (key == "artifact" or key.endswith("_artifact")):
                    paths.add(item)
                if key == "artifacts":
                    if isinstance(item, dict):
                        paths.update(path for path in item.values() if isinstance(path, str))
                    elif isinstance(item, list):
                        paths.update(entry["path"] for entry in item if isinstance(entry, dict) and isinstance(entry.get("path"), str))
                if isinstance(value.get("sha256"), str) and key == "path" and isinstance(item, str):
                    paths.add(item)
                visit(item, key)
        elif isinstance(value, list):
            for item in value:
                visit(item, parent)
    visit(document.get("domains", {}))
    return paths


def migrate_document(value: dict) -> tuple[dict, list[str]]:
    """Only lossless known migrations are accepted. Never rewrite an open file."""
    if not isinstance(value, dict):
        raise ValueError("Project document must be an object")
    version = value.get("schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported project schema {version}; original file retained. Open with a compatible Oxygen CAD release.")
    result = dict(value)
    result.setdefault("schema_version", 1)
    return result, ["Added explicit schema_version=1"] if "schema_version" not in value else []


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Project directory name must be 1–64 ASCII letters, numbers, hyphens or underscores")
    if value.lower() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        raise ValueError("Project name is reserved by the operating system")
    return value


def contained(root: Path, relative: str, *, exists=True) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"..", ".", ""} for part in relative.split("/")) or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe project path")
    result = root.joinpath(*path.parts)
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Project paths cannot traverse symbolic links")
    if not result.resolve().is_relative_to(root.resolve()) or (exists and not result.is_file()):
        raise ValueError(f"Project dependency is missing: {relative}")
    return result


def verify_assets(root: Path, documents: list[dict]):
    """Check saved provenance, not just newly generated archive checksums."""
    expected_hashes = {}
    for document in documents:
        domains = document.get("domains", {})
        declarations = list(document.get("references", []))
        registries = {}
        for domain in ("interop", "visualization"):
            registry = domains.get(domain, {}).get("assets", [])
            if not isinstance(registry, (dict, list)):
                raise ValueError(f"Invalid {domain} immutable asset registry")
            registries[domain] = registry
            declarations.extend(registry.values() if isinstance(registry, dict) else registry)
        by_file, by_id = {}, {}
        for reference in declarations:
            if not isinstance(reference, dict):
                raise ValueError("Invalid immutable asset declaration")
            relative, digest = reference.get("file"), reference.get("sha256")
            if not isinstance(relative, str) or not relative.startswith("assets/") or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Immutable assets require contained paths and SHA-256 provenance")
            path = contained(root, relative)
            if path.stem != digest or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Project dependency hash mismatch: {relative}")
            if relative in by_file and by_file[relative] != digest:
                raise ValueError("Conflicting immutable asset declarations")
            if relative in expected_hashes and expected_hashes[relative] != digest:
                raise ValueError(f"Conflicting saved dependency hashes: {relative}")
            expected_hashes[relative] = digest
            by_file[relative] = digest
            if reference.get("id"):
                if reference["id"] in by_id and by_id[reference["id"]] != relative:
                    raise ValueError("Ambiguous immutable asset identity")
                by_id[reference["id"]] = relative
        for reference in declarations:
            if reference.get("source_asset") and reference["source_asset"] not in by_id:
                raise ValueError(f"Derived asset source is missing: {reference['source_asset']}")
        links = (document.get("native_document") or {}).get("links", {})
        if not isinstance(links, dict):
            raise ValueError("Invalid native linked-design registry")
        for link in links.values():
            relative = link.get("path") if isinstance(link, dict) else None
            if not isinstance(relative, str) or relative not in by_file:
                raise ValueError("Native linked source lacks a registered immutable dependency")
            contained(root, relative)
        artifact_paths = declared_artifacts(document)
        for relative in artifact_paths:
            _result_path(root, relative)
        # Imported engineering sources can live in the evidence cache, e.g. KiCad originals.
        def verify_evidence(value):
            if isinstance(value, dict):
                digest = value.get("sha256")
                if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
                    for key in ("original_artifact", "artifact", "path"):
                        relative = value.get(key)
                        if isinstance(relative, str) and relative in artifact_paths:
                            if hashlib.sha256(_result_path(root, relative).read_bytes()).hexdigest() != digest:
                                raise ValueError(f"Saved artifact provenance hash mismatch: {relative}")
                            key = ".o3d/" + relative
                            if key in expected_hashes and expected_hashes[key] != digest:
                                raise ValueError(f"Conflicting saved dependency hashes: {relative}")
                            expected_hashes[key] = digest
                for item in value.values():
                    verify_evidence(item)
            elif isinstance(value, list):
                for item in value:
                    verify_evidence(item)
        verify_evidence(domains)
    return expected_hashes


def project_metadata(store):
    path = store.cache / "project.json"
    if path.exists():
        return json.loads(path.read_text())
    value = {"schema_version": 1, "id": str(uuid.uuid4()), "archived": False}
    write_json(path, value)
    return value


def portable_archive(store, destination: Path | None = None, *, include_results=True, expected_project=None):
    """Capture source, all retained history and immutable dependencies, never credentials."""
    with store.lock():
        project = store._load()
        if expected_project is not None and project != expected_project:
            raise ValueError("Project changed during export; retry export from the current revision")
        files = {"o3d.json": store.root / "o3d.json"}
        for relative in (".o3d/history.jsonl", ".o3d/undo.json", ".o3d/project.json", ".o3d/collaboration.json"):
            path = store.root / relative
            if path.exists():
                files[relative] = contained(store.root, relative)
        directories = ["assets", ".o3d/history"]
        if include_results:
            directories.extend(".o3d/" + prefix for prefix in RESULT_ROOTS)
        for directory in directories:
            base = store.root / directory
            if base.is_symlink():
                raise ValueError("Archive source cannot be a symbolic link")
            if base.exists():
                for path in sorted(base.rglob("*")):
                    if path.is_symlink():
                        raise ValueError("Archive dependencies cannot be symbolic links")
                    if path.is_file():
                        relative = path.relative_to(store.root).as_posix()
                        files[relative] = contained(store.root, relative)
        documents = [json.loads(path.read_text()) for name, path in files.items() if name == "o3d.json" or name.startswith(".o3d/history/")]
        from .model import Project
        for value in documents:
            Project.model_validate(migrate_document(value)[0])
        expected_hashes = verify_assets(store.root, documents)
        for document in documents:
            for relative in declared_artifacts(document):
                files[".o3d/" + relative] = _result_path(store.root, relative)
        for entry in store._history():
            name = f".o3d/history/{entry['revision']:08d}.json"
            if name not in files or (entry.get("sha256") and checksum(json.loads(files[name].read_text())) != entry["sha256"]):
                raise ValueError(f"Revision {entry['revision']} is missing or corrupt; repair before export")
        size = sum(path.stat().st_size for path in files.values())
        if size > MAX_ARCHIVE_BYTES or len(files) + 1 > 100000:
            raise ValueError("Portable archive exceeds its 512 MiB or 100,000 member limit")
        manifest = {"format": "oxygen-cad-project", "schema_version": ARCHIVE_VERSION, "project_schema": project.schema_version,
                    "revision": project.revision, "scope": {"history": "all retained revisions", "assets": "all registered and local immutable assets", "results": include_results,
                    "result_roots": list(RESULT_ROOTS) if include_results else [], "saved_artifact_dependencies": "always included",
                    "excluded": ["credentials", "jobs", "unsaved drafts", "derived exports"]},
                    "files": {}}
        destination = destination or store.cache / "exports" / str(project.revision) / "project.oxygen.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".archive-", dir=destination.parent)
        os.close(descriptor)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                # Fixed metadata makes archives reproducible for unchanged inputs.
                written_bytes = 0
                for name, path in sorted(files.items()):
                    # A job can publish a newer cache file while we hold the source lock.
                    # Hash exactly the bytes written, rather than reading a second time.
                    content = path.read_bytes()
                    written_bytes += len(content)
                    if written_bytes > MAX_ARCHIVE_BYTES:
                        raise ValueError("Portable archive grew beyond its 512 MiB limit during capture")
                    digest = hashlib.sha256(content).hexdigest()
                    if name in expected_hashes and digest != expected_hashes[name]:
                        raise ValueError(f"Saved dependency changed during capture: {name}")
                    manifest["files"][name] = {"sha256": digest, "bytes": len(content)}
                    archive.writestr(zipfile.ZipInfo(name), content)
                manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
                if written_bytes + len(manifest_bytes) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Portable archive manifest exceeds the total 512 MiB member limit")
                archive.writestr(zipfile.ZipInfo("manifest.json"), manifest_bytes)
            with open(temporary, "rb") as output:
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"revision": project.revision, "path": str(destination), "manifest": manifest}


def restore_archive(source: Path | bytes, destination: Path):
    """Validate every member before adopting a new project. Existing data is untouched."""
    import io
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 100000 or len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError("Archive contains duplicate files or exceeds safety limits")
            for entry in entries:
                contained(temporary, entry.filename, exists=False)
                if stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir():
                    raise ValueError("Archive must contain regular files only")
            if "release-manifest.json" in names:
                release = json.loads(archive.read("release-manifest.json"))
                expected_release = release.get("files", {})
                if release.get("format") != "oxygen-cad-release" or release.get("schema_version") != 1 or set(names) != set(expected_release) | {"release-manifest.json"}:
                    raise ValueError("Unsupported or incomplete release archive")
                for name, metadata in expected_release.items():
                    content = archive.read(name)
                    if len(content) != metadata["bytes"] or hashlib.sha256(content).hexdigest() != metadata["sha256"]:
                        raise ValueError(f"Release checksum mismatch: {name}")
                nested = release.get("project_archive")
                if nested != "project.oxygen.zip" or nested not in expected_release:
                    raise ValueError("Release lacks a portable project")
                # Restore the verified saved project; manufacturing outputs remain in the release.
                return restore_archive(archive.read(nested), destination)
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "oxygen-cad-project" or manifest.get("schema_version") != ARCHIVE_VERSION:
                raise ValueError("Unsupported portable project format")
            expected = manifest.get("files", {})
            if set(names) != set(expected) | {"manifest.json"} or "o3d.json" not in expected:
                raise ValueError("Archive manifest does not describe every file")
            allowed = ("assets/", ".o3d/history/", *(".o3d/" + prefix + "/" for prefix in RESULT_ROOTS))
            exact = {"o3d.json", ".o3d/history.jsonl", ".o3d/undo.json", ".o3d/project.json", ".o3d/collaboration.json"}
            for name, metadata in expected.items():
                if name not in exact and not name.startswith(allowed):
                    raise ValueError(f"Archive file is outside portable scope: {name}")
                content = archive.read(name)
                if len(content) != metadata["bytes"] or hashlib.sha256(content).hexdigest() != metadata["sha256"]:
                    raise ValueError(f"Archive checksum mismatch: {name}")
                atomic_bytes(contained(temporary, name, exists=False), content)
        from .store import Store
        from .model import Project
        documents = [json.loads((temporary / "o3d.json").read_text())]
        documents.extend(json.loads(path.read_text()) for path in (temporary / ".o3d/history").glob("*.json"))
        for document in documents:
            Project.model_validate(migrate_document(document)[0])
        verify_assets(temporary, documents)
        restored = Store(temporary)
        # Recovery must not discard a corrupt latest state from a purported complete archive.
        report = restored.recover()
        if report["events"] or report["revision"] != manifest["revision"]:
            raise ValueError("Archive history/source integrity mismatch")
        restored._agent_files()
        os.replace(temporary, destination)
        sync_directory(destination.parent)
        return {"path": str(destination), "revision": report["revision"], "files": len(expected)}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


class ProjectCatalog:
    """Filesystem-local project organization; no cloud or identity claim."""
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def store(self, name):
        from .store import Store
        name = safe_name(name)
        path = self.root / name
        if path.is_symlink():
            raise ValueError("Project directory cannot be a symbolic link")
        return Store(path)

    def list(self, query="", archived=False):
        items = []
        for path in sorted(self.root.iterdir()):
            if path.is_symlink() or not path.is_dir() or not (path / "o3d.json").is_file() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", path.name):
                continue
            store = self.store(path.name)
            try:
                with store.lock():
                    project = store._load()
                    meta = project_metadata(store)
                if meta["archived"] == archived and query.lower() in (path.name + project.name).lower():
                    items.append({**meta, "directory": path.name, "name": project.name, "revision": project.revision})
            except (ValueError, OSError) as error:
                items.append({"directory": path.name, "error": str(error)})
        return items

    def create(self, name, project):
        store = self.store(name)
        # mkdir is the catalog-level exclusive creation primitive.
        store.root.mkdir(exist_ok=False)
        store.init(project)
        with store.lock():
            project_metadata(store)
        return store

    def duplicate(self, source, name):
        original = self.store(source)
        target = self.store(name)
        if target.root.exists():
            raise ValueError("Duplicate destination already exists")
        archive = portable_archive(original)
        restore_archive(Path(archive["path"]), target.root)
        with target.lock():
            write_json(target.cache / "project.json", {"schema_version": 1, "id": str(uuid.uuid4()), "archived": False})
        return target

    def archive(self, name, archived=True):
        store = self.store(name)
        store.load()
        with store.lock():
            meta = project_metadata(store) | {"archived": bool(archived)}
            write_json(store.cache / "project.json", meta)
        return meta
