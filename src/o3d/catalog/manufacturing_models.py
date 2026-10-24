"""Public evidence models. Catalog records describe sourced metadata, never live offers.

Job numeric filters compare an explicit source scalar in the exact supplied unit.
No dimension, tolerance, process/material join or unit conversion is inferred.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import NumericFilter

RecordType = Literal['capability', 'material', 'purchased_part', 'hardware', 'finish']


class ManufacturingConstraint(BaseModel):
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    key: str = Field(min_length=1)
    value: Any
    unit: str | None = None
    source_field: str | None = None


class ManufacturingRecord(BaseModel):
    # Research fields that are not yet normalized remain in the evidence payload.
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    record_type: RecordType
    process: str | None = None
    material: str | None = None
    product_url: str | None = None
    source_url: str = Field(min_length=1)
    facts: dict = Field(default_factory=dict)
    constraints: list[ManufacturingConstraint] = Field(default_factory=list)
    api_access: Literal['public', 'requires_key', 'partner', 'web_only', 'unknown'] = 'unknown'
    order_path: str | None = None
    license_status: str = 'unknown'
    notes: list[str] = Field(default_factory=list)


class ManufacturingSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    q: str = Field(default='', max_length=500)
    provider: str | None = None
    record_type: RecordType | None = None
    process: str | None = None
    material: str | None = None
    api_access: str | None = None
    facts: dict = Field(default_factory=dict)
    numeric: list[NumericFilter] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class ManufacturingJob(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    provider: str | None = None
    process: str | None = None
    material: str | None = None
    formats: list[str] = Field(default_factory=list)
    quantity: int | None = Field(default=None, ge=1)
    dimensions: dict[str, float] = Field(default_factory=dict)
    dimension_unit: str = 'mm'
    tolerance: float | None = Field(default=None, gt=0)
    tolerance_unit: str = 'mm'
    numeric: list[NumericFilter] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode='after')
    def positive_dimensions(self):
        if any(axis not in {'x', 'y', 'z'} or value <= 0 for axis, value in self.dimensions.items()):
            raise ValueError('dimensions must contain positive x, y or z values')
        return self
