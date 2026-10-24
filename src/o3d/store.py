"""Atomic project edits, optimistic concurrency and immutable revision snapshots."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .model import Command, Project, geometry_key, study_key, verification_key


class Conflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.cache = self.root / ".o3d"

    @contextmanager
    def lock(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        with (self.cache / "lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def load(self) -> Project:
        return Project.model_validate_json((self.root / "o3d.json").read_text())

    def init(self, project: Project):
        with self.lock():
            if (self.root / "o3d.json").exists():
                raise Conflict("Project already exists")
            self._save(project, "human", "Create project", "create")
            self._agent_files()
        return project

    def _save(self, project: Project, actor: str, summary: str, action: str):
        value = project.model_dump(mode="json")
        write_json(self.cache / "history" / f"{project.revision:08d}.json", value)
        write_json(self.root / "o3d.json", value)
        entry = {"revision": project.revision, "actor": actor, "summary": summary, "action": action, "time": now()}
        with (self.cache / "history.jsonl").open("a") as history:
            history.write(json.dumps(entry) + "\n")

    def history(self):
        path = self.cache / "history.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def apply(self, command: Command) -> Project:
        with self.lock():
            current = self.load()
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
            if command.actor == "agent":
                for name, parameter in current.parameters.items():
                    other = updated.parameters.get(name)
                    if parameter.locked and (other is None or other != parameter):
                        raise Conflict(f"Parameter {name} is locked by the user")
                    if parameter.locked:
                        replacements = {f.id: f.model_dump() for f in updated.features}
                        for feature in current.features:
                            for field, value in feature.model_dump().items():
                                if value == f"${name}" and replacements.get(feature.id, {}).get(field) != value:
                                    raise Conflict(f"Parameter {name} binding on {feature.id}.{field} is locked by the user")
                requirements = {r.id: r.model_dump() for r in updated.requirements}
                for requirement in current.requirements:
                    if requirements.get(requirement.id) != requirement.model_dump():
                        raise Conflict(f"Requirement {requirement.id} is user-owned; agents cannot weaken or remove it")
            updated = Project.model_validate(updated.model_dump() | {"revision": current.revision + 1})
            self._save(updated, command.actor, command.summary, command.action)
            write_json(pointer, state)
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
        path = self.root / "assets" / f"{sha}.step"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        updated = current.model_copy(deep=True)
        updated.references.append(ReferenceAsset(id=asset_id, name=Path(name).name, sha256=sha, file=f"assets/{sha}.step"))
        return self.apply(Command(expected_revision=expected_revision, actor="human", project=updated, summary=f"Import {Path(name).name}"))

    def artifact(self, relative: str) -> Path:
        path = (self.cache / relative).resolve()
        if not path.is_relative_to(self.cache.resolve()) or not path.is_file():
            raise ValueError("Artifact does not exist")
        return path

    def state(self):
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
                "history": self.history(), "context": context}

    def _agent_files(self):
        write_json(self.root / "project.schema.json", Project.model_json_schema())
        (self.root / "AGENTS.md").write_text('''# Oxygen CAD project\n\nThe authoritative design is o3d.json. Use `o3d inspect --json` and `o3d context --json` first.\nWrite a modified document to proposal.json, then apply with `o3d apply proposal.json --expected-revision N --actor agent`.\nNever overwrite o3d.json while the studio is running. The command service enforces revision and parameter locks.\nUse the project.schema.json schema. Dimensions are in mm; parameter references use $name.\nAfter applying: build, check, render; inspect numeric measurements and the PNG before revising.\nUse `o3d simulate` only after materials, forces, supports and assumptions are specified.\nA finished solve is not evidence of physical safety. Never invent material or load data.\nMissing or ambiguous references require explicit repair, not silently choosing another face.\nExport only current geometry. All machine-facing commands accept --json.\nSupported profiles: rectangle, circle, polygon; extrude add/cut; holes/rectangular arrays; fillet/chamfer.\nNo arbitrary code, general sketch constraints, moving assemblies, or contact simulation.\n''')
