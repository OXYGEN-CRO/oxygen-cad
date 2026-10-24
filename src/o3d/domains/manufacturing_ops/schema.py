"""Manufacturing process metadata; never a second CAD feature graph. Units are mm."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Positive = Annotated[float, Field(gt=0, le=1e6)]
Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class Machine(Model):
    id: ID
    name: str = Field(min_length=1, max_length=160)
    process: Literal["milling", "turning", "cutting", "fff"]
    post: Literal["linuxcnc_mill_v1", "linuxcnc_lathe_v1", "grbl_laser_v1", "cura_fff_v1"]
    travel_min: Vec3 = (-500, -500, -500)
    travel_max: Vec3 = (500, 500, 500)
    max_spindle_rpm: Positive = 10000
    max_feed_mm_min: Positive = 3000
    provenance: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check(self):
        if any(a >= b for a, b in zip(self.travel_min, self.travel_max)):
            raise ValueError("Machine travel minima must precede maxima")
        expected = {"milling": "linuxcnc_mill_v1", "turning": "linuxcnc_lathe_v1", "cutting": "grbl_laser_v1", "fff": "cura_fff_v1"}
        if self.post != expected[self.process]:
            raise ValueError("Post is incompatible with machine process")
        return self


class Tool(Model):
    id: ID
    name: str = Field(min_length=1, max_length=160)
    number: int = Field(ge=1, le=999)
    kind: Literal["flat_endmill", "drill", "turning_insert", "laser", "probe", "ball_endmill"]
    diameter: Positive
    flute_length: Positive = 20
    gauge_length: Positive = 40
    holder_diameter: Positive = 16
    spindle_rpm: Positive = 5000
    feed_mm_min: Positive = 300
    plunge_mm_min: Positive = 100
    provenance: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check(self):
        if self.gauge_length < self.flute_length:
            raise ValueError("Tool gauge length must cover flute length")
        return self


class Stock(Model):
    kind: Literal["box", "cylinder"] = "box"
    minimum: Vec3 = (0, 0, -10)
    maximum: Vec3 = (100, 60, 0)
    material: str = Field(default="Unspecified", min_length=1, max_length=160)
    thickness: Positive | None = None

    @model_validator(mode="after")
    def check(self):
        if any(a >= b for a, b in zip(self.minimum, self.maximum)):
            raise ValueError("Stock minimum must precede maximum")
        if self.kind == "cylinder" and abs((self.maximum[0]-self.minimum[0])-(self.maximum[1]-self.minimum[1])) > 1e-8:
            raise ValueError("Cylinder stock requires equal X/Y diameters; Z is spindle axis")
        return self


class Fixture(Model):
    id: ID
    name: str = Field(min_length=1, max_length=160)
    minimum: Vec3
    maximum: Vec3

    @model_validator(mode="after")
    def check(self):
        if any(a >= b for a, b in zip(self.minimum, self.maximum)):
            raise ValueError("Fixture bounds are reversed")
        return self


class Setup(Model):
    id: ID
    name: str = Field(min_length=1, max_length=160)
    machine_id: ID
    stock: Stock = Field(default_factory=Stock)
    fixtures: list[Fixture] = Field(default_factory=list, max_length=100)
    wcs_origin: Vec3 = (0, 0, 0)
    wcs_rotation_z_deg: float = Field(default=0, ge=-360, le=360)
    work_offset: int = Field(default=54, ge=54, le=59)
    clearance_z: float = 5
    tolerance_mm: Positive = 0.05
    model_refs: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check(self):
        if self.clearance_z <= self.stock.maximum[2]:
            raise ValueError("Clearance must be above stock")
        return self


STRATEGIES = Literal["face", "pocket", "contour", "drill", "turn_face", "turn_profile", "turn_groove", "part_off", "laser_profile", "parallel_finish", "rough_3d", "rest_3d"]


class Operation(Model):
    id: ID
    name: str = Field(min_length=1, max_length=160)
    setup_id: ID
    tool_id: ID
    strategy: STRATEGIES
    boundary: list[Vec2] = Field(default_factory=list, max_length=10000)
    islands: list[list[Vec2]] = Field(default_factory=list, max_length=100)
    holes: list[Vec2] = Field(default_factory=list, max_length=1000)
    top_z: float = 0
    bottom_z: float = -2
    stepdown: Positive = 1
    stepover: float = Field(default=0.4, gt=0, le=0.8)
    allowance_mm: float = Field(default=0, ge=0, le=100)
    peck_mm: float = Field(default=2, gt=0, le=1000)
    # Turning coordinates are [axial Z, radius]; only externally accessible monotone OD.
    profile_zr: list[Vec2] = Field(default_factory=list, max_length=1000)
    nose_radius_mm: float = Field(default=0, ge=0, le=100)
    # Tab intervals are fractions of contour arclength, lifted by tab_height_mm.
    tab_centers: list[float] = Field(default_factory=list, max_length=100)
    tab_width_mm: Positive = 3
    tab_height_mm: Positive = 1
    lead_in_mm: float = Field(default=0, ge=0, le=100)
    depends_on: list[ID] = Field(default_factory=list, max_length=100)
    # Heightfield finishing: heights[row y][column x]; positive stock-space Z.
    surface_x: list[float] = Field(default_factory=list, max_length=200)
    surface_y: list[float] = Field(default_factory=list, max_length=200)
    surface_heights: list[list[float]] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check(self):
        if self.bottom_z >= self.top_z:
            raise ValueError("Operation bottom must be below top")
        if any(not 0 <= t < 1 for t in self.tab_centers):
            raise ValueError("Tab centers must be contour fractions in [0,1)")
        if self.nose_radius_mm:
            raise ValueError("Turning nose compensation is not qualified; use a zero-radius reference tool or external post")
        if self.lead_in_mm:
            raise ValueError("Custom lead-ins are not qualified; this version retracts and plunges")
        if self.strategy in {"face", "pocket", "contour", "laser_profile"} and len(self.boundary) < 3:
            raise ValueError("Planar machining requires an explicit closed boundary")
        if self.strategy == "drill" and not self.holes:
            raise ValueError("Drilling requires hole centers")
        if self.strategy.startswith("turn_") or self.strategy == "part_off":
            if len(self.profile_zr) < 2 or any(r <= 0 for _, r in self.profile_zr):
                raise ValueError("Turning requires positive [Z, radius] profile points")
            if any(a[0] <= b[0] for a, b in zip(self.profile_zr, self.profile_zr[1:])):
                raise ValueError("Turning OD profile must run from front toward chuck in strictly decreasing Z")
        return self


class NestPart(Model):
    id: ID
    boundary: list[Vec2] = Field(min_length=3, max_length=10000)
    holes: list[list[Vec2]] = Field(default_factory=list, max_length=100)
    quantity: int = Field(default=1, ge=1, le=100)
    rotations_deg: list[float] = Field(default_factory=lambda: [0, 90], min_length=1, max_length=16)
    material: str = Field(min_length=1, max_length=160)
    thickness_mm: Positive


class Nest(Model):
    sheet_width_mm: Positive = 300
    sheet_height_mm: Positive = 200
    margin_mm: float = Field(default=5, ge=0, le=1000)
    gap_mm: float = Field(default=2, ge=0, le=1000)
    kerf_mm: float = Field(default=0.2, ge=0, le=100)
    max_sheets: int = Field(default=10, ge=1, le=100)
    material: str = "Steel"
    thickness_mm: Positive = 2
    parts: list[NestPart] = Field(default_factory=list, max_length=100)


class Printer(Model):
    machine_id: ID
    technology: Literal["fff"] = "fff"
    layer_height_mm: float = Field(default=0.2, gt=0.02, le=1)
    nozzle_diameter_mm: float = Field(default=0.4, gt=0.1, le=2)
    filament_diameter_mm: float = Field(default=1.75, gt=0.5, le=4)
    temperature_c: float = Field(default=200, ge=0, le=500)
    bed_temperature_c: float = Field(default=60, ge=0, le=200)
    infill_percent: float = Field(default=20, ge=0, le=100)
    supports: bool = False
    support_angle_deg: float = Field(default=50, gt=0, lt=90)
    wall_count: int = Field(default=2, ge=1, le=20)
    print_speed_mm_s: float = Field(default=40, gt=0, le=300)
    material: str = Field(default="PLA", min_length=1, max_length=160)
    profile_source: str = Field(min_length=1, max_length=500)
    orientation_deg: Vec3 = (0, 0, 0)
    center_xy: Vec2 | None = None


class PlasticsRules(Model):
    material: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=500)
    min_wall_mm: Positive = 1
    max_wall_mm: Positive = 4
    min_draft_deg: float = Field(default=1, ge=0, le=45)
    pull_direction: Vec3 = (0, 0, 1)

    @model_validator(mode="after")
    def check(self):
        if self.min_wall_mm >= self.max_wall_mm:
            raise ValueError("Plastic minimum wall must be below maximum")
        if sum(v*v for v in self.pull_direction) < 1e-10:
            raise ValueError("Pull direction must be nonzero")
        return self


class Manufacturing(Model):
    schema_version: Literal[1] = 1
    machines: list[Machine] = Field(default_factory=list, max_length=100)
    tools: list[Tool] = Field(default_factory=list, max_length=500)
    setups: list[Setup] = Field(default_factory=list, max_length=100)
    operations: list[Operation] = Field(default_factory=list, max_length=1000)
    nesting: Nest | None = None
    printer: Printer | None = None
    plastics: PlasticsRules | None = None

    @model_validator(mode="after")
    def references(self):
        for name in ["machines", "tools", "setups", "operations"]:
            items = getattr(self, name)
            if len({x.id for x in items}) != len(items):
                raise ValueError(f"Duplicate {name} IDs")
        if len({tool.number for tool in self.tools}) != len(self.tools):
            raise ValueError("Tool numbers must be unique in the saved process library")
        machines, tools, setups = ({x.id for x in getattr(self, n)} for n in ["machines", "tools", "setups"])
        for setup in self.setups:
            if setup.machine_id not in machines:
                raise ValueError(f"Setup {setup.id} references a missing machine")
        seen = set()
        for op in self.operations:
            if op.setup_id not in setups or op.tool_id not in tools:
                raise ValueError(f"Operation {op.id} references a missing setup/tool")
            if not set(op.depends_on).issubset(seen):
                raise ValueError("Operation dependencies must precede the operation (cycles/forward refs are forbidden)")
            seen.add(op.id)
        if self.printer and self.printer.machine_id not in machines:
            raise ValueError("Printer references a missing machine")
        return self
