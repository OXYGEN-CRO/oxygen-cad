"""Host-owned assembly review settings. Geometry and mechanism state live in SolidCraft."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Dimension(DataModel):
    name: str = Field(min_length=1, max_length=120)
    nominal: float = Field(ge=0)
    lower: float = 0
    upper: float = 0
    sign: Literal[-1, 1] = 1

    @model_validator(mode="after")
    def ordered(self):
        if self.lower > self.upper or self.nominal + self.lower < 0:
            raise ValueError("Dimension lower/upper deviations must be ordered and keep dimensions nonnegative")
        return self


class Stack(DataModel):
    name: str = Field(min_length=1, max_length=120)
    dimensions: list[Dimension] = Field(min_length=1, max_length=100)
    required_min: float | None = None
    required_max: float | None = None
    assumptions: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def ordered(self):
        if self.required_min is not None and self.required_max is not None and self.required_min > self.required_max:
            raise ValueError("Required minimum must be no greater than maximum")
        return self


class Pose(DataModel):
    name: str = Field(min_length=1, max_length=120)
    values: dict[str, list[float]] = Field(default_factory=dict)
    native_key: str


class AssemblyData(DataModel):
    schema_version: Literal[1] = 1
    selected_occurrence: int | None = None
    selected_joint: int | None = None
    selected_study: str | int | None = None
    show_origins: bool = True
    samples: int = Field(default=21, ge=3, le=500)
    duration_s: float = Field(default=1, ge=0.001, le=86400)
    clearance_mm: float = Field(default=0.1, ge=0, le=1e6)
    interference_budget_s: float = Field(default=30, gt=0, le=110)
    bom_levels: Literal["flat", "top"] = "flat"
    bom_group_by: Literal["definition", "part_number"] = "part_number"
    bom_group_children: bool = False
    poses: list[Pose] = Field(default_factory=list, max_length=100)
    locks: dict[str, list[list[float] | None]] = Field(default_factory=dict)
    stacks: list[Stack] = Field(default_factory=list, max_length=100)
    reports: list[dict] = Field(default_factory=list, max_length=100)


def native_key(project) -> str:
    """A report follows native design state rather than UI settings or host revision."""
    encoded = json.dumps(project.native_document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def stack_result(stack: Stack) -> dict:
    """Signed interval arithmetic; no inferred standards, statistical assumptions or fit classes."""
    nominal = sum(d.sign * d.nominal for d in stack.dimensions)
    minimum = sum(min(d.sign * (d.nominal + d.lower), d.sign * (d.nominal + d.upper)) for d in stack.dimensions)
    maximum = sum(max(d.sign * (d.nominal + d.lower), d.sign * (d.nominal + d.upper)) for d in stack.dimensions)
    specified = stack.required_min is not None or stack.required_max is not None
    passed = (stack.required_min is None or minimum >= stack.required_min) and (stack.required_max is None or maximum <= stack.required_max)
    return {"name": stack.name, "method": "worst_case_interval", "units": "mm", "nominal": nominal,
            "minimum": minimum, "maximum": maximum, "status": ("passed" if passed else "failed") if specified else "unresolved",
            "required_min": stack.required_min, "required_max": stack.required_max, "assumptions": stack.assumptions,
            "missing": [] if specified else ["Acceptance limits are required"],
            "scope": "Explicit dimensional intervals only; thread strength, preload and manufactured fit are not established"}
