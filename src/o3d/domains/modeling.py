"""Modeling metadata, separate from the canonical native engineering design."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ModelingData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    native_context: dict[str, Any] = Field(default_factory=dict)
    sketch_entry: dict[str, Any] | None = None
    parameter_locks: dict[str, StrictBool] = Field(default_factory=dict)
    legacy_parameter_map: dict[str, str] | None = None


def validate(data) -> dict:
    # Session context and the entry checkpoint belong to the native service. Never
    # reconstruct them from the UI's parameter fields or erase them on a lock edit.
    return ModelingData.model_validate(data).model_dump(mode="json", exclude_none=True)


def validate_transition(before, after, actor):
    old = validate(before)
    new = validate(after)
    if actor != "human":
        for name, locked in old["parameter_locks"].items():
            if locked and not new["parameter_locks"].get(name, False):
                raise ValueError(f"Agent cannot unlock user-owned parameter {name}")


def execute(store, project, operation, payload, cancel=None):
    if cancel and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise ValueError("Modeling operation cancelled")
    if operation == "inspect":
        from ..native_document import inspect_native
        return inspect_native(project, store)
    if operation == "set_parameter_lock":
        name = payload.get("name")
        locked = payload.get("locked")
        if not isinstance(name, str) or not name or not isinstance(locked, bool):
            raise ValueError("A parameter name and boolean locked value are required")
        data = deepcopy(project.domains.get("modeling", {"schema_version": 1}))
        data.setdefault("parameter_locks", {})[name] = locked
        validate_transition(project.domains.get("modeling", {}), data, payload.get("actor", "agent"))
        return {"data": validate(data), "changed": True}
    raise ValueError(f"Unsupported modeling operation: {operation}")
