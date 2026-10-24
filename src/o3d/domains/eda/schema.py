"""Versioned electrical authoring source. All dimensions are millimetres/degrees.

This module is deliberately independent of CAD kernels and external EDA runtimes.
Pin/net identities, not coincident drawing coordinates, define connectivity.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_.:-]{0,127}$')]
Point = tuple[float, float]
Positive = Annotated[float, Field(gt=0)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, validate_default=True)


class Pin(Model):
    id: ID
    number: str
    name: str = ''
    electrical: Literal['passive', 'input', 'output', 'bidirectional', 'power_in', 'power_out', 'open_collector', 'no_connect'] = 'passive'
    at: Point = (0, 0)


class Pad(Model):
    id: ID
    pin_id: ID
    number: str
    at: Point
    size: tuple[Positive, Positive]
    shape: Literal['rect', 'circle', 'oval'] = 'rect'
    kind: Literal['smd', 'pth', 'npth'] = 'smd'
    rotation: float = 0
    drill: float = Field(default=0, ge=0)
    slot: Point | None = None  # drill-centre span, symmetric about pad centre
    layers: list[str] = Field(default_factory=lambda: ['F.Cu'])
    mask_margin: float = 0.05
    paste_margin: float = 0

    @model_validator(mode='after')
    def drill_required(self):
        if self.kind != 'smd' and self.drill <= 0:
            raise ValueError('Through-hole pads require an explicit drill')
        if self.kind == 'smd' and (self.drill or self.slot):
            raise ValueError('SMT pads cannot have drills')
        if self.shape == 'circle' and self.size[0] != self.size[1]:
            raise ValueError('Circular pad requires equal dimensions')
        return self


class Model3D(Model):
    asset: str  # immutable project asset identity; never interpreted as a process path
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    offset: tuple[float, float, float] = (0, 0, 0)
    rotation: tuple[float, float, float] = (0, 0, 0)
    size: tuple[Positive, Positive, Positive]
    source: str
    licence: str


class SpiceModel(Model):
    kind: Literal['R', 'C', 'L', 'V', 'I', 'D', 'Q', 'M', 'X']
    pin_order: list[ID]
    terminal_roles: dict[ID, str] = Field(default_factory=dict)
    value: str = ''
    model_name: str = ''
    model_text: str = ''
    source: str = ''
    sha256: str = ''


class LibraryPart(Model):
    id: ID
    version: str
    name: str
    manufacturer: str = ''
    mpn: str = ''
    source: str
    licence: str
    voltage_rating: float | None = Field(default=None, gt=0)
    current_rating: float | None = Field(default=None, gt=0)
    pins: list[Pin]
    pads: list[Pad]
    body_size: tuple[Positive, Positive] = (2, 1)
    courtyard: list[Point] = Field(default_factory=list)
    model3d: Model3D | None = None
    spice: SpiceModel | None = None

    @model_validator(mode='after')
    def identity(self):
        unique(self.pins, 'library pin'); unique(self.pads, 'library pad')
        pins = {p.id for p in self.pins}
        if len({p.number for p in self.pins}) != len(self.pins):
            raise ValueError('Pin numbers must be unique')
        if any(p.pin_id not in pins for p in self.pads):
            raise ValueError('Each pad must map to an existing symbol pin')
        if self.spice and (len(set(self.spice.pin_order)) != len(self.spice.pin_order) or set(self.spice.pin_order) != pins):
            raise ValueError('SPICE pin_order must map each symbol pin exactly once')
        return self


class Port(Model):
    id: ID
    name: str
    net_id: ID
    parent_net_id: ID


class Sheet(Model):
    id: ID
    name: str
    parent_id: ID | None = None
    ports: list[Port] = Field(default_factory=list)


class Component(Model):
    id: ID
    sheet_id: ID
    library_id: ID
    library_version: str
    reference: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    value: str = ''
    at: Point = (0, 0)
    rotation: float = 0
    locked: bool = False


class PinRef(Model):
    component_id: ID
    pin_id: ID

    def key(self):
        return f'{self.component_id}/{self.pin_id}'


class Net(Model):
    id: ID
    sheet_id: ID
    name: str
    global_name: bool = False
    members: list[PinRef] = Field(default_factory=list)


class Bus(Model):
    id: ID
    sheet_id: ID
    name: str
    net_ids: list[ID]


class Wire(Model):
    id: ID
    sheet_id: ID
    net_id: ID
    points: list[Point] = Field(min_length=2)
    start: PinRef | None = None
    end: PinRef | None = None
    # Explicit junction identities keep geometric crossing independent of net membership.
    junction_ids: list[ID] = Field(default_factory=list)


class Layer(Model):
    id: str
    kind: Literal['copper', 'dielectric']
    thickness: Positive
    material: str = ''
    epsilon_r: float | None = Field(default=None, gt=0)


class Hole(Model):
    id: ID
    at: Point
    diameter: Positive
    plated: bool = False
    annular_ring: float = Field(default=0.15, ge=0)
    slot: Point | None = None


class Placement(Model):
    id: ID
    component_id: ID
    at: Point
    side: Literal['top', 'bottom'] = 'top'
    rotation: float = 0
    locked: bool = False


class Track(Model):
    id: ID
    net_id: ID
    layer: str
    points: list[Point] = Field(min_length=2)
    width: Positive
    locked: bool = False


class Via(Model):
    id: ID
    net_id: ID
    at: Point
    diameter: Positive
    drill: Positive
    start_layer: str = 'F.Cu'
    end_layer: str = 'B.Cu'
    technology: Literal['through', 'blind', 'buried', 'microvia'] = 'through'
    locked: bool = False


class Zone(Model):
    id: ID
    net_id: ID | None = None
    layer: str
    polygon: list[Point] = Field(min_length=3)
    kind: Literal['copper', 'keepout'] = 'keepout'
    clearance: float = Field(default=0.2, ge=0)
    thermal_gap: Positive = 0.25
    thermal_spoke: Positive = 0.3
    # Filled copper is produced/checked by the independent KiCad worker, never guessed.


class Rule(Model):
    id: ID
    priority: int = 0
    net_ids: list[ID] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)
    width: Positive | None = None
    clearance: float | None = Field(default=None, ge=0)
    drill: Positive | None = None
    annular_ring: Positive | None = None
    edge_clearance: float | None = Field(default=None, ge=0)


class Process(Model):
    name: str = 'User-defined process (not supplier-certified)'
    source: str = 'User-entered design constraints'
    captured: str = ''
    min_width: Positive = 0.2
    clearance: float = Field(default=0.2, ge=0)
    min_drill: Positive = 0.3
    annular_ring: Positive = 0.15
    edge_clearance: float = Field(default=0.25, ge=0)
    mask_web: float = Field(default=0.1, ge=0)


class Pair(Model):
    id: ID
    positive: ID
    negative: ID
    width: Positive
    gap: Positive
    max_skew: float = Field(ge=0)
    max_length: Positive | None = None


class Board(Model):
    id: ID = 'board'
    outline: list[Point] = Field(default_factory=lambda: [(0, 0), (40, 0), (40, 30), (0, 30)], min_length=3)
    cutouts: list[list[Point]] = Field(default_factory=list)
    holes: list[Hole] = Field(default_factory=list)
    stackup: list[Layer] = Field(default_factory=lambda: [Layer(id='F.Cu', kind='copper', thickness=0.035), Layer(id='core', kind='dielectric', thickness=1.53, material='FR4'), Layer(id='B.Cu', kind='copper', thickness=0.035)])
    placements: list[Placement] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    vias: list[Via] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    origin: Point = (0, 0)
    outline_owner: Literal['ecad', 'mcad'] = 'ecad'
    mechanical_occurrence_id: str | None = None
    transform: tuple[float, float, float, float, float, float] = (0, 0, 0, 0, 0, 0)

    @model_validator(mode='after')
    def stack(self):
        unique(self.stackup, 'layer'); unique(self.placements, 'placement')
        for label in ('tracks', 'vias', 'holes', 'zones'):
            unique(getattr(self, label), label)
        layers = [l.id for l in self.stackup if l.kind == 'copper']
        if len(layers) < 2 or layers[0] != 'F.Cu' or layers[-1] != 'B.Cu' or len(layers) % 2:
            raise ValueError('Stackup requires an even copper layer count with F.Cu first and B.Cu last')
        if self.stackup[0].kind != 'copper' or self.stackup[-1].kind != 'copper':
            raise ValueError('Stackup outer layers must be copper')
        for a, b in zip(self.stackup, self.stackup[1:]):
            if a.kind == b.kind:
                raise ValueError('Copper and dielectric layers must alternate')
        if len({p.component_id for p in self.placements}) != len(self.placements):
            raise ValueError('Only one footprint per component is supported')
        for item in self.tracks + self.zones:
            if item.layer not in layers:
                raise ValueError(f'Unknown copper layer: {item.layer}')
        for via in self.vias:
            if via.start_layer not in layers or via.end_layer not in layers or layers.index(via.start_layer) >= layers.index(via.end_layer):
                raise ValueError(f'Invalid via layer pair: {via.id}')
            a, b = layers.index(via.start_layer), layers.index(via.end_layer)
            if via.technology == 'through' and (a != 0 or b != len(layers)-1):
                raise ValueError('Through vias must span the complete stack')
            if via.technology == 'microvia' and b-a != 1:
                raise ValueError('Microvia must join adjacent copper layers')
            if via.technology == 'blind' and not ((a == 0) ^ (b == len(layers)-1)):
                raise ValueError('Blind via must have exactly one outer layer')
            if via.technology == 'buried' and (a == 0 or b == len(layers)-1):
                raise ValueError('Buried via must have two internal layers')
            if via.drill >= via.diameter:
                raise ValueError('Via drill must be smaller than copper diameter')
        return self


class Variant(Model):
    id: ID
    name: str
    dnp: list[ID] = Field(default_factory=list)
    values: dict[ID, str] = Field(default_factory=dict)


class Waiver(Model):
    finding_id: str
    reason: str = Field(min_length=8)
    author: str = Field(min_length=1)
    source_hash: str
    revision: int = Field(ge=1)


class Mate(Model):
    id: ID
    a: ID
    b: ID
    pin_map: dict[ID, ID]


class HarnessWire(Model):
    id: ID
    start: PinRef
    end: PinRef
    points: list[tuple[float, float, float]] = Field(min_length=2)
    gauge_awg: float = Field(gt=0, le=50)
    insulation: str
    min_bend_radius: Positive
    slack: float = Field(default=0, ge=0)
    bundle: str = ''


class Document(Model):
    schema_version: Literal[1] = 1
    units: Literal['mm'] = 'mm'
    name: str = 'Electrical design'
    library: list[LibraryPart] = Field(default_factory=list)
    sheets: list[Sheet] = Field(default_factory=lambda: [Sheet(id='root', name='Main')])
    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    buses: list[Bus] = Field(default_factory=list)
    wires: list[Wire] = Field(default_factory=list)
    no_connect: list[PinRef] = Field(default_factory=list)
    board: Board = Field(default_factory=Board)
    rules: list[Rule] = Field(default_factory=list)
    process: Process = Field(default_factory=Process)
    differential_pairs: list[Pair] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    waivers: list[Waiver] = Field(default_factory=list)
    mates: list[Mate] = Field(default_factory=list)
    harness: list[HarnessWire] = Field(default_factory=list)
    provenance: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def graph(self):
        for name in ('sheets', 'components', 'nets', 'buses', 'wires', 'rules', 'differential_pairs', 'variants', 'mates', 'harness'):
            unique(getattr(self, name), name)
        keys = [(p.id, p.version) for p in self.library]
        if len(set(keys)) != len(keys):
            raise ValueError('Library id/version pairs must be unique')
        sheets = {s.id: s for s in self.sheets}; nets = {n.id: n for n in self.nets}
        components = {c.id: c for c in self.components}
        lib = {(p.id, p.version): p for p in self.library}
        if not sheets or not any(s.parent_id is None for s in self.sheets):
            raise ValueError('At least one root sheet is required')
        for sheet in self.sheets:
            seen = {sheet.id}; parent = sheet.parent_id
            while parent:
                if parent not in sheets or parent in seen:
                    raise ValueError('Sheet hierarchy has an unknown parent or a cycle')
                seen.add(parent); parent = sheets[parent].parent_id
            unique(sheet.ports, 'port')
            for port in sheet.ports:
                if port.net_id not in nets or port.parent_net_id not in nets:
                    raise ValueError('Hierarchical port must reference real local/parent nets')
                if nets[port.net_id].sheet_id != sheet.id or nets[port.parent_net_id].sheet_id != sheet.parent_id:
                    raise ValueError('Port net scope does not match child/parent sheets')
        references = set()
        for c in self.components:
            if c.sheet_id not in sheets or (c.library_id, c.library_version) not in lib:
                raise ValueError(f'{c.id}: missing sheet or pinned library version')
            if c.reference in references:
                raise ValueError('Component reference designators must be unique')
            references.add(c.reference)
        def check_pin(ref):
            c = components.get(ref.component_id)
            if not c or ref.pin_id not in {p.id for p in lib[c.library_id, c.library_version].pins}:
                raise ValueError(f'Unknown pin: {ref.key()}')
            return c
        assigned = set()
        for net in self.nets:
            if net.sheet_id not in sheets:
                raise ValueError('Unknown net sheet')
            for ref in net.members:
                c = check_pin(ref)
                if c.sheet_id != net.sheet_id:
                    raise ValueError('Pin/net scopes differ; use an explicit hierarchical port')
                if ref.key() in assigned:
                    raise ValueError(f'Pin assigned more than once: {ref.key()}')
                assigned.add(ref.key())
        for ref in self.no_connect:
            check_pin(ref)
            if ref.key() in assigned:
                raise ValueError('A no-connect pin cannot belong to a net')
        for wire in self.wires:
            if wire.net_id not in nets or wire.sheet_id != nets[wire.net_id].sheet_id:
                raise ValueError('Wire net/sheet mismatch')
            for ref in (wire.start, wire.end):
                if ref and ref.key() not in {m.key() for m in nets[wire.net_id].members}:
                    raise ValueError('Wire terminal must belong to its electrical net')
        for bus in self.buses:
            if bus.sheet_id not in sheets or any(n not in nets or nets[n].sheet_id != bus.sheet_id for n in bus.net_ids):
                raise ValueError('Bus must enumerate real same-sheet nets')
        for item in self.board.placements:
            if item.component_id not in components:
                raise ValueError('Footprint references an unknown component')
            c=components[item.component_id]
            copper={l.id for l in self.board.stackup if l.kind=='copper'}
            for pad in lib[c.library_id,c.library_version].pads:
                if pad.kind!='npth' and (not pad.layers or any(l!='*.Cu' and l not in copper for l in pad.layers)):
                    raise ValueError('Placed pad must reference available copper layers')
        for item in self.board.tracks + self.board.vias + self.board.zones:
            if item.net_id is not None and item.net_id not in nets:
                raise ValueError('Copper references an unknown net')
        for pair in self.differential_pairs:
            if pair.positive not in nets or pair.negative not in nets or pair.positive == pair.negative:
                raise ValueError('Differential pair requires two different existing nets')
        for rule in self.rules:
            if not set(rule.net_ids)<=set(nets) or not set(rule.layers)<={l.id for l in self.board.stackup if l.kind=='copper'}:
                raise ValueError('Scoped rule references an unknown net or copper layer')
        for variant in self.variants:
            if not set(variant.dnp + list(variant.values)) <= set(components):
                raise ValueError('Variant references an unknown component')
        for wire in self.harness:
            check_pin(wire.start); check_pin(wire.end)
        for mate in self.mates:
            if mate.a not in components or mate.b not in components:
                raise ValueError('Mate references an unknown connector')
            ap = {p.id for p in lib[components[mate.a].library_id, components[mate.a].library_version].pins}
            bp = {p.id for p in lib[components[mate.b].library_id, components[mate.b].library_version].pins}
            if set(mate.pin_map) != ap or set(mate.pin_map.values()) != bp or len(set(mate.pin_map.values())) != len(mate.pin_map):
                raise ValueError('Connector mate requires a bijective complete pin map')
        return self


def unique(items, label):
    ids = [x.id for x in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f'Duplicate {label} IDs')


def digest(data: Document | dict) -> str:
    value = data.model_dump(mode='json') if isinstance(data, Document) else dict(data)
    value.pop('waivers', None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
