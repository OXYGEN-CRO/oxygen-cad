"""Versioned workspaces sharing one project and revision boundary.

Domain modules load only when requested. Heavy geometry/solver dependencies belong
inside their execution functions, never in this registry's import path.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
from copy import deepcopy

from ..model import Command, Project, digest
from ..store import Conflict

DOMAIN_NAMES = {
    "modeling": "Model", "assemblies": "Assembly", "drawings": "Drawing",
    "manufacturing": "Manufacture", "simulation": "Simulate",
    "electronics": "Electronics", "visualization": "Render",
    "interop": "Import and repair", "platform": "Project",
}


def module_for(domain):
    if domain not in DOMAIN_NAMES:
        raise ValueError(f"Unknown workspace: {domain}")
    name = f"o3d.domains.{domain}"
    if importlib.util.find_spec(name) is None:
        raise ValueError(f"Workspace {domain} is not installed")
    return importlib.import_module(name)


def catalog():
    return [{"id": key, "name": name,
             "available": importlib.util.find_spec(f"o3d.domains.{key}") is not None}
            for key, name in DOMAIN_NAMES.items()]


def validate_data(domain, data):
    if not isinstance(data, dict):
        raise ValueError("Workspace data must be an object")
    encoded = json.dumps(data, allow_nan=False)
    if len(encoded.encode()) > 10 * 1024 * 1024:
        raise ValueError("Workspace data exceeds 10 MiB")
    if data.get("schema_version", 1) != 1:
        raise ValueError("Unsupported workspace schema version")
    validated = module_for(domain).validate(deepcopy(data))
    if not isinstance(validated, dict):
        raise ValueError("Workspace validator must return an object")
    return validated


def save_domain(store, domain, data, expected_revision, actor="agent"):
    current = store.load()
    if current.revision != expected_revision:
        raise Conflict("Project changed; inspect before saving workspace data")
    updated = current.model_copy(deep=True)
    updated.domains[domain] = validate_data(domain, data)
    return store.apply(Command(expected_revision=expected_revision, actor=actor,
                               project=updated, summary=f"Edit {domain} workspace"))


def execute(store, project, domain, operation, payload, cancel=None, actor="agent"):
    if not isinstance(operation, str) or not operation or len(operation) > 100:
        raise ValueError("A workspace operation is required")
    limit_mb = 70 if domain == "interop" and operation == "import" else 10
    if not isinstance(payload, dict) or len(json.dumps(payload, allow_nan=False).encode()) > limit_mb * 1024 * 1024:
        raise ValueError(f"Workspace input must be an object of at most {limit_mb} MiB")
    payload = deepcopy(payload)
    # Electronics consumes the authenticated actor to enforce its edit locks.
    # Other domains accept their own strict operation payloads unchanged.
    if domain == "electronics":
        payload["actor"] = actor
    result = module_for(domain).execute(store, project, operation, payload, cancel=cancel)
    if not isinstance(result, dict):
        raise ValueError("Workspace operation must return an object")
    json.dumps(result, allow_nan=False)
    return result


def finalize(store, snapshot, domain, result, actor="agent"):
    """Publish a worker proposal through the same guarded transaction as UI edits."""
    result = deepcopy(result)
    result.setdefault("source_revision", snapshot.revision)
    result.setdefault("source_hash", digest(snapshot.model_dump(mode="json")))
    if result.get("changed"):
        if "project" in result:
            proposal = Project.model_validate(result["project"])
            if proposal.revision != snapshot.revision:
                raise ValueError("Workspace proposal must retain its input revision")
        elif "data" in result:
            proposal = snapshot.model_copy(deep=True)
            proposal.domains[domain] = validate_data(domain, result["data"])
        else:
            raise ValueError("Changed workspace result requires data or a project proposal")
        updated = store.apply(Command(expected_revision=snapshot.revision, actor=actor,
                                      project=proposal, summary=f"Update {domain} workspace"))
        result["project"] = updated.model_dump(mode="json")
        result["revision"] = updated.revision
        result["stale"] = False
    else:
        result["stale"] = digest(store.load().model_dump(mode="json")) != digest(snapshot.model_dump(mode="json"))
    return result
