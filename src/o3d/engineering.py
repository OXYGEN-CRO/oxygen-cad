"""One bounded command surface for engineering features in the CLI and local UI."""
from __future__ import annotations

from copy import deepcopy

from .model import Command, Project
from .store import Conflict, Store


def _json(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _fields(payload, allowed):
    if not isinstance(payload, dict):
        raise ValueError("Engineering input must be an object")
    unknown = set(payload) - set(allowed)
    if unknown:
        raise ValueError("Unknown engineering fields: " + ", ".join(sorted(unknown)))


def _revision(store, payload):
    revision = payload.get("expected_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("This operation requires an integer expected_revision")
    current = store.load()
    if current.revision != revision:
        raise Conflict("Project changed; inspect before continuing engineering work")
    actor = payload.get("actor", "agent")
    if actor not in ("human", "agent"):
        raise ValueError("Actor must be human or agent")
    return current, actor


def execute(store: Store, feature: str, operation: str, payload: dict) -> dict:
    """No network, checkout, purchase or implicit live-offer persistence."""
    payload = deepcopy(payload)
    if feature == "brief":
        from .brief import evaluate, bind_evidence
        if operation == "inspect":
            _fields(payload, {"brief"})
            return _json(evaluate(store, brief=payload.get("brief")))
        if operation in ("save", "bind"):
            _fields(payload, {"brief", "expected_revision", "actor"})
            current, actor = _revision(store, payload)
            value = payload.get("brief", _json(current.engineering_brief))
            if operation == "bind":
                value = _json(bind_evidence(store, brief=value))
            data = current.model_dump(mode="json")
            data["engineering_brief"] = value
            proposal = Project.model_validate(data)
            updated = store.apply(Command(expected_revision=current.revision, actor=actor, project=proposal,
                                          summary="Bind engineering evidence" if operation == "bind" else "Edit engineering brief"))
            return {"project": updated.model_dump(mode="json"), "traceability": _json(evaluate(store))}
    elif feature == "compatibility":
        from .compatibility import compare, propose_adapter
        if operation == "evaluate":
            return _json(compare(payload))
        if operation == "adapter":
            return _json(propose_adapter(payload))
    elif feature == "procurement":
        from .procurement import inspect_bom, evaluate_procurement
        if operation == "inspect":
            return _json(inspect_bom(payload))
        if operation == "evaluate":
            return _json(evaluate_procurement(payload))
    elif feature == "supplier":
        from .supplier_handoff import preflight, create_request, status, record_response, capture_source
        if operation == "prepare":
            import math
            from .handoff import create
            _fields(payload, {"section_z_mm", "overhang_degrees", "expected_revision", "actor"})
            _revision(store, payload)
            values = [payload.get("section_z_mm"), payload.get("overhang_degrees")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise ValueError("Supply finite section_z_mm and overhang_degrees")
            return create(store, values[0], values[1], expected_revision=payload["expected_revision"])
        if operation == "source":
            _fields(payload, {"content"})
            if not isinstance(payload.get("content"), str):
                raise ValueError("Supplier source content must be text")
            return capture_source(store, payload["content"])
        if operation == "evaluate":
            _fields(payload, {"profile", "request"})
            return preflight(store, payload["profile"], payload["request"])
        if operation == "package":
            _fields(payload, {"profile", "request", "expected_revision", "actor"})
            _revision(store, payload)
            return create_request(store, payload["profile"], payload["request"], expected_revision=payload["expected_revision"])
        if operation == "status":
            _fields(payload, {"manifest", "profile", "request"})
            return status(store, payload["manifest"], profile=payload.get("profile"), request=payload.get("request"))
        if operation == "response":
            _fields(payload, {"manifest", "response", "expected_revision", "actor"})
            _revision(store, payload)
            return record_response(store, payload["manifest"], payload["response"], expected_revision=payload["expected_revision"])
    raise ValueError(f"Unknown engineering operation: {feature}/{operation}")
