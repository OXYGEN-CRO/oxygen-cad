"""Unit-bearing requirements and sourced component contracts for static joint selection."""
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]

class JointModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, validate_default=True)

UNITS = {'kg': ('mass', 1.), 'g': ('mass', .001), 'm': ('length', 1.), 'mm': ('length', .001),
         's': ('time', 1.), 'min': ('time', 60.), 'V': ('voltage', 1.), 'N*m': ('torque', 1.),
         'N*mm': ('torque', .001), 'degC': ('temperature', 1.), 'rad/s': ('speed', 1.)}

class Quantity(JointModel):
    value: float
    unit: Literal['kg', 'g', 'm', 'mm', 's', 'min', 'V', 'N*m', 'N*mm', 'degC', 'rad/s']

    @field_validator('value', mode='before')
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError('Quantity values must be finite numbers, not booleans or strings')
        return value

    @model_validator(mode="after")
    def allowed_sign(self):
        if self.unit != "degC" and self.value < 0:
            raise ValueError("Physical magnitudes must be nonnegative")
        return self

    def si(self, dimension):
        kind, scale = UNITS[self.unit]
        if kind != dimension:
            raise ValueError(f'Expected {dimension}, received {self.unit}')
        return self.value * scale

class Source(JointModel):
    id: ID
    url: str = Field(pattern=r'^https://[^\s]+$', max_length=2000)
    locator: str = Field(min_length=1, max_length=1000)
    captured_at: datetime
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: Literal['vendor', 'measured', 'derived'] = 'vendor'

    @field_validator('captured_at')
    @classmethod
    def dated(cls, value):
        if value.tzinfo is None:
            raise ValueError('Source capture must include a timezone')
        return value

class Mount(JointModel):
    """Aligned rectangular bolt grid only; no inferred rotation, thread engagement or fit."""
    pattern_x: Quantity
    pattern_y: Quantity
    thread: str = Field(min_length=1, max_length=40)
    frame_convention: Literal['right_hand_z_out'] = 'right_hand_z_out'
    source: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def lengths(self):
        self.pattern_x.si('length'); self.pattern_y.si('length')
        return self

class Rating(JointModel):
    kind: Literal['stall', 'continuous', 'hold_test']
    torque: Quantity
    voltage: Quantity
    source_id: ID
    duration: Quantity | None = None
    ambient_min: Quantity | None = None
    ambient_max: Quantity | None = None
    cooling: str | None = Field(default=None, min_length=1, max_length=200)
    duty_cycle_max: float | None = Field(default=None, gt=0, le=1)
    basis: Literal['output'] = 'output'

    @model_validator(mode='after')
    def dimensions(self):
        self.torque.si('torque'); self.voltage.si('voltage')
        if self.voltage.value <= 0: raise ValueError('Rating voltage must be positive')
        if self.duration: self.duration.si('time')
        if self.ambient_min: self.ambient_min.si('temperature')
        if self.ambient_max: self.ambient_max.si('temperature')
        if self.ambient_min and self.ambient_max and self.ambient_min.value > self.ambient_max.value: raise ValueError('Ambient bounds are reversed')
        return self

class ActuatorContract(JointModel):
    id: ID
    manufacturer: str = Field(min_length=1, max_length=200)
    mpn: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    catalog_part_id: str | None = Field(default=None, pattern=r'^[0-9a-fA-F-]{36}$')
    scope: str = Field(min_length=1, max_length=1000)
    sources: list[Source] = Field(min_length=1, max_length=30)
    supply_min: Quantity | None = None
    supply_max: Quantity | None = None
    bus: Literal['TTL', 'RS485', 'CAN'] | None = None
    protocol: str | None = Field(default=None, min_length=1, max_length=100)
    connector: str | None = Field(default=None, min_length=1, max_length=100)
    pinout: dict[str, str] = Field(default_factory=dict, max_length=30)
    interface_source_id: ID | None = None
    mount: Mount | None = None
    ratings: list[Rating] = Field(default_factory=list, max_length=100)
    notes: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def references(self):
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)): raise ValueError('Duplicate component source IDs')
        if self.interface_source_id and self.interface_source_id not in ids: raise ValueError('Unknown interface source')
        if any(r.source_id not in ids for r in self.ratings): raise ValueError('Unknown rating source')
        if self.supply_min: self.supply_min.si('voltage')
        if self.supply_max: self.supply_max.si('voltage')
        if self.supply_min and self.supply_max and self.supply_min.value > self.supply_max.value:
            raise ValueError('Component supply minimum exceeds maximum')
        return self

class JointRequirement(JointModel):
    id: ID
    statement: str = Field(min_length=1, max_length=3000)
    provenance: str = Field(min_length=1, max_length=2000)
    state: Literal['draft', 'accepted'] = 'draft'
    subject: ID | None = None
    parameter_refs: list[ID] = Field(default_factory=list, max_length=30)
    bindings: dict[Literal['payload_mass', 'payload_arm', 'link_mass', 'link_com'], ID] = Field(default_factory=dict)
    payload_mass: Quantity | None = None
    payload_arm: Quantity | None = None
    link_mass: Quantity | None = None
    link_com: Quantity | None = None
    hold_duration: Quantity | None = None
    supply_min: Quantity | None = None
    supply_max: Quantity | None = None
    ambient: Quantity | None = None
    cooling: str | None = Field(default=None, min_length=1, max_length=200)
    duty_cycle: float | None = Field(default=None, gt=0, le=1)
    bus: Literal['TTL', 'RS485', 'CAN'] | None = None
    protocol: str | None = Field(default=None, min_length=1, max_length=100)
    connector: str | None = Field(default=None, min_length=1, max_length=100)
    pinout: dict[str, str] = Field(default_factory=dict, max_length=30)
    mount: Mount | None = None
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    static_model_confirmed: bool = False
    selected_component_id: ID | None = None

    @model_validator(mode='after')
    def dimensions(self):
        for field, dim in [('payload_mass','mass'), ('payload_arm','length'), ('link_mass','mass'), ('link_com','length'),
                           ('hold_duration','time'), ('supply_min','voltage'), ('supply_max','voltage'), ('ambient','temperature')]:
            value = getattr(self, field)
            if value is not None: value.si(dim)
        if self.supply_min and self.supply_max and self.supply_min.value > self.supply_max.value:
            raise ValueError('Supply minimum exceeds maximum')
        for name in self.bindings:
            if getattr(self, name) is not None: raise ValueError('A bound quantity must be supplied by its parameter, not a second literal value')
        if len(self.parameter_refs) != len(set(self.parameter_refs)): raise ValueError('Duplicate parameter references')
        return self
