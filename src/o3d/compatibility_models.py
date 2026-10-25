"""Explicit, sourced mounting interfaces; catalog marketing fields are not geometry."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .model import Model, Project, Vec3

Coordinate = Annotated[float, Field(strict=True, ge=-1_000_000, le=1_000_000)]
BoundedVec3 = tuple[Coordinate, Coordinate, Coordinate]
Axis = tuple[Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)], Annotated[float, Field(strict=True)]]


class InterfaceEvidence(Model):
    kind: Literal["source", "measured"]
    reference: str = Field(min_length=1, pattern=r"\S", max_length=2000)
    source_field: str = Field(min_length=1, pattern=r"\S", max_length=200)
    # The exact document/measurement bytes supporting the dimensions, not a URL hash.
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PlanarFrame(Model):
    """Local XY in a named common assembly frame. Translation uses interface units."""
    assembly_frame: str = Field(min_length=1, max_length=100, pattern=r"\S")
    origin: BoundedVec3 = (0, 0, 0)
    x_axis: Axis = (1, 0, 0)
    y_axis: Axis = (0, 1, 0)

    @model_validator(mode="after")
    def orthonormal(self):
        dot = sum(a*b for a, b in zip(self.x_axis, self.y_axis))
        if abs(dot) > 1e-9 or any(abs(sum(v*v for v in axis)-1) > 1e-9 for axis in (self.x_axis, self.y_axis)):
            raise ValueError("Frame axes must be unit length and orthogonal; scale/shear are unsupported")
        return self


class BoltHole(Model):
    label: str = Field(min_length=1, max_length=64, pattern=r"\S")
    center: tuple[Coordinate, Coordinate]
    diameter: float = Field(strict=True, gt=0, le=10000)


class MountingTolerance(Model):
    """Worst-case radial center error and symmetric diameter error, in interface units."""
    position: float = Field(strict=True, ge=0, le=100)
    diameter: float = Field(strict=True, ge=0, le=100)


class ComponentInterface(Model):
    provider: str = Field(min_length=1, max_length=100, pattern=r"\S")
    sku: str = Field(min_length=1, max_length=200, pattern=r"\S")
    asset_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    interface_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    kind: Literal["planar_through_holes"] = "planar_through_holes"
    units: Literal["mm", "in"] = "mm"
    frame: PlanarFrame | None = None
    holes: list[BoltHole] = Field(default_factory=list, max_length=32)
    tolerance: MountingTolerance | None = None
    evidence: InterfaceEvidence | None = None

    @model_validator(mode="after")
    def unique_holes(self):
        if len({h.label for h in self.holes}) != len(self.holes):
            raise ValueError("Hole labels must be unique within an interface")
        if self.tolerance and any(h.diameter <= self.tolerance.diameter for h in self.holes):
            raise ValueError("Minimum tolerance-bounded hole diameter must be positive")
        return self


class CompatibilityRequest(Model):
    a: ComponentInterface
    b: ComponentInterface
    fastener_diameter_mm: float | None = Field(default=None, strict=True, gt=0, le=10000)


class InterfaceSignature(Model):
    provider: str
    sku: str
    asset_sha256: str | None
    interface_id: str
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompatibilityReason(Model):
    code: str
    message: str
    hole: str | None = None
    measured_mm: float | None = None
    limit_mm: float | None = None


LIMITS = [
    "Evaluated scope: registration of labeled planar through-holes for a floating straight fastener only.",
    "Unknown: body/envelope collisions, fastener heads, engagement, retention, access, strength, fatigue, and manufacturing process capability.",
    "Unknown: electrical, voltage, polarity, pinout, bus/protocol, thermal and functional compatibility; catalog text does not qualify these.",
    "A sourced or measured input is an assertion by its author; signatures detect changes but do not authenticate supplier geometry or prove manufactured fit.",
]


class CompatibilityResult(Model):
    status: Literal["compatible", "incompatible", "unknown"]
    overall_status: Literal["unknown"] = "unknown"
    scope: Literal["mechanical_mounting_registration_only"] = "mechanical_mounting_registration_only"
    sources: list[InterfaceSignature]
    reasons: list[CompatibilityReason]
    limits: list[str] = Field(default_factory=lambda: list(LIMITS))


class AdapterRequest(Model):
    a: ComponentInterface
    b: ComponentInterface
    thickness_mm: float = Field(strict=True, gt=0, le=100)
    edge_clearance_mm: float = Field(strict=True, gt=0, le=100)
    web_clearance_mm: float = Field(strict=True, gt=0, le=100)
    # Added to each sourced hole diameter, not an implied fit tolerance.
    machining_clearance_mm: float = Field(strict=True, ge=0, le=10)
    width_mm: float | None = Field(default=None, strict=True, gt=0, le=2000)
    height_mm: float | None = Field(default=None, strict=True, gt=0, le=2000)


class AdapterHoleMeasurement(Model):
    feature_id: str
    source_interface: Literal["a", "b"]
    label: str
    center_mm: Vec3
    diameter_mm: float
    edge_clearance_mm: float


class AdapterAcceptance(Model):
    status: Literal["passed"] = "passed"
    method: Literal["independent_native_step_geometry"] = "independent_native_step_geometry"
    width_mm: float
    height_mm: float
    thickness_mm: float
    holes: list[AdapterHoleMeasurement]
    minimum_web_mm: float
    expected_volume_mm3: float
    measured_volume_mm3: float
    native_geometry_signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class AdapterProposal(Model):
    project: Project
    request: AdapterRequest
    sources: list[InterfaceSignature]
    acceptance: AdapterAcceptance
    limits: list[str] = Field(default_factory=lambda: list(LIMITS) + [
        "Editable proposal only. Apply through Store with expected revision and locks; rebuild and inspect exported geometry before use.",
        "Plate and all holes are in a single common assembly XY plane. No threads, slots, counterbores, bends, noncoplanar mounting, or body collision verification.",
    ])
