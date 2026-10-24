"""The public, versioned engineering document. No executable project code."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scalar = float | str
Vec3 = tuple[float, float, float]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class Parameter(Model):
    value: float
    unit: Literal["mm", "deg", "g", "N", "MPa", "dimensionless"] = "mm"
    locked: bool = False


class FeatureBase(Model):
    id: Identifier
    name: str = ""
    suppressed: bool = False


class Profile(FeatureBase):
    type: Literal["profile"] = "profile"
    shape: Literal["rectangle", "circle", "polygon"] = "rectangle"
    width: Scalar = 40
    height: Scalar = 20
    radius: Scalar = 5
    points: list[tuple[float, float]] = Field(default_factory=list)
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    origin: Vec3 = (0, 0, 0)
    rotation: Vec3 = (0, 0, 0)

    @model_validator(mode="after")
    def valid_polygon(self):
        if self.shape == "polygon" and len(self.points) < 3:
            raise ValueError("A polygon requires at least three vertices")
        return self


class Extrude(FeatureBase):
    type: Literal["extrude"] = "extrude"
    profile: Identifier
    distance: Scalar = 4
    operation: Literal["add", "cut"] = "add"


class Hole(FeatureBase):
    type: Literal["hole"] = "hole"
    center: Vec3 = (0, 0, 0)
    axis: Literal["X", "Y", "Z"] = "Z"
    diameter: Scalar = 3.2
    through: bool = True
    depth: Scalar = 5
    reverse: bool = True
    rows: int = Field(default=1, ge=1, le=30)
    columns: int = Field(default=1, ge=1, le=30)
    spacing: tuple[float, float] = (10, 10)


class EdgeTreatment(FeatureBase):
    type: Literal["fillet", "chamfer"]
    selection: str = "part:vertical_edges"
    size: Scalar = 1


class NativeFeature(FeatureBase):
    """A serializable modelling command executed by the vendored Rust engine."""
    type: Literal["native"] = "native"
    command: str = Field(pattern=r"^(solid|sketch)\.[a-z][a-z0-9_.]*$")
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def modelling_only(self):
        from .native_commands import PROJECT_COMMANDS
        if self.command not in PROJECT_COMMANDS:
            raise ValueError("This command is unavailable in a saved native feature")
        # Saved projects describe geometry; filesystem/network commands are never project features.
        def validate(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in {"path", "file", "filename", "url", "font", "script", "commands"}:
                        raise ValueError("File, font and script access is unavailable in native feature parameters")
                    validate(item)
            elif isinstance(value, list):
                for item in value:
                    validate(item)
        validate(self.params)
        if len(json.dumps(self.params, allow_nan=False)) > 100_000:
            raise ValueError("Native feature parameters exceed 100 KB")
        return self


Feature = Annotated[Profile | Extrude | Hole | EdgeTreatment | NativeFeature, Field(discriminator="type")]



class Placement(Model):
    translation: Vec3 = (0, 0, 0)
    rotation: Vec3 = (0, 0, 0)


class ReferenceAsset(Model):
    id: Identifier
    name: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file: str
    placement: Placement = Field(default_factory=Placement)
    visible: bool = True
    check_interference: bool = True


class Material(Model):
    name: str = "User-defined material"
    youngs_modulus_mpa: float | None = Field(default=None, gt=0)
    poisson_ratio: float | None = Field(default=None, gt=-1, lt=0.5)
    density_g_cm3: float = Field(gt=0)
    yield_strength_mpa: float | None = Field(default=None, gt=0)
    source: str = Field(min_length=1, pattern=r"\S")
    thermal_conductivity_w_m_k: float | None = Field(default=None, gt=0)
    specific_heat_j_kg_k: float | None = Field(default=None, gt=0)
    thermal_expansion_per_k: float | None = None
    process: str | None = None
    condition: str | None = None
    property_sources: dict[str, str] = Field(default_factory=dict)


class Requirement(Model):
    id: Identifier
    kind: Literal["max_mass", "max_dimension", "min_clearance", "max_displacement", "max_temperature", "min_frequency", "min_buckling_factor", "max_stress"]
    value: float = Field(gt=0)
    axis: Literal["X", "Y", "Z"] = "X"
    reference: str | None = None
    enabled: bool = True


class Force(Model):
    face: str
    vector_n: Vec3


class TemperatureBoundary(Model):
    face: str
    temperature_c: float = Field(ge=-273.15)


class HeatSource(Model):
    face: str
    power_w: float


StudyType = Literal["linear_static", "modal", "buckling", "thermal", "thermal_stress", "nonlinear_static",
                    "quasistatic_event", "dynamic_event", "shape_optimization", "electronics_cooling", "injection_molding"]


class Study(Model):
    schema_version: Literal[1] = 1
    type: StudyType = "linear_static"
    name: str = "Engineering study"
    fixed_faces: list[str] = Field(default_factory=list)
    forces: list[Force] = Field(default_factory=list)
    temperatures: list[TemperatureBoundary] = Field(default_factory=list)
    heat_sources: list[HeatSource] = Field(default_factory=list)
    reference_temperature_c: float | None = Field(default=None, ge=-273.15)
    mode_count: int = Field(default=6, ge=1, le=20)
    mesher: Literal["netgen", "gmsh"] = "netgen"
    mesh_size_mm: float = Field(default=4, gt=0)
    max_nodes: int = Field(default=200000, ge=100, le=200000)
    solver_timeout_seconds: int = Field(default=180, ge=1, le=600)
    refinement_steps: int = Field(default=3, ge=2, le=4)
    convergence_tolerance: float = Field(default=0.05, gt=0, le=0.2)
    stress_probe_mm: Vec3 | None = None
    probe_point_mm: Vec3 | None = None
    assumptions_confirmed: bool = False


class Project(Model):
    schema_version: Literal[1] = 1
    geometry_engine: Literal["solidcraft", "occt"] = "solidcraft"
    revision: int = Field(default=1, ge=1)
    name: str = "Untitled mounting part"
    units: Literal["mm"] = "mm"
    parameters: dict[Identifier, Parameter] = Field(default_factory=dict)
    features: list[Feature] = Field(default_factory=list, max_length=200)
    # The actual upstream versioned document becomes canonical after migration.
    # Legacy v1 projects still load with their feature list until their first native edit.
    native_document: dict | None = None
    domains: dict[str, dict] = Field(default_factory=dict)
    references: list[ReferenceAsset] = Field(default_factory=list, max_length=30)
    placement: Placement = Field(default_factory=Placement)
    material: Material | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    study: Study = Field(default_factory=Study)

    @model_validator(mode="after")
    def validate_graph(self):
        if self.native_document is not None:
            if self.geometry_engine != "solidcraft":
                raise ValueError("Native documents require the SolidCraft geometry engine")
            if not isinstance(self.native_document.get("format"), str):
                raise ValueError("Native documents must retain their upstream format version")
            if not isinstance(self.native_document.get("features"), list) or not isinstance(self.native_document.get("params"), list):
                raise ValueError("Expected an upstream native document with features and params")
            if any(self.placement.translation) or any(self.placement.rotation):
                raise ValueError("Canonical native documents store placement in their native timeline; use solid.move")
            if len(json.dumps(self.native_document, allow_nan=False).encode()) > 15 * 1024 * 1024:
                raise ValueError("Native document exceeds 15 MiB")
        ids = [f.id for f in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("Feature IDs must be unique")
        refs = [r.id for r in self.references]
        if len(refs) != len(set(refs)):
            raise ValueError("Reference IDs must be unique")
        profiles = set()
        for feature in self.features:
            if isinstance(feature, Profile):
                profiles.add(feature.id)
            elif isinstance(feature, Extrude) and feature.profile not in profiles:
                raise ValueError(f"{feature.id}: profile must appear before the extrusion")
            for key in ("width", "height", "radius", "distance", "diameter", "depth", "size"):
                if hasattr(feature, key):
                    value = self.resolve(getattr(feature, key))
                    if key != "distance" and value <= 0:
                        raise ValueError(f"{feature.id}.{key} must be positive")
                    if key == "distance" and value == 0:
                        raise ValueError(f"{feature.id}.distance cannot be zero")
        for r in self.references:
            if r.file != f"assets/{r.sha256}.step":
                raise ValueError("Reference files must be content-addressed STEP assets")
        return self

    def resolve(self, scalar: Scalar) -> float:
        if isinstance(scalar, str):
            if not re.fullmatch(r"\$[A-Za-z][A-Za-z0-9_-]{0,63}", scalar):
                raise ValueError("Parameter references use $name; executable expressions are unsupported")
            name = scalar[1:]
            if name not in self.parameters:
                raise ValueError(f"Missing parameter: {name}")
            return self.parameters[name].value
        if not math.isfinite(scalar):
            raise ValueError("Dimensions must be finite")
        return scalar


class Finding(Model):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    feature: str | None = None
    value: float | None = None
    limit: float | None = None


class Command(Model):
    expected_revision: int = Field(ge=1)
    actor: Literal["human", "agent"] = "agent"
    action: Literal["replace", "restore", "undo", "redo"] = "replace"
    project: Project | None = None
    restore_revision: int | None = None
    summary: str = "Edit project"


class Job(Model):
    id: str
    kind: Literal["build", "check", "simulate", "render", "export", "domain"]
    revision: int
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    result: dict | None = None


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def geometry_key(project: Project) -> str:
    return digest({"engine": project.geometry_engine, "toolchain": "oxygen-native-document-v3-0520910bfd4f" if project.geometry_engine == "solidcraft" else "build123d-0.13.0-v1", "features": [f.model_dump() for f in project.features] if project.native_document is None else None,
                   "native_document": project.native_document,
                   "native_pending_moves": project.domains.get("modeling", {}).get("native_context", {}).get("pending_moves") if project.native_document else None,
                   "parameters": {k: v.value for k, v in project.parameters.items()},
                   "native_material": {"name": project.material.name, "density_g_cm3": project.material.density_g_cm3} if project.geometry_engine == "solidcraft" and project.material else None,
                   "references": [r.model_dump(exclude={"visible"}) for r in project.references],
                   "placement": project.placement.model_dump()})


def study_key(project: Project) -> str:
    from .analysis import solver_identity
    try:
        runtime = solver_identity()
    except (RuntimeError, OSError):
        runtime = {"name": "CalculiX", "available": False}
    return digest({"geometry": geometry_key(project), "material": project.material.model_dump() if project.material else None,
                   "study": project.study.model_dump(), "requirements": [r.model_dump() for r in project.requirements if r.kind in ("max_displacement", "max_temperature", "min_frequency", "min_buckling_factor", "max_stress")], "solver": runtime})


def verification_key(project: Project) -> str:
    return digest({"geometry": geometry_key(project), "material": project.material.model_dump() if project.material else None,
                   "requirements": [r.model_dump() for r in project.requirements]})
