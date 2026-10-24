"""Canonical upstream documents, isolated native sessions, and revision-safe commands.

The native document is the upstream format, including its migration version and entity
IDs. Oxygen owns only the engineering envelope and transient editing context.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import zipfile

from .model import Command, Placement, Project
from .native import NativeSession, UPSTREAM_COMMIT, add_legacy_parameters, compile_native
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
    return command in ASSET_COMMANDS or (command.partition(".")[0] in NATIVE_GROUPS and command not in BLOCKED_COMMANDS) or command in {"engine.commands", "engine.describe", "document.inspect", "model.faces", "model.edges", "model.threads"}


def _safe_params(value, *, key="", curve_paths=False):
    """Validate untrusted data before the engine sees it, including nested commands.

    An occurrence `path` is a numeric identity array, never a filesystem path.
    """
    if key.lower() in {"path", "file", "filename", "url", "font", "script", "commands", "directory", "dir", "slicer", "program", "executable"}:
        if key == "path" and isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value):
            return
        if key == "path" and curve_paths:
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    _safe_params(nested_value, key=nested_key, curve_paths=curve_paths)
                return
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
    if root != store.root / "assets" or resolved.parent != root or path.is_symlink() or not resolved.is_file():
        raise ValueError("Native imports must resolve immutable assets inside the project assets directory")
    if resolved.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Native imports are limited to 50 MiB")
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != asset.get("sha256") or resolved.stem != digest:
        raise ValueError("Project asset content or content-addressed filename changed")
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


def _guard_identity(rows, value, label, *, root=False):
    if root and value in (0, "0", "root"):
        return
    if isinstance(value, int) or isinstance(value, str) and value.isdecimal():
        matches = [r for r in rows if str(r.get("id")) == str(value)]
    elif isinstance(value, str):
        matches = [r for r in rows if r.get("name") == value]
    else:
        return  # Structured native references are validated by the owning command.
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous {label} reference {value!r}; select an explicit stable ID")


def _guard_references(document, command, params):
    """Do not let an upstream first-name match choose an ambiguous design object."""
    for singular, plural, rows, root in (
        ("component", "components", document.get("components", []), True),
        ("occurrence", "occurrences", document.get("occurrences", []), False),
    ):
        if singular in params:
            _guard_identity(rows, params[singular], singular, root=root)
        if isinstance(params.get(plural), list):
            for value in params[plural]:
                _guard_identity(rows, value, singular, root=root)
    if command.startswith("timeline.") or command.startswith("solid.pattern"):
        for value in ([params["feature"]] if "feature" in params else params.get("features", [])):
            _guard_identity(document.get("features", []), value, "feature")
    if command == "joint.as_built":
        for key in ("a", "b"):
            if key in params:
                _guard_identity(document.get("occurrences", []), params[key], "occurrence")


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
            session.legacy_parameter_map = project.domains.get("modeling", {}).get("legacy_parameter_map", {})
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
                session.execute("oxygen.document.set", metadata["placed_canonical"])
                session.legacy_parameter_map = metadata["parameter_bindings"]
        else:
            session.legacy_parameter_map = add_legacy_parameters(session, project)
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
    _safe_params(params, curve_paths=command in {"solid.sweep", "solid.pattern.path", "surface.sweep", "sketch.text", "sketch.edit_text", "construct.plane.along_path", "construct.point.along_path"})
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
        parameter_map = dict(session.legacy_parameter_map)
        if command.startswith("parameters.") and params.get("name") in parameter_map:
            params["name"] = parameter_map[params["name"]]
        renamed_from = params.get("name") if command in {"parameters.rename", "parameters.change"} and isinstance(params.get("new_name"), str) else None
        _guard_references(before["document"], command, params)
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
    if renamed_from is not None:
        parameter_map = {name: params["new_name"] if native_name == renamed_from else native_name for name, native_name in parameter_map.items()}
    before = _portable_links(before, store)
    after = _portable_links(after, store)
    updated = project.model_copy(deep=True)
    if after == before:
        return updated, result
    updated.native_document = after["document"]
    if project.native_document is None:
        updated.placement = Placement()
    for parameter in values:
        host_name = next((name for name, native_name in parameter_map.items() if native_name == parameter["name"]), parameter["name"])
        if host_name in updated.parameters and parameter.get("value") is not None:
            mirror = updated.parameters[host_name]
            value = math.degrees(parameter["value"]) if mirror.unit == "deg" else parameter["value"]
            if not math.isclose(value, mirror.value, rel_tol=1e-12, abs_tol=1e-12):
                mirror.value = value
    modeling = updated.domains.setdefault("modeling", {"schema_version": 1})
    modeling["native_context"] = after["context"]
    if parameter_map:
        modeling["legacy_parameter_map"] = parameter_map
    if not before["context"].get("active_sketch") and after["context"].get("active_sketch"):
        modeling["sketch_entry"] = before
    elif not after["context"].get("active_sketch"):
        modeling.pop("sketch_entry", None)
    locks = modeling.setdefault("parameter_locks", {})
    if renamed_from in locks:
        locks[params["new_name"]] = locks.pop(renamed_from)
    for name, parameter in project.parameters.items():
        if parameter.locked:
            locks.setdefault(parameter_map.get(name, name), True)
    # Validate B-rep solids and open surfaces independently before acknowledging an edit.
    # A sketch-only document is valid and intentionally has no exportable body yet.
    if before["document"] != after["document"]:
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
