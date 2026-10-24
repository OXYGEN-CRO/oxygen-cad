from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow():
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Provider(Model):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    website: str
    access: Literal["stored", "live-only", "permission-required"]
    license: str
    terms_url: str
    notes: str = ""


class Spec(Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: str | float | bool
    unit: str | None = None
    source_field: str
    evidence: Literal["source", "derived", "measured"] = "source"


class Port(Model):
    name: str
    kind: Literal["mechanical", "electrical", "bus", "fluid"]
    interface: str
    properties: dict = Field(default_factory=dict)
    # Geometry in mm/degrees, explicitly absent until sourced/measured.
    frame: dict | None = None
    evidence: Literal["source", "derived", "measured"] = "source"
    source_field: str


class Asset(Model):
    url: str
    format: str
    kind: Literal["cad", "drawing", "datasheet", "image", "simulation"] = "cad"
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    byte_size: int | None = Field(default=None, ge=0)
    license: str = "unknown"
    license_url: str | None = None
    attribution: str = ""
    download_allowed: bool = False
    # A model's existence does not establish dimensional fidelity or assembly scope.
    scope: str = "unverified"

    @model_validator(mode="after")
    def known_license(self):
        if self.download_allowed and (self.license == "unknown" or not self.license_url):
            raise ValueError("Downloads require a known asset license and license URL")
        return self


class Part(Model):
    external_id: str
    name: str
    description: str = ""
    category: str = "unclassified"
    family: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    supplier_sku: str | None = None
    product_url: str | None = None
    lifecycle: Literal["unknown", "active", "discontinued", "obsolete"] = "unknown"
    procurement: Literal["off_the_shelf", "custom", "reference", "unknown"] = "unknown"
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    attributes: dict = Field(default_factory=dict)
    specs: list[Spec] = Field(default_factory=list)
    ports: list[Port] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    observed_at: datetime = Field(default_factory=utcnow)


class NumericFilter(Model):
    key: str
    unit: str
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("A numeric filter needs min or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min exceeds max")
        return self


class Search(Model):
    q: str = Field(default="", max_length=500)
    category: str | None = None
    family: str | None = None
    provider: str | None = None
    manufacturer: str | None = None
    tags: list[str] = Field(default_factory=list)
    attributes: dict = Field(default_factory=dict)
    numeric: list[NumericFilter] = Field(default_factory=list)
    cad_required: bool = False
    downloadable_cad: bool = False
    procurement: Literal["off_the_shelf", "custom", "reference", "unknown"] | None = None
    mode: Literal["lexical", "hybrid"] = "lexical"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class BOMItem(Model):
    label: str
    quantity: int = Field(default=1, ge=1)
    kind: Literal["off_the_shelf", "custom"]
    part_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    requirements: list[NumericFilter] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def reference(self):
        if self.kind == "custom" and self.part_id:
            raise ValueError("Custom parts must not refer to a purchased catalog part")
        return self


class BOM(Model):
    name: str
    items: list[BOMItem] = Field(min_length=1)
