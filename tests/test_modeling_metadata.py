import pytest
from pydantic import ValidationError

from o3d.domains.modeling import validate, validate_transition


def test_parameter_lock_metadata_preserves_native_context_and_sketch_checkpoint():
    metadata = {"schema_version": 1, "native_context": {"active_sketch": 42, "selection": [{"type": "sketch_curve", "id": "l7"}], "pending_moves": {}}, "sketch_entry": {"document": {"format": 1, "features": [{"id": 42}]}, "context": {"active_sketch": None}}, "parameter_locks": {"width": True}, "legacy_parameter_map": {"width": "width"}}
    assert validate(metadata) == metadata
    with pytest.raises(ValidationError):
        validate({"parameter_locks": {"width": "false"}})


def test_agent_cannot_remove_or_weaken_parameter_locks():
    before = {"parameter_locks": {"width": True}}
    for after in [{}, {"parameter_locks": {"width": False}}]:
        with pytest.raises(ValueError, match="cannot unlock"):
            validate_transition(before, after, "agent")
    validate_transition(before, {"parameter_locks": {"width": False}}, "human")
