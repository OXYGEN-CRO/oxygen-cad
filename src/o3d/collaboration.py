"""Exact-revision local review, checkpoints and explicit three-way reconciliation."""
from __future__ import annotations

import json
import uuid
from copy import deepcopy

from .identity import principal_context
from .model import Project, Command
from .recovery import checksum, write_json


def diff(before, after, path=""):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [item for key in sorted(set(before) | set(after)) for item in diff(before.get(key), after.get(key), f"{path}/{key}")]
    return [{"path": path or "/", "before": before, "after": after}]


def merge(base, current, proposed, resolutions=None, path=""):
    """No last-writer-wins. Lists with stable IDs merge as entity maps."""
    resolutions = resolutions or {}
    if current == proposed or proposed == base:
        return deepcopy(current), []
    if current == base:
        return deepcopy(proposed), []
    choice = resolutions.get(path)
    if choice in {"current", "proposed"}:
        return deepcopy(current if choice == "current" else proposed), []
    if all(isinstance(value, dict) for value in (base, current, proposed)):
        result, conflicts = {}, []
        missing = object()
        for key in sorted(set(base) | set(current) | set(proposed)):
            b, c, p = base.get(key, missing), current.get(key, missing), proposed.get(key, missing)
            key_path = f"{path}/{key}"
            if missing in (b, c, p):
                if c == p or p == b:
                    value = c
                elif c == b:
                    value = p
                elif resolutions.get(key_path) in {"current", "proposed"}:
                    value = c if resolutions[key_path] == "current" else p
                else:
                    conflicts.append({"path": key_path, "kind": "add/delete conflict"})
                    value = c
                if value is not missing:
                    result[key] = deepcopy(value)
            else:
                result[key], items = merge(b, c, p, resolutions, key_path)
                conflicts.extend(items)
        return result, conflicts
    if all(isinstance(value, list) for value in (base, current, proposed)):
        combined = base + current + proposed
        identity = next((key for key in ("id", "name") if combined and all(isinstance(item, dict) and key in item for item in combined)), None)
        if identity and all(len({item[identity] for item in value}) == len(value) for value in (base, current, proposed)):
            maps = [{str(item[identity]): item for item in value} for value in (base, current, proposed)]
            result, conflicts = merge(*maps, resolutions, path)
            order = list(dict.fromkeys(str(item[identity]) for item in current + proposed))
            return [result[key] for key in order if key in result], conflicts
    return deepcopy(current), [{"path": path or "/", "kind": "value conflict", "base": base, "current": current, "proposed": proposed}]


class Collaboration:
    def __init__(self, store):
        self.store = store
        self.path = store.cache / "collaboration.json"

    def _read(self):
        if self.path.exists():
            value = json.loads(self.path.read_text())
            if value.get("schema_version") != 1:
                raise ValueError("Unsupported review data version")
            return value
        return {"schema_version": 1, "checkpoints": [], "reviews": [], "comments": []}

    def _target(self, revision):
        path = self.store.cache / "history" / f"{int(revision):08d}.json"
        if not path.is_file():
            raise ValueError("Target revision does not exist")
        value = json.loads(path.read_text())
        Project.model_validate(value)
        return value

    @staticmethod
    def _author():
        principal = principal_context.get()
        return principal.public() if principal else {"id": "local", "name": "Local filesystem user", "kind": "cooperative-local"}

    def inspect(self):
        with self.store.lock():
            project = self.store._load()
            value = self._read()
            current_hash = checksum(project.model_dump(mode="json"))
            return {**value, "reviews": [{**review, "state": "current" if review["sha256"] == current_hash else "stale"} for review in value["reviews"]]}

    def record(self, kind, revision, text, anchor=None, decision=None):
        if kind not in {"checkpoints", "reviews", "comments"}:
            raise ValueError("Unsupported collaboration record")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 5000:
            raise ValueError("Review text must be 1–5000 characters")
        if decision is not None and decision not in {"approved", "changes_requested"}:
            raise ValueError("Unknown review decision")
        with self.store.lock():
            target = self._target(revision)
            if anchor:
                if not isinstance(anchor, dict) or len(json.dumps(anchor)) > 4000:
                    raise ValueError("Invalid annotation anchor")
                feature = anchor.get("feature_id")
                native_features = (target.get("native_document") or {}).get("features", [])
                ids = {item.get("id") for item in target.get("features", []) + native_features}
                if feature and feature not in ids:
                    raise ValueError("Annotation feature is absent from target revision")
            value = self._read()
            from .store import now
            record = {"id": str(uuid.uuid4()), "revision": int(revision), "sha256": checksum(target), "text": text.strip(), "author": self._author(), "time": now()}
            if anchor:
                record["anchor"] = anchor
            if decision:
                record["decision"] = decision
            value[kind].append(record)
            write_json(self.path, value)
            return record

    def compare(self, before, after):
        with self.store.lock():
            return {"before": before, "after": after, "changes": diff(self._target(before), self._target(after))}

    def reconcile(self, base_revision, proposed, resolutions=None):
        with self.store.lock():
            base = self._target(base_revision)
            current = self.store._load()
            proposal = Project.model_validate(proposed).model_dump(mode="json")
            # Revision is transaction metadata, never a merge conflict.
            base["revision"] = proposal["revision"] = current.revision
            result, conflicts = merge(base, current.model_dump(mode="json"), proposal, resolutions)
            return {"base_revision": base_revision, "expected_revision": current.revision, "conflicts": conflicts,
                    "project": Project.model_validate(result).model_dump(mode="json") if not conflicts else None,
                    "geometry_validation": "not_run"}
