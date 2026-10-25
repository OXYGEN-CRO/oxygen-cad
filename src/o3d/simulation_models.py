"""Versioned simulation inputs; mm/N/s/tonne mechanics and W/K thermal inputs.

No process invocation, CAD imports or inferred material properties at import time.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


Vec3 = tuple[float, float, float]
StudyType = Literal["linear_static", "modal", "buckling", "steady_thermal", "thermal_stress", "nonlinear_static", "quasi_static_event", "dynamic_event", "electronics_cooling", "topology", "generative", "general_cfd", "fatigue", "harmonic", "transient_thermal", "injection_molding"]


class MaterialRecord(Record):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    revision: int = Field(default=1, ge=1)
    name: str
    source: str = Field(min_length=1)
    youngs_modulus_mpa: float | None = Field(default=None, gt=0)
    poisson_ratio: float | None = Field(default=None, gt=-1, lt=.5)
    density_g_cm3: float | None = Field(default=None, gt=0)
    conductivity_w_mk: float | None = Field(default=None, gt=0)
    expansion_per_k: float | None = None
    specific_heat_j_kgk: float | None = Field(default=None, gt=0)
    yield_strength_mpa: float | None = Field(default=None, gt=0)
    # True yield stress (MPa), equivalent plastic strain. No automatic engineering conversion.
    plastic_curve: list[tuple[float, float]] = Field(default_factory=list)
    plastic_source: str | None = None
    condition: str = "User-specified; physical correlation not supplied"
    process: str | None = None
    property_sources: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def curves(self):
        if self.plastic_curve:
            if not self.plastic_source or self.plastic_curve[0][1] != 0:
                raise ValueError("Plastic curve requires provenance and an initial zero plastic strain")
            if any(s <= 0 or e < 0 for s, e in self.plastic_curve):
                raise ValueError("Plastic true stresses must be positive and plastic strains nonnegative")
            if any(b[1] <= a[1] or b[0] < a[0] for a, b in zip(self.plastic_curve, self.plastic_curve[1:])):
                raise ValueError("Plastic curve must have increasing strains and nondecreasing true stresses")
        return self


class BodyAssignment(Record):
    body_id: str
    material_id: str


class Region(Record):
    id: str
    body_id: str = "part"
    selection: str | None = None
    node_ids: list[int] = Field(default_factory=list)
    # Boundary triangles carry outward orientation. Coordinates alone never select a face.
    triangles: list[tuple[int, int, int] | tuple[int, int, int, int, int, int]] = Field(default_factory=list)
    normal: Vec3 | None = None
    center_mm: Vec3 | None = None
    area_mm2: float | None = Field(default=None, gt=0)


class Element(Record):
    id: int = Field(gt=0)
    body_id: str = "part"
    nodes: tuple[int, int, int, int] | tuple[int, int, int, int, int, int, int, int, int, int]


class MeshRecord(Record):
    id: str = "mesh"
    geometry_key: str
    nodes: dict[int, Vec3]
    elements: list[Element] = Field(min_length=1, max_length=150000)
    regions: list[Region] = Field(default_factory=list)
    source: str = Field(min_length=1)
    element_family: Literal["C3D4", "C3D10"] = "C3D4"
    mesh_size_mm: float | None = Field(default=None, gt=0)
    cad_volumes_mm3: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def connectivity(self):
        if not self.nodes or len(self.nodes) > 100000 or any(n <= 0 for n in self.nodes):
            raise ValueError("Mesh requires 1..100000 positive node identifiers")
        if len({e.id for e in self.elements}) != len(self.elements):
            raise ValueError("Element identifiers must be unique")
        if len({r.id for r in self.regions}) != len(self.regions):
            raise ValueError("Region identifiers must be unique")
        used = set()
        order = 10 if self.element_family == "C3D10" else 4
        face_order = 6 if order == 10 else 3
        for e in self.elements:
            if len(e.nodes) != order or len(set(e.nodes)) != order or any(n not in self.nodes for n in e.nodes):
                raise ValueError(f"Element {e.id}: repeated or missing node")
            used.update(e.nodes)
        if used != set(self.nodes):
            raise ValueError("Unconnected mesh nodes are not permitted")
        for r in self.regions:
            if any(len(t) != face_order or len(set(t)) != face_order for t in r.triangles):
                raise ValueError(f"Region {r.id}: boundary order differs from {self.element_family}")
            if any(n not in self.nodes for n in r.node_ids) or any(n not in self.nodes for t in r.triangles for n in t):
                raise ValueError(f"Region {r.id}: missing node")
        if any(value <= 0 for value in self.cad_volumes_mm3.values()) or not self.cad_volumes_mm3.keys() <= {e.body_id for e in self.elements}:
            raise ValueError("CAD volumes require positive values and existing body IDs")
        return self


class AnalysisModel(Record):
    id: str
    name: str
    geometry_key: str
    body_ids: list[str] = Field(default_factory=lambda: ["part"])
    simplifications: list[str] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    mesh: MeshRecord | None = None
    refinements: list[MeshRecord] = Field(default_factory=list, max_length=4)


class Support(Record):
    id: str
    region_id: str
    kind: Literal["fixed", "partial", "frictionless", "pin"] = "fixed"
    components: tuple[bool, bool, bool] = (True, True, True)
    direction: Vec3 | None = None
    origin_mm: Vec3 = (0, 0, 0)
    # Pin: direction is cylinder axis, constrain radial translations.


class Load(Record):
    id: str
    kind: Literal["force", "pressure", "moment", "remote", "gravity", "bearing", "hydrostatic"] = "force"
    region_id: str | None = None
    body_id: str | None = None
    vector: Vec3 = (0, 0, 0)
    magnitude: float = 0
    origin_mm: Vec3 = (0, 0, 0)
    axis: Vec3 | None = None
    density_kg_m3: float | None = Field(default=None, gt=0)
    source: str = Field(min_length=1)
    frame: Literal["world"] = "world"


class ThermalBoundary(Record):
    id: str
    kind: Literal["temperature", "heat_power", "heat_flux", "convection", "radiation", "body_heat"]
    region_id: str | None = None
    body_id: str | None = None
    value: float
    ambient_k: float | None = Field(default=None, gt=0)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def physical(self):
        if self.kind == "temperature" and self.value <= 0:
            raise ValueError("Temperatures use positive absolute kelvin")
        if self.kind in {"convection", "radiation"} and (not self.ambient_k or self.value < 0):
            raise ValueError("Convection/radiation requires nonnegative coefficient and ambient kelvin")
        if self.kind == "radiation" and self.value > 1:
            raise ValueError("Emissivity must be between zero and one")
        return self


class LoadCase(Record):
    id: str
    name: str
    supports: list[Support] = Field(default_factory=list)
    loads: list[Load] = Field(default_factory=list)
    thermal: list[ThermalBoundary] = Field(default_factory=list)
    initial_velocity_mm_s: Vec3 = (0, 0, 0)
    # Time (s), multiplier; piecewise linear, no extrapolation beyond last point.
    history: list[tuple[float, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def history_order(self):
        if self.history and (self.history[0][0] != 0 or any(b[0] <= a[0] for a, b in zip(self.history, self.history[1:]))):
            raise ValueError("History starts at zero with strictly increasing times")
        ids = [x.id for x in [*self.supports, *self.loads, *self.thermal]]
        if len(ids) != len(set(ids)):
            raise ValueError("Boundary/load identifiers must be unique within a case")
        return self


class Interface(Record):
    id: str
    kind: Literal["bonded", "separation", "thermal", "disabled"]
    node_pairs: list[tuple[int, int]] = Field(default_factory=list)
    normal: Vec3 | None = None
    stiffness_n_mm: float | None = Field(default=None, gt=0)
    conductance_w_k: float | None = Field(default=None, gt=0)
    initial_gap_mm: float = Field(default=0, ge=0)
    source: str = Field(min_length=1)
    formulation: Literal["paired_node_penalty"] = "paired_node_penalty"


class Bolt(Record):
    id: str
    node_a: int
    node_b: int
    area_mm2: float = Field(gt=0)
    youngs_modulus_mpa: float = Field(gt=0)
    preload_n: float = Field(default=0, ge=0)
    source: str = Field(min_length=1)
    idealization: Literal["axial_truss"] = "axial_truss"


class Limit(Record):
    id: str
    quantity: Literal["max_displacement_mm", "max_von_mises_mpa", "max_temperature_k", "min_frequency_hz", "min_buckling_factor", "mass_g"]
    operator: Literal["le", "ge"] = "le"
    value: float
    source: str = Field(min_length=1)


class SolverSettings(Record):
    residual_tolerance: float = Field(default=1e-7, gt=0, le=.01)
    balance_tolerance: float = Field(default=.01, gt=0, le=.1)
    convergence_tolerance: float = Field(default=.05, gt=0, le=.2)
    mode_count: int = Field(default=6, ge=1, le=30)
    max_iterations: int = Field(default=40, ge=2, le=200)
    duration_s: float = Field(default=.01, gt=0, le=1000)
    time_step_s: float | None = Field(default=None, gt=0)
    max_time_steps: int = Field(default=20000, ge=10, le=200000)
    damping_per_s: float = Field(default=0, ge=0)
    quasi_static_energy_ratio: float = Field(default=.05, gt=0, le=.1)
    energy_tolerance: float = Field(default=.02, gt=0, le=.1)
    nonlinear_initial_increment: float = Field(default=.1, gt=0, le=1)
    large_deformation: bool = False
    inertial_relief: bool = False
    probe_point_mm: Vec3 | None = None
    probe_body_id: str | None = None


class SearchSettings(Record):
    volume_fraction: float = Field(default=.5, gt=.05, le=1)
    iterations: int = Field(default=20, ge=1, le=100)
    minimum_density: float = Field(default=.001, ge=.001, le=.1)
    penalization: float = Field(default=3, ge=1, le=4)
    filter_radius_mm: float = Field(gt=0)
    preserved_element_ids: list[int] = Field(default_factory=list)
    obstacle_element_ids: list[int] = Field(default_factory=list)
    # Bounded candidates explicitly supplied, not unreviewed material inventions.
    candidate_material_ids: list[str] = Field(default_factory=list, max_length=7)
    manufacturing: Literal["unrestricted"] = "unrestricted"


class StudyRecord(Record):
    id: str
    name: str
    type: StudyType = "linear_static"
    model_id: str
    assignments: list[BodyAssignment] = Field(default_factory=list)
    cases: list[LoadCase] = Field(default_factory=list)
    interfaces: list[Interface] = Field(default_factory=list)
    bolts: list[Bolt] = Field(default_factory=list)
    settings: SolverSettings = Field(default_factory=SolverSettings)
    limits: list[Limit] = Field(default_factory=list)
    stress_free_temperature_k: float | None = Field(default=None, gt=0)
    search: SearchSettings | None = None
    assumptions: list[str] = Field(default_factory=list)
    assumptions_confirmed: bool = False

    @model_validator(mode="after")
    def unique(self):
        for name in ("cases", "interfaces", "bolts", "limits"):
            rows = getattr(self, name)
            if len({r.id for r in rows}) != len(rows):
                raise ValueError(f"{name} identifiers must be unique")
        if len({r.body_id for r in self.assignments}) != len(self.assignments):
            raise ValueError("Each body has exactly one material assignment")
        return self


class SimulationDomain(Record):
    schema_version: Literal[1] = 1
    models: list[AnalysisModel] = Field(default_factory=list, max_length=30)
    materials: list[MaterialRecord] = Field(default_factory=list, max_length=100)
    studies: list[StudyRecord] = Field(default_factory=list, max_length=100)
    active_study_id: str | None = None

    @model_validator(mode="after")
    def references(self):
        for name in ("models", "materials", "studies"):
            values = getattr(self, name)
            if len({x.id for x in values}) != len(values):
                raise ValueError(f"Duplicate {name} identifier")
        models, materials = {m.id: m for m in self.models}, {m.id for m in self.materials}
        for s in self.studies:
            if s.model_id not in models:
                raise ValueError(f"Study {s.id}: missing analysis model")
            if any(a.material_id not in materials for a in s.assignments):
                raise ValueError(f"Study {s.id}: missing material revision")
            if any(a.body_id not in models[s.model_id].body_ids for a in s.assignments):
                raise ValueError(f"Study {s.id}: assignment to missing body")
        if self.active_study_id and self.active_study_id not in {s.id for s in self.studies}:
            raise ValueError("Active study is missing")
        return self
