"""Offline, explicit supplier request inputs; no provider limits are built in."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog.manufacturing_models import ManufacturingRecord


class SupplierModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SuppliedSource(SupplierModel):
    """Retained bytes, not a claim of independent HTTP verification or certification."""
    url: str = Field(min_length=1, max_length=2000)
    capture: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    captured_at: datetime
    expires_at: datetime
    kind: Literal['supplied_capture', 'synthetic_fixture'] = 'supplied_capture'

    @model_validator(mode='after')
    def timestamps(self):
        if self.captured_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError('Source timestamps require a timezone')
        if self.expires_at <= self.captured_at:
            raise ValueError('Source expiry must follow capture')
        return self


class SupplierProfile(SupplierModel):
    schema_version: Literal[1] = 1
    provider: str = Field(min_length=1, max_length=200)
    record: ManufacturingRecord
    source: SuppliedSource

    @model_validator(mode='after')
    def capability_only(self):
        if self.record.record_type not in {'capability', 'material'}:
            raise ValueError('Select a sourced capability/material record, not a purchased-part offer')
        if self.record.source_url != self.source.url:
            raise ValueError('Record source_url must match retained source URL')
        return self


class ExactMeasurement(SupplierModel):
    value: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=40)


class SupplierRequest(SupplierModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    process: str = Field(min_length=1, max_length=200)
    material: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, strict=True)
    handoff_manifest: str = Field(min_length=1, max_length=500)
    artifacts: list[str] = Field(min_length=1, max_length=32)
    tolerance: ExactMeasurement | None = None
    minimum_wall: ExactMeasurement | None = None

    @model_validator(mode='after')
    def unique_artifacts(self):
        if len(set(self.artifacts)) != len(self.artifacts):
            raise ValueError('Select each artifact only once')
        return self


class SupplierResponse(SupplierModel):
    """Explicitly supplied correspondence, bound to one package, never a live lookup."""
    state: Literal['awaiting_response', 'quoted', 'rejected', 'unknown']
    request_id: str = Field(min_length=1, max_length=200)
    # Copy binding_sha256 from the request package to prevent cross-request reuse.
    binding_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: SuppliedSource
    note: str = Field(min_length=1, max_length=4000)
    amount: str | None = Field(default=None, pattern=r'^\d+(?:\.\d{1,6})?$')
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')

    @model_validator(mode='after')
    def quote_fields(self):
        if self.state == 'quoted' and (self.amount is None or self.currency is None):
            raise ValueError('Explicit quoted response requires amount and currency')
        if self.state != 'quoted' and (self.amount is not None or self.currency is not None):
            raise ValueError('Amount/currency belong only to an explicit quoted response')
        return self
