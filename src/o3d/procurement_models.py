"""Bounded procurement inputs. Offers are transient observations, not catalog metadata."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_QUANTITY = 1_000_000_000


class ProcurementModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SourceProvenance(ProcurementModel):
    source_url: str | None = Field(default=None, max_length=2000)
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @field_validator('source_url')
    @classmethod
    def safe_url(cls, value):
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in value):
                raise ValueError('Source URL must be HTTPS without credentials or control characters')
        return value


class ProcurementIdentity(ProcurementModel):
    provider: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=300)
    asset_id: str | None = Field(default=None, min_length=1, max_length=300)
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


# Decimal strings preserve input precision over JSON/JavaScript. Numeric bounds
# also bound arithmetic precision and prevent pathological exponent expansion.
def bounded_decimal(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Use a bounded decimal string')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal') from exc
    if not number.is_finite() or not -6 <= number.as_tuple().exponent <= 12 or number.copy_abs() > Decimal('1000000000000'):
        raise ValueError('Decimal must be finite, at most 1e12 with at most six decimal places')
    return number


class ProcurementRequirement(ProcurementModel):
    key: str = Field(min_length=1, max_length=200)
    kind: Literal['interface', 'performance']
    operator: Literal['eq', 'gte', 'lte'] = 'eq'
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=50)
    hard: bool = True

    @model_validator(mode='after')
    def numeric_bounds(self):
        if self.operator != 'eq':
            bounded_decimal(self.value)
        return self


class ProcurementLine(ProcurementModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    classification: Literal['purchased', 'custom', 'unknown'] = 'unknown'
    quantity: int | None = Field(default=None, ge=1, le=MAX_QUANTITY, strict=True)
    identity: ProcurementIdentity | None = None
    requirements: list[ProcurementRequirement] = Field(default_factory=list, max_length=100)
    # Explicitly confirm that all hard interface and performance constraints were
    # declared. A short list alone cannot establish safe substitution.
    hard_constraints_complete: bool = False
    offer_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_requirements(self):
        keys = [(r.kind, r.key) for r in self.requirements]
        if len(set(keys)) != len(keys):
            raise ValueError('Requirement keys must be unique per kind')
        return self


class ProcurementBOM(ProcurementModel):
    lines: list[ProcurementLine] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def unique_lines(self):
        if len({line.id for line in self.lines}) != len(self.lines):
            raise ValueError('BOM line IDs must be unique')
        return self


class QuantityPrice(ProcurementModel):
    minimum_quantity: int = Field(ge=1, le=MAX_QUANTITY, strict=True)
    unit_price: str = Field(max_length=40)

    @field_validator('unit_price')
    @classmethod
    def money(cls, value):
        if bounded_decimal(value) < 0:
            raise ValueError('Price cannot be negative')
        return value


class OfferObservation(ProcurementModel):
    id: str = Field(min_length=1, max_length=100)
    identity: ProcurementIdentity | None = None
    provenance: SourceProvenance | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    regions: list[str] = Field(default_factory=list, max_length=100)
    minimum_order_quantity: int | None = Field(default=None, ge=1, le=MAX_QUANTITY, strict=True)
    quantity_breaks: list[QuantityPrice] = Field(default_factory=list, max_length=100)
    stock_state: Literal['in_stock', 'out_of_stock', 'backorder', 'unknown', 'unsupported'] = 'unknown'
    stock_quantity: int | None = Field(default=None, ge=0, le=MAX_QUANTITY, strict=True)
    lead_time_state: Literal['known', 'unknown', 'unsupported'] = 'unknown'
    lead_time_days: int | None = Field(default=None, ge=0, le=3650, strict=True)
    support: Literal['supported', 'unsupported'] = 'supported'

    @field_validator('observed_at', 'expires_at', mode='before')
    @classmethod
    def timestamp_text(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError('Timestamp requires an ISO string with timezone')
        return value

    @field_validator('observed_at', 'expires_at')
    @classmethod
    def aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('Timestamp requires an explicit timezone')
        return value

    @field_validator('regions')
    @classmethod
    def region_values(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError('Regions must be bounded nonempty explicit region identifiers')
        return values

    @model_validator(mode='after')
    def coherent(self):
        if self.observed_at and self.expires_at and self.expires_at <= self.observed_at:
            raise ValueError('Offer expiry must follow observation')
        breaks = [p.minimum_quantity for p in self.quantity_breaks]
        if len(set(breaks)) != len(breaks):
            raise ValueError('Quantity break thresholds must be unique')
        if self.lead_time_state == 'known' and self.lead_time_days is None:
            raise ValueError('Known lead time requires days')
        if self.lead_time_state != 'known' and self.lead_time_days is not None:
            raise ValueError('Unknown/unsupported lead time cannot supply days')
        if self.stock_state == 'out_of_stock' and self.stock_quantity not in (None, 0):
            raise ValueError('Out of stock contradicts positive stock quantity')
        return self


class AlternativeProperty(ProcurementModel):
    key: str = Field(min_length=1, max_length=200)
    kind: Literal['interface', 'performance']
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=50)
    provenance: SourceProvenance | None = None


class ProcurementAlternative(ProcurementModel):
    id: str = Field(min_length=1, max_length=100)
    line_id: str = Field(min_length=1, max_length=100)
    identity: ProcurementIdentity | None = None
    properties: list[AlternativeProperty] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_properties(self):
        keys = [(p.kind, p.key) for p in self.properties]
        if len(set(keys)) != len(keys):
            raise ValueError('Alternative properties must be unique per kind')
        return self


class ProcurementEvaluationRequest(ProcurementModel):
    bom: ProcurementBOM
    offers: list[OfferObservation] = Field(default_factory=list, max_length=1000)
    alternatives: list[ProcurementAlternative] = Field(default_factory=list, max_length=1000)
    region: str | None = Field(default=None, min_length=1, max_length=100)
    as_of: datetime | None = None

    _timestamp_text = field_validator('as_of', mode='before')(OfferObservation.timestamp_text.__func__)
    _aware = field_validator('as_of')(OfferObservation.aware.__func__)

    @model_validator(mode='after')
    def unique_observations(self):
        for values in (self.offers, self.alternatives):
            if len({v.id for v in values}) != len(values):
                raise ValueError('Observation IDs must be unique')
        return self
