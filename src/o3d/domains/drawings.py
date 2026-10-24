"""Versioned drawing/PMI intent. Importing this module never loads a CAD kernel.

Lengths, sheet positions and tolerances are stored in millimetres; units affect
presentation only. A published file is immutable and identifies both the exact
source geometry and the issuing project revision.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Id = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Point2 = tuple[float, float]
Point3 = tuple[float, float, float]


class Reference(Record):
    kind: Literal["point", "face", "edge", "hole", "parameter"] = "point"
    target: str = ""
    point: Point3 = (0, 0, 0)
    row: int = Field(default=0, ge=0)
    column: int = Field(default=0, ge=0)
    location: Literal["center", "min", "max"] = "center"


class Tolerance(Record):
    kind: Literal["none", "symmetric", "deviation", "limits", "fit"] = "none"
    upper_mm: float = Field(default=0, ge=0)
    lower_mm: float = Field(default=0, le=0)
    fit: Literal["H7", "g6", "H7/g6"] | None = None

    @model_validator(mode="after")
    def fit_required(self):
        if self.kind == "fit" and self.fit is None:
            raise ValueError("A fit tolerance requires a fit class; supported ISO 286 range is 3–30 mm")
        return self


class View(Record):
    id: Id
    kind: Literal["base", "projected", "section", "detail"] = "base"
    orientation: Literal["front", "back", "top", "bottom", "right", "left", "isometric"] = "front"
    parent: Id | None = None
    position: Point2 = (80, 100)
    scale: float = Field(default=1, gt=0, le=100)
    hidden: bool = True
    tangent: bool = False
    label: str = Field(default="", max_length=100)
    # Section plane equation n . (p-origin) = 0. The positive half is removed.
    section_origin: Point3 = (0, 0, 0)
    section_normal: Point3 = (0, 1, 0)
    exclude_bodies: list[str] = Field(default_factory=list, max_length=200)
    detail_center: Point2 = (0, 0)
    detail_radius: float = Field(default=10, gt=0)
    bodies: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def association(self):
        if self.kind != "base" and not self.parent:
            raise ValueError(f"{self.kind} view requires a parent")
        if self.kind == "section" and sum(x*x for x in self.section_normal) < 1e-12:
            raise ValueError("Section normal must be nonzero")
        return self


class Annotation(Record):
    id: Id
    view: Id
    kind: Literal["linear", "aligned", "angular", "radius", "diameter", "note", "center", "centerline", "datum", "gdt", "surface", "weld", "edge", "balloon", "hole_note", "thread_note"]
    references: list[Reference] = Field(default_factory=list, max_length=8)
    position: Point2 = (0, -10)
    axis: Literal["x", "y"] = "x"
    text: str = Field(default="", max_length=2000)
    tolerance: Tolerance = Field(default_factory=Tolerance)
    precision: int = Field(default=2, ge=0, le=6)
    dual_units: bool = False
    basic: bool = False
    datum: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}$")
    characteristic: Literal["position", "flatness", "straightness", "perpendicularity", "parallelism", "circularity", "cylindricity", "profile", "runout"] | None = None
    tolerance_mm: float | None = Field(default=None, gt=0)
    datums: list[str] = Field(default_factory=list, max_length=3)
    material_condition: Literal["RFS", "MMC", "LMC"] = "RFS"
    item: str | None = None

    @model_validator(mode="after")
    def semantics(self):
        needed = {"linear": 2, "aligned": 2, "angular": 3, "radius": 1, "diameter": 1,
                  "center": 1, "centerline": 2, "hole_note": 1, "thread_note": 1,
                  "datum": 1, "gdt": 1, "surface": 1, "weld": 1, "edge": 1, "balloon": 1}
        if len(self.references) < needed.get(self.kind, 0):
            raise ValueError(f"{self.kind} requires {needed[self.kind]} attached references")
        if self.kind == "datum" and not self.datum:
            raise ValueError("Datum symbol requires an identifier")
        if self.kind == "gdt":
            if self.characteristic is None or self.tolerance_mm is None:
                raise ValueError("A feature control frame requires a characteristic and tolerance")
            form = self.characteristic in ("flatness", "straightness", "circularity", "cylindricity")
            if form and self.datums:
                raise ValueError("Form tolerances cannot reference datums")
            if self.material_condition != "RFS" and self.characteristic not in ("position", "perpendicularity", "parallelism", "straightness"):
                raise ValueError("Material modifier is not supported for this characteristic")
        if self.kind == "balloon" and not self.item:
            raise ValueError("Balloon requires a stable BOM item key")
        if self.kind == "thread_note" and not self.text:
            raise ValueError("Thread designation must be supplied explicitly; threads are never inferred")
        return self


class Table(Record):
    id: Id
    kind: Literal["parts", "holes", "revision", "custom"]
    position: Point2 = (10, 20)
    columns: list[str] = Field(default_factory=list, max_length=12)
    rows: list[list[str]] = Field(default_factory=list, max_length=200)
    view: Id | None = None
    references: list[Reference] = Field(default_factory=list, max_length=200)


class Sheet(Record):
    id: Id
    name: str = Field(default="Sheet 1", max_length=100)
    size: Literal["A4", "A3", "A2", "Letter", "B", "custom"] = "A4"
    width_mm: float = Field(default=297, ge=50, le=2000)
    height_mm: float = Field(default=210, ge=50, le=2000)
    views: list[View] = Field(default_factory=list, max_length=40)
    annotations: list[Annotation] = Field(default_factory=list, max_length=1000)
    tables: list[Table] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def graph(self):
        ids = [v.id for v in self.views]
        if len(set(ids)) != len(ids):
            raise ValueError("View IDs must be unique within a sheet")
        prior = set()
        for view in self.views:
            if view.parent and view.parent not in prior:
                raise ValueError("Parent view must precede its child on the same sheet")
            prior.add(view.id)
        for a in self.annotations:
            if a.view not in prior:
                raise ValueError(f"Annotation {a.id} has a missing view")
        for table in self.tables:
            if table.view and table.view not in prior:
                raise ValueError(f"Table {table.id} has a missing view")
        return self


class Revision(Record):
    code: str = Field(max_length=20)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    description: str = Field(max_length=300)
    author: str = Field(default="", max_length=100)


class Document(Record):
    id: Id
    title: str = Field(default="Manufacturing drawing", max_length=200)
    number: str = Field(default="", max_length=80)
    standard: Literal["ISO", "ASME"] = "ISO"
    projection: Literal["first", "third"] = "first"
    units: Literal["mm", "inch"] = "mm"
    source_revision: int = Field(ge=1)
    source_geometry_key: str = Field(min_length=1, max_length=128)
    template: Id | None = None
    title_block: dict[str, str] = Field(default_factory=dict, max_length=30)
    revisions: list[Revision] = Field(default_factory=list, max_length=100)
    sheets: list[Sheet] = Field(default_factory=list, min_length=1, max_length=30)

    @model_validator(mode="after")
    def identifiers(self):
        if len({s.id for s in self.sheets}) != len(self.sheets):
            raise ValueError("Sheet IDs must be unique")
        return self


class Template(Record):
    id: Id
    name: str
    standard: Literal["ISO", "ASME"] = "ISO"
    projection: Literal["first", "third"] = "first"
    units: Literal["mm", "inch"] = "mm"
    sheet: Sheet = Field(default_factory=lambda: Sheet(id="sheet1"))
    title_block: dict[str, str] = Field(default_factory=dict)


class PMI(Record):
    id: Id
    kind: Literal["note", "hole_note", "thread_note", "datum", "size", "gdt"]
    reference: Reference
    plane_origin: Point3 = (0, 0, 0)
    plane_normal: Point3 = (0, 0, 1)
    plane_x: Point3 = (1, 0, 0)
    text: str = Field(default="", max_length=2000)
    tolerance: Tolerance = Field(default_factory=Tolerance)
    datum: str | None = None
    characteristic: str | None = None
    tolerance_mm: float | None = Field(default=None, gt=0)
    datums: list[str] = Field(default_factory=list, max_length=3)
    source_geometry_key: str
    source_revision: int = Field(ge=1)
    imported_entity: str | None = None

    @model_validator(mode="after")
    def plane(self):
        n, x = self.plane_normal, self.plane_x
        if sum(v*v for v in n) < 1e-10 or sum(v*v for v in x) < 1e-10:
            raise ValueError("Annotation plane axes must be nonzero")
        if abs(sum(a*b for a,b in zip(n,x))) > 1e-6:
            raise ValueError("Annotation plane x axis must be perpendicular to its normal")
        if self.kind == "datum" and not self.datum:
            raise ValueError("3D datum requires an identifier")
        if self.kind == "gdt" and (not self.characteristic or self.tolerance_mm is None):
            raise ValueError("3D GD&T requires characteristic and tolerance")
        return self


class Drawings(Record):
    schema_version: Literal[1] = 1
    documents: list[Document] = Field(default_factory=list, max_length=100)
    templates: list[Template] = Field(default_factory=list, max_length=100)
    pmi: list[PMI] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def unique(self):
        for name in ("documents", "templates", "pmi"):
            values = getattr(self, name)
            if len({v.id for v in values}) != len(values):
                raise ValueError(f"Duplicate {name} IDs")
        templates = {t.id for t in self.templates}
        for doc in self.documents:
            if doc.template and doc.template not in templates:
                raise ValueError(f"Missing template {doc.template}")
        return self


def validate(data: dict) -> dict:
    return Drawings.model_validate(data).model_dump(mode="json")


def data_for(project) -> Drawings:
    return Drawings.model_validate(getattr(project, "domains", {}).get("drawings", {"schema_version": 1}))


def execute(store, project, operation, payload, cancel=None) -> dict:
    """Kernel-heavy work is delayed until an explicit domain job is executed."""
    from .drawing_worker import execute as run
    return run(store, project, operation, payload or {}, cancel)


def read_step_pmi(text: str) -> dict:
    """Preserve the AP242 entity subgraph, explicitly reporting unmapped semantics.

    This is an inventory, not a claim that every AP242 tolerance has been decoded.
    Raw entity records and their transitive dependencies remain independently
    inspectable. Inspection mapping requires an explicit supported PMI attachment.
    """
    if len(text) > 64 * 1024 * 1024:
        raise ValueError("PMI inventory accepts STEP files up to 64 MiB")
    entities = dict(re.findall(r"(#\d+)\s*=\s*(.*?);", text, re.S))
    names = ("GEOMETRIC_TOLERANCE", "POSITION_TOLERANCE", "FLATNESS_TOLERANCE", "DATUM", "DIMENSIONAL_SIZE",
             "DIMENSIONAL_LOCATION", "PLUS_MINUS_TOLERANCE", "TOLERANCE_VALUE", "DRAUGHTING_CALLOUT",
             "ANNOTATION_TEXT", "GEOMETRIC_ITEM_SPECIFIC_USAGE")
    roots = {key for key, value in entities.items() if any(re.search(r"\b"+n+r"\b", value) for n in names)}
    todo, found = list(roots), set()
    while todo:
        key = todo.pop()
        if key in found or key not in entities:
            continue
        found.add(key)
        todo.extend(re.findall(r"#\d+", entities[key]))
    return {"schema_version": 1, "format": "STEP AP242 entity inventory", "root_entities": sorted(roots),
            "entities": {k: entities[k].strip() for k in sorted(found)}, "semantic_mapping": "requires_explicit_attachment",
            "omissions": ["No automatic datum/tolerance-to-topology mapping or AP242 semantic export is claimed."]}
