"""Standalone engineering brief schema; references the engineering document by ID only."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .joint_models import ActuatorContract, JointRequirement

BriefID = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Signature = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class BriefModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class BriefInput(BriefModel):
    id: BriefID
    kind: Literal["payload", "envelope", "interface", "budget", "load", "assumption", "other"]
    description: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    value: float | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=40, pattern=r"\S")
    provenance: str | None = Field(default=None, min_length=1, max_length=2000, pattern=r"\S")
    parameter: BriefID | None = None

    @model_validator(mode="after")
    def explicit_quantity(self):
        if self.value is not None and (self.unit is None or self.provenance is None):
            raise ValueError("A specified brief quantity needs explicit units and provenance")
        return self


class BriefEvidence(BriefModel):
    """Identity of native evidence, never a second copy of checks or solver results."""
    source: Literal["checks", "study"]
    source_sha256: Signature
    geometry_signature: Signature
    material_signature: Signature
    requirement_signature: Signature
    input_signature: Signature
    study_signature: Signature | None = None


class BriefLink(BriefModel):
    id: BriefID
    description: str = Field(default="", max_length=2000)
    requirement_id: BriefID
    parameter_refs: list[BriefID] = Field(default_factory=list, max_length=100)
    input_refs: list[BriefID] = Field(default_factory=list, max_length=100)
    study_id: BriefID | None = None
    evidence: BriefEvidence | None = None

    @model_validator(mode="after")
    def distinct_refs(self):
        for name in ("parameter_refs", "input_refs"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {name}")
        return self


class EngineeringBrief(BriefModel):
    schema_version: Literal[1] = 1
    title: str = Field(default="Engineering brief", min_length=1, max_length=200, pattern=r"\S")
    task: str = Field(default="", max_length=10000)
    inputs: list[BriefInput] = Field(default_factory=list, max_length=200)
    links: list[BriefLink] = Field(default_factory=list, max_length=200)
    joint_requirements: list[JointRequirement] = Field(default_factory=list, max_length=50)
    components: list[ActuatorContract] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def distinct_ids(self):
        for name in ("inputs", "links", "joint_requirements", "components"):
            ids = [item.id for item in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate brief {name} IDs")
        return self


# Short public spelling for integrations that operate on a standalone brief.
Brief = EngineeringBrief
