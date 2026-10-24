"""Canonical upstream documents, isolated native sessions, and revision-safe commands.

The native document is the upstream format, including its migration version and entity
IDs. Oxygen owns only the engineering envelope and transient editing context.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

from .model import Command, Project
from .native import NativeSession, UPSTREAM_COMMIT, compile_native
from .store import Conflict

# Command registration/enablement remains upstream-owned. These groups are host-exposed;
# process/file/resource commands require a specific trusted adapter instead.
NATIVE_GROUPS = frozenset("sketch solid surface construct mesh face body parameters expr timeline select component occurrence joint contact motion assembly bom config explode inspect material appearance sheet plastic form canvas".split())
BLOCKED_COMMANDS = frozenset({
    "component.update_links", "component.extract", "component.replace_with_link",
    "component.insert_design", "component.relink",  # exposed only through asset_id below
    # Text uses the bundled font when no font parameter is supplied; path fonts are denied.
})
ASSET_COMMANDS = frozenset({"file.insert_step", "file.insert_mesh", "component.insert_design", "component.relink"})


def host_allowed(command: str) -> bool:
    return command in ASSET_COMMANDS or (command.partition(".")[0] in NATIVE_GROUPS and command not in BLOCKED_COMMANDS) or command in {"engine.commands", "engine.describe", "document.inspect"}


def _safe_params(value, *, key="", curve_paths=False):
    """Validate untrusted data before the engine sees it, including nested commands.

    An occurrence `path` is a numeric identity array, never a filesystem path.
    """
    if key.lower() in {"path", "file", "filename", "url", "font", "script", "commands", "directory", "dir", "slicer", "program", "executable"}:
        if key == "path" and isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value):
            return
        if key == "path" and curve_paths:
            refs = value if isinstance(value, list) else [value]
            if refs and all(isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", v) for v in refs):
                return
        raise ValueError("Filesystem, font, script and process parameters require a project-asset adapter")
    if key == "command" and isinstance(value, str) and not host_allowed(value):
        raise ValueError("Nested command is unavailable through native transactions")
    if isinstance(value, dict):
        for k, v in value.items():
            _safe_params(v, key=k, curve_paths=curve_paths)
    elif isinstance(value, list):
        for v in value:
            _safe_params(v, curve_paths=curve_paths)


def resolve_asset(store, project: Project, asset_id: str) -> Path:
    """Resolve an immutable project-owned asset, checking scope, hash and symlinks."""
    if store is None:
        raise ValueError("Project assets require a Store")
    assets = [r.model_dump() for r in project.references]
    registry = project.domains.get("interop", {}).get("assets", [])
    assets += list(registry.values()) if isinstance(registry, dict) else registry
    matches = [a for a in assets if a.get("id") == asset_id]
    if len(matches) != 1:
        raise ValueError("Project asset is missing or ambiguous")
    asset = matches[0]
    file = asset.get("file", "")
    path = store.root / file
    root = (store.root / "assets").resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink() or not resolved.is_file():
        raise ValueError("Native imports must resolve immutable assets inside the project assets directory")
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != asset.get("sha256") or resolved.stem != digest:
        raise ValueError("Project asset content or content-addressed filename changed")
    if resolved.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Native imports are limited to 50 MiB")
    return resolved


def _check_imported_design(path: Path):
    if path.suffix.lower() == ".solidcraft":
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("document.json")
            if info.file_size > 15 * 1024 * 1024:
                raise ValueError("Imported native document exceeds 15 MiB")
            document = json.loads(archive.read(info))
    else:
        document = json.loads(path.read_text())
    if document.get("links"):
        raise ValueError("Import linked designs only after embedding their external link dependencies")


def _portable_links(snapshot, store):
    """Never persist machine-specific native linked-source paths."""
    for link in snapshot["document"].get("links", {}).values():
        path = Path(link["path"])
        if path.is_absolute():
            if store is None or not path.resolve().is_relative_to((store.root / "assets").resolve()):
                raise ValueError("Native linked sources must be immutable project assets")
            link["path"] = path.resolve().relative_to(store.root).as_posix()
        elif not path.as_posix().startswith("assets/") or ".." in path.parts:
            raise ValueError("Native linked sources must remain inside project assets")
    return snapshot


@contextmanager
def opened_native(project: Project, store=None):
    """Yield a source-built native session on an immutable project snapshot."""
    if project.geometry_engine != "solidcraft":
        raise ValueError("Native commands require geometry_engine=solidcraft")
    with NativeSession() as session:
        version = session.execute("oxygen.version")
        if version.get("upstream_commit") != UPSTREAM_COMMIT or version.get("protocol") != 1:
            raise RuntimeError("Native engine does not match the pinned document toolchain")
        if project.native_document is not None:
            document = json.loads(json.dumps(project.native_document))
            for link in document.get("links", {}).values():
                path = Path(link["path"])
                if path.is_absolute() or ".." in path.parts or not path.as_posix().startswith("assets/"):
                    raise ValueError("Native linked sources must be portable project assets")
                if store is not None:
                    resolved = (store.root / path).resolve()
                    if not resolved.is_relative_to((store.root / "assets").resolve()):
                        raise ValueError("Native linked source escapes the project assets directory")
                    link["path"] = str(resolved)
            session.execute("oxygen.document.set", {"document": document,
                "context": project.domains.get("modeling", {}).get("native_context", {})})
        elif project.features:
            with tempfile.TemporaryDirectory(prefix="oxygen-migrate-") as folder:
                _, metadata = compile_native(project, Path(folder))
                session.execute("oxygen.document.set", metadata["canonical"])
        else:
            for name, parameter in project.parameters.items():
                session.execute("parameters.add", {"name": name, "expression": parameter.value,
                    "unit": parameter.unit if parameter.unit in {"mm", "deg"} else ""})
        yield session


def query_native(project: Project, command: str, params=None, store=None):
    """Trusted domain query, with no project write. Domain code owns output paths."""
    with opened_native(project, store) as session:
        return session.execute(command, params or {})


def inspect_native(project: Project, store=None):
    with opened_native(project, store) as session:
        inspection = session.execute("document.inspect", {"measure": True})
        sketches = [session.execute("sketch.inspect", {"sketch": s["id"]}) for s in inspection["sketches"]]
        commands = [c | {"host_allowed": host_allowed(c["id"])} for c in session.execute("engine.commands")]
        return {"revision": project.revision, "engine": session.execute("oxygen.version"),
            "document": session.execute("oxygen.document.get")["document"],
            "inspection": inspection, "sketches": sketches, "commands": commands,
            "scene": session.execute("oxygen.scene"),
            "limitations": []}


def prepare_native_command(project: Project, command: str, params=None, store=None):
    """Evaluate a proposed edit without writing; callers commit through Store.apply."""
    if not host_allowed(command):
        raise ValueError("Native command is unavailable through the project transaction interface")
    params = dict(params or {})
    _safe_params(params, curve_paths=command in {"solid.sweep", "solid.pattern.path", "surface.sweep", "sketch.text", "sketch.edit_text"})
    if len(json.dumps(params, allow_nan=False).encode()) > 1024 * 1024:
        raise ValueError("Native command parameters exceed 1 MiB")
    if command in ASSET_COMMANDS:
        asset_id = params.pop("asset_id", None)
        if not isinstance(asset_id, str):
            raise ValueError("Native import requires an immutable project asset_id")
        path = resolve_asset(store, project, asset_id)
        permitted = {"file.insert_step": {".step", ".stp", ".igs", ".iges", ".fcstd", ".3dm"},
            "file.insert_mesh": {".stl", ".obj", ".3mf", ".glb"},
            "component.insert_design": {".solidcraft", ".json"}, "component.relink": {".solidcraft", ".json"}}
        if path.suffix.lower() not in permitted[command]:
            raise ValueError("This asset format is unavailable for the native import command")
        if command in {"component.insert_design", "component.relink"}:
            _check_imported_design(path)
        params["path"] = str(path)
    with opened_native(project, store) as session:
        before = session.execute("oxygen.document.get")
        if command == "sketch.cancel":
            entry = project.domains.get("modeling", {}).get("sketch_entry")
            if not entry:
                raise ValueError("No persisted sketch entry exists; use project undo to restore this older editing session")
            session.execute("oxygen.document.set", entry)
            result = {"cancelled": before["context"].get("active_sketch")}
        else:
            result = session.execute(command, params)
        after = session.execute("oxygen.document.get")
        values = session.execute("document.inspect")["params"]
    before = _portable_links(before, store)
    after = _portable_links(after, store)
    updated = project.model_copy(deep=True)
    if after == before:
        return updated, result
    updated.native_document = after["document"]
    for parameter in values:
        if parameter["name"] in updated.parameters and parameter.get("value") is not None:
            updated.parameters[parameter["name"]].value = parameter["value"]
    modeling = updated.domains.setdefault("modeling", {"schema_version": 1})
    modeling["native_context"] = after["context"]
    if not before["context"].get("active_sketch") and after["context"].get("active_sketch"):
        modeling["sketch_entry"] = before
    elif not after["context"].get("active_sketch"):
        modeling.pop("sketch_entry", None)
    locks = modeling.setdefault("parameter_locks", {})
    locks.update({name: True for name, parameter in project.parameters.items() if parameter.locked})
    # Validate B-rep solids and open surfaces independently before acknowledging an edit.
    # A sketch-only document is valid and intentionally has no exportable body yet.
    with tempfile.TemporaryDirectory(prefix="oxygen-native-validate-") as folder:
        candidate = updated.model_copy(deep=True)
        candidate.domains["modeling"]["native_context"] = after["context"] | {"active_sketch": None}
        compile_native(candidate, Path(folder))
    return Project.model_validate(updated.model_dump()), result


def execute_native(store, expected_revision: int, actor: str, command: str, params=None):
    project = store.load()
    if project.revision != expected_revision:
        raise Conflict(f"Revision conflict: expected {expected_revision}, current {project.revision}. Re-inspect before applying.")
    updated, result = prepare_native_command(project, command, params, store)
    if updated != project:
        updated = store.apply(Command(expected_revision=expected_revision, actor=actor, project=updated,
            summary=f"Native {command}"))
    return {"project": updated, "result": result}
