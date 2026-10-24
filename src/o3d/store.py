"""Atomic project edits, optimistic concurrency and immutable revision snapshots."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .model import Command, Project, geometry_key, study_key, verification_key


class Conflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


from .recovery import write_json, commit, replay, checksum, atomic_bytes, sync_directory


def _bindings(before, after, name, path, native=False):
    """Preserve references by stable entity ID, including nested native expressions."""
    if isinstance(before, str):
        bound = bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", before)) if native else before == f"${name}"
        if bound and after != before:
            raise Conflict(f"Parameter {name} binding on {path} is locked by the user")
    elif isinstance(before, dict):
        for key, value in before.items():
            _bindings(value, after.get(key) if isinstance(after, dict) else None, name, f"{path}.{key}", native)
    elif isinstance(before, (list, tuple)):
        for index, value in enumerate(before):
            replacement = after[index] if isinstance(after, (list, tuple)) and index < len(after) else None
            if isinstance(value, dict) and isinstance(after, list):
                identity = "id" if "id" in value else "name" if "name" in value else None
                if identity:
                    replacement = next((item for item in after if isinstance(item, dict) and item.get(identity) == value[identity]), None)
            _bindings(value, replacement, name, f"{path}.{index}", native)


def enforce_agent_guards(current, updated):
    for name, parameter in current.parameters.items():
        other = updated.parameters.get(name)
        if parameter.locked and (other is None or other != parameter):
            raise Conflict(f"Parameter {name} is locked by the user")
        if parameter.locked:
            _bindings([f.model_dump() for f in current.features], [f.model_dump() for f in updated.features], name, "features")
            _bindings(getattr(current, "native_document", None), getattr(updated, "native_document", None), name, "native_document", True)
    locks = getattr(current, "domains", {}).get("modeling", {}).get("parameter_locks", {})
    new_locks = getattr(updated, "domains", {}).get("modeling", {}).get("parameter_locks", {})
    before, after = getattr(current, "native_document", None), getattr(updated, "native_document", None)
    for name, locked in locks.items():
        if locked:
            if not new_locks.get(name):
                raise Conflict(f"Parameter {name} lock is user-owned")
            old_param = next((p for p in (before or {}).get("params", []) if p.get("name") == name), None)
            new_param = next((p for p in (after or {}).get("params", []) if p.get("name") == name), None)
            if old_param != new_param:
                raise Conflict(f"Parameter {name} is locked by the user")
            _bindings(before, after, name, "native_document", True)
    requirements = {r.id: r.model_dump() for r in updated.requirements}
    for requirement in current.requirements:
        if requirements.get(requirement.id) != requirement.model_dump():
            raise Conflict(f"Requirement {requirement.id} is user-owned; agents cannot weaken or remove it")


def validate_domain_transitions(current, updated, actor):
    """Domain-owned guards also cover full-document replacement and history actions."""
    from importlib import import_module
    known = {"modeling", "assemblies", "drawings", "manufacturing", "simulation", "electronics", "visualization", "interop", "platform"}
    before, after = getattr(current, "domains", {}), getattr(updated, "domains", {})
    for domain in sorted((set(before) | set(after)) & known):
        name = f"o3d.domains.{domain}"
        try:
            module = import_module(name)
        except ModuleNotFoundError as error:
            if error.name == name:
                continue
            raise
        hook = getattr(module, "validate_transition", None)
        if hook:
            hook(before.get(domain, {}), after.get(domain, {}), actor)


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.cache = self.root / ".o3d"
        self._fault = None  # injectable crash boundary for persistence tests

    @contextmanager
    def lock(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        sync_directory(self.root)
        sync_directory(self.root.parent)
        with (self.cache / "lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                replay(self.root)
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load(self) -> Project:
        from .projects import migrate_document
        value, _ = migrate_document(json.loads((self.root / "o3d.json").read_text()))
        return Project.model_validate(value)

    def load(self) -> Project:
        with self.lock():
            return self._load()

    def init(self, project: Project):
        with self.lock():
            if (self.root / "o3d.json").exists():
                raise Conflict("Project already exists")
            self._save(project, "human", "Create project", "create")
            from .projects import project_metadata
            project_metadata(self)
            self._agent_files()
        return project

    def _history(self):
        path = self.cache / "history.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []

    def _save(self, project: Project, actor: str, summary: str, action: str, undo=None, request_id=None, request_hash=None):
        value = project.model_dump(mode="json")
        entry = {"revision": project.revision, "actor": actor, "summary": summary, "action": action,
                 "time": now(), "sha256": checksum(value)}
        from .identity import principal_context
        principal = principal_context.get()
        if principal:
            entry["principal"] = principal.public()
        if request_id:
            entry.update(request_id=request_id, request_hash=request_hash)
        commit(self.root, {"project": value, "history": self._history() + [entry],
                          "undo": undo or {"undo": [], "redo": []}}, self._fault)

    def history(self):
        with self.lock():
            return self._history()

    def recover(self):
        """Repair verified saved state, preserving damaged bytes and audit evidence."""
        with self.lock():
            events, valid, invalid = [], {}, []
            history = self._history()
            current_path = self.root / "o3d.json"
            try:
                current_value = json.loads(current_path.read_text())
            except (OSError, ValueError):
                current_value = None
            if isinstance(current_value, dict) and current_value.get("schema_version", 1) != 1:
                raise ValueError("Future project schema cannot be repaired by this version; original retained")
            if current_value is not None:
                try:
                    Project.model_validate(current_value)
                except ValueError:
                    current_value = None
            evidence_root = self.cache / "recovery" / now().replace(":", "-")
            for entry in history:
                path = self.cache / "history" / f"{entry['revision']:08d}.json"
                try:
                    value = json.loads(path.read_text())
                    Project.model_validate(value)
                    if entry.get("sha256") and checksum(value) != entry["sha256"]:
                        raise ValueError("checksum mismatch")
                    valid[entry["revision"]] = value
                except (OSError, ValueError) as error:
                    # Current source is a second copy only when the recorded digest authenticates it.
                    if current_value and current_value["revision"] == entry["revision"] and entry.get("sha256") == checksum(current_value):
                        if path.exists():
                            atomic_bytes(evidence_root / path.name, path.read_bytes())
                        write_json(path, current_value)
                        valid[entry["revision"]] = current_value
                        events.append({"revision": entry["revision"], "repaired_snapshot": True, "error": str(error)})
                    else:
                        invalid.append((entry["revision"], path))
                        events.append({"revision": entry["revision"], "error": str(error)})
            if not valid:
                raise ValueError("No verified revision available; original evidence retained")
            revision = max(valid)
            intact = current_value is not None and checksum(current_value) == checksum(valid[revision])
            if not intact:
                evidence = evidence_root / "o3d.json"
                if current_path.exists():
                    atomic_bytes(evidence, current_path.read_bytes())
                write_json(current_path, valid[revision])
                events.append({"restored_revision": revision, "evidence": str(evidence.relative_to(self.root))})
            if invalid:
                # Preserve damaged immutable entries outside active history, making future edits usable.
                history_path = self.cache / "history.jsonl"
                atomic_bytes(evidence_root / "history.jsonl", history_path.read_bytes())
                for _, path in invalid:
                    if path.exists():
                        target = evidence_root / path.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(path, target)
                atomic_bytes(history_path, b"".join((json.dumps(entry, sort_keys=True) + "\n").encode() for entry in history if entry["revision"] in valid))
            pointer = self.cache / "undo.json"
            try:
                undo = json.loads(pointer.read_text())
            except (OSError, ValueError):
                undo = {}
            write_json(pointer, {key: [r for r in undo.get(key, []) if r in valid] for key in ("undo", "redo")})
            report = {"revision": revision, "events": events, "valid_revisions": sorted(valid)}
            if events:
                report["evidence_directory"] = str(evidence_root.relative_to(self.root))
                write_json(self.cache / "recovery" / "last-report.json", report)
            return report

    def apply(self, command: Command, request_id: str | None = None) -> Project:
        from .identity import authorized
        with self.lock(), authorized(self, "edit") as principal:
            if principal and principal.kind == "agent":
                command = command.model_copy(update={"actor": "agent"})
            current = self._load()
            request_hash = checksum(command.model_dump(mode="json"))
            if request_id:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
                    raise ValueError("Invalid request ID")
                prior = next((entry for entry in self._history() if entry.get("request_id") == request_id), None)
                if prior:
                    if prior.get("request_hash") != request_hash:
                        raise Conflict("Request ID already used for a different edit")
                    return Project.model_validate_json((self.cache / "history" / f"{prior['revision']:08d}.json").read_text())
            if command.expected_revision != current.revision:
                raise Conflict(f"Revision conflict: expected {command.expected_revision}, current {current.revision}. Re-inspect before applying.")
            pointer = self.cache / "undo.json"
            state = json.loads(pointer.read_text()) if pointer.exists() else {"undo": [], "redo": []}
            state = {key: list(value) if isinstance(value, list) else ([value] if value else []) for key, value in state.items()}
            if command.action == "replace":
                if command.project is None:
                    raise ValueError("replace requires a complete project")
                updated = command.project
                state = {"undo": state.get("undo", []) + [current.revision], "redo": []}
            else:
                target = command.restore_revision if command.action == "restore" else (state.get(command.action) or [None])[-1]
                if not target:
                    raise Conflict(f"No {command.action} revision available")
                path = self.cache / "history" / f"{int(target):08d}.json"
                if not path.exists():
                    raise ValueError("Revision does not exist")
                updated = Project.model_validate_json(path.read_text())
                if command.action == "undo":
                    state["undo"].pop()
                    state["redo"].append(current.revision)
                elif command.action == "redo":
                    state["redo"].pop()
                    state["undo"].append(current.revision)
                else:
                    state = {"undo": state.get("undo", []) + [current.revision], "redo": []}
            if command.action == "replace" and getattr(current, "native_document", None) is not None:
                if updated.features != current.features:
                    raise Conflict("Native document is canonical; edit its native timeline instead of legacy features")
                if updated.native_document == current.native_document and updated.parameters != current.parameters:
                    raise Conflict("Native document is canonical; edit dimensions through native parameter commands")
            if command.actor == "agent":
                enforce_agent_guards(current, updated)
            validate_domain_transitions(current, updated, command.actor)
            updated = Project.model_validate(updated.model_dump() | {"revision": current.revision + 1})
            self._save(updated, command.actor, command.summary, command.action, state, request_id, request_hash)
            return updated

    def import_step(self, content: bytes, name: str, expected_revision: int):
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("STEP imports are limited to 50 MiB")
        if not content.lstrip().startswith(b"ISO-10303-21;"):
            raise ValueError("Expected a text STEP exchange file")
        sha = hashlib.sha256(content).hexdigest()
        from .model import ReferenceAsset
        current = self.load()
        asset_id = f"ref_{sha[:12]}"
        if any(r.id == asset_id for r in current.references):
            raise Conflict("This STEP asset is already in the project")
        from .projects import contained
        path = contained(self.root, f"assets/{sha}.step", exists=False)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("Existing immutable asset hash changed; original retained")
        else:
            atomic_bytes(path, content)
        updated = current.model_copy(deep=True)
        updated.references.append(ReferenceAsset(id=asset_id, name=Path(name).name, sha256=sha, file=f"assets/{sha}.step"))
        return self.apply(Command(expected_revision=expected_revision, actor="human", project=updated, summary=f"Import {Path(name).name}"))

    def artifact(self, relative: str) -> Path:
        path = (self.cache / relative).resolve()
        if not path.is_relative_to(self.cache.resolve()) or not path.is_file():
            raise ValueError("Artifact does not exist")
        return path

    def state(self):
        from .analysis import capabilities
        project = self.load()
        gkey, skey = geometry_key(project), study_key(project)
        build_path = self.cache / "builds" / gkey / "build.json"
        analysis_path = self.cache / "studies" / skey / "result.json"
        build = json.loads(build_path.read_text()) if build_path.exists() else None
        analysis = json.loads(analysis_path.read_text()) if analysis_path.exists() else None
        checks_path = self.cache / "builds" / gkey / "checks.json"
        checks = json.loads(checks_path.read_text()) if checks_path.exists() else None
        if checks and checks.get("input_key") != verification_key(project):
            checks = None
        last = self.cache / "last-build.json"
        last_build = json.loads(last.read_text()) if last.exists() else None
        context_path = self.cache / "context.json"
        context = json.loads(context_path.read_text()) if context_path.exists() else {}
        return {"project": project.model_dump(mode="json"), "geometry_key": gkey, "study_key": skey,
                "build": build, "last_build": last_build, "analysis": analysis, "checks": checks,
                "geometry_state": "current" if build else "stale" if last_build else "not_run",
                "analysis_state": analysis.get("state") if analysis else "stale" if (self.cache / "last-study.json").exists() else "not_run",
                "history": self.history(), "context": context, "simulation_capabilities": capabilities()}

    def _agent_files(self):
        write_json(self.root / "project.schema.json", Project.model_json_schema())
        (self.root / "AGENTS.md").write_text('''# Oxygen CAD project\n\nThe authoritative design is o3d.json. Use `o3d inspect --json` and `o3d context --json` first.\nWrite a modified document to proposal.json, then apply with `o3d apply proposal.json --expected-revision N --actor agent`.\nNever overwrite o3d.json while the studio is running. The command service enforces revision and parameter locks.\nUse the project.schema.json schema. Dimensions are in mm; parameter references use $name.\nAfter applying: build, check, render; inspect numeric measurements and the PNG before revising.\nUse `o3d simulate` only after materials, forces, supports and assumptions are specified.\nA finished solve is not evidence of physical safety. Never invent material or load data.\nMissing or ambiguous references require explicit repair, not silently choosing another face.\nExport only current geometry. All machine-facing commands accept --json.\nSupported profiles: rectangle, circle, polygon; extrude add/cut; holes/rectangular arrays; fillet/chamfer.\nNo arbitrary code, general sketch constraints, moving assemblies, or contact simulation.\n''')
