"""Create an inspectable two-standard drawing fixture in a NEW directory.

    PYTHONPATH=src .venv/bin/python examples/drawing_plate.py /tmp/plate-drawing

The dimensions/materials are test inputs, not a manufacturing authorization.
"""

import argparse
import json
from pathlib import Path

from o3d.domains.drawings import execute, validate
from o3d.model import Project, geometry_key
from o3d.store import Store


def create(destination: Path):
    project = Project.model_validate(
        {
            "name": "Plate drawing reference",
            "geometry_engine": "occt",
            "features": [
                {
                    "id": "outline",
                    "type": "profile",
                    "plane": "XY",
                    "width": 60,
                    "height": 40,
                },
                {
                    "id": "plate",
                    "type": "extrude",
                    "profile": "outline",
                    "distance": 10,
                },
                {
                    "id": "bores",
                    "type": "hole",
                    "axis": "Z",
                    "center": [-10, 0, 0],
                    "diameter": 10,
                    "columns": 2,
                    "spacing": [20, 0],
                },
            ],
        }
    )
    key = geometry_key(project)
    hole = {"kind": "hole", "target": "bores"}
    top_sheet = {
        "id": "dimensions",
        "name": "Dimensions",
        "size": "A4",
        "views": [{"id": "top", "orientation": "top", "position": [100, 85]}],
        "annotations": [
            {
                "id": "diameter",
                "kind": "diameter",
                "view": "top",
                "references": [hole],
                "position": [-40, -25],
                "precision": 3,
                "tolerance": {"kind": "fit", "fit": "H7"},
            },
            {
                "id": "pitch",
                "kind": "linear",
                "view": "top",
                "references": [hole, {**hole, "column": 1}],
                "position": [0, 30],
            },
            {
                "id": "datum",
                "kind": "datum",
                "view": "top",
                "references": [{"kind": "face", "target": "part:zmax"}],
                "datum": "A",
                "position": [35, 20],
            },
            {
                "id": "position",
                "kind": "gdt",
                "view": "top",
                "references": [hole],
                "characteristic": "position",
                "tolerance_mm": 0.02,
                "datums": ["A"],
                "material_condition": "MMC",
                "position": [35, -25],
            },
            {
                "id": "surface",
                "kind": "surface",
                "view": "top",
                "references": [{"kind": "face", "target": "part:zmax"}],
                "surface_roughness_um": 3.2,
                "surface_process": "required",
                "position": [35, 35],
            },
            {
                "id": "note",
                "kind": "note",
                "view": "top",
                "text": "REFERENCE FIXTURE — engineering review required",
                "position": [-80, 70],
            },
        ],
        "tables": [
            {
                "id": "holes",
                "kind": "holes",
                "view": "top",
                "position": [10, 20],
                "references": [hole, {**hole, "column": 1}],
            }
        ],
    }
    section_sheet = {
        "id": "sections",
        "name": "Sections",
        "size": "A3",
        "views": [
            {"id": "front", "orientation": "top", "position": [80, 90]},
            {
                "id": "cut",
                "kind": "section",
                "parent": "front",
                "orientation": "front",
                "position": [180, 90],
                "section_origin": [0, 0, 0],
                "section_normal": [0, 1, 0],
                "label": "A-A",
            },
            {
                "id": "detail",
                "kind": "detail",
                "parent": "cut",
                "position": [300, 90],
                "detail_center": [-10, 5],
                "detail_radius": 12,
                "scale": 2,
            },
        ],
        "tables": [{"id": "revisions", "kind": "revision", "position": [10, 200]}],
    }
    iso = {
        "id": "iso",
        "title": "Plate — reference drawing",
        "number": "O3D-PLATE-001",
        "source_revision": 1,
        "source_geometry_key": key,
        "standard": "ISO",
        "projection": "first",
        "units": "mm",
        "sheets": [top_sheet, section_sheet],
        "revisions": [
            {
                "code": "A",
                "date": "2026-10-24",
                "description": "Reference fixture",
                "author": "Oxygen",
            }
        ],
    }
    inch = {
        "id": "asme",
        "title": "Plate — inch reference",
        "number": "O3D-PLATE-IN",
        "source_revision": 1,
        "source_geometry_key": key,
        "standard": "ASME",
        "projection": "third",
        "units": "inch",
        "sheets": [
            {
                "id": "inch",
                "size": "Letter",
                "views": [{"id": "top", "orientation": "top", "position": [100, 90]}],
                "annotations": [
                    {
                        "id": "diameter",
                        "kind": "diameter",
                        "view": "top",
                        "references": [hole],
                        "position": [-40, -25],
                        "precision": 4,
                        "dual_units": True,
                        "tolerance": {
                            "kind": "deviation",
                            "upper_mm": 0.015,
                            "lower_mm": 0,
                        },
                    }
                ],
            }
        ],
    }
    project.domains["drawings"] = validate(
        {
            "schema_version": 1,
            "documents": [iso, inch],
            "templates": [{"id": "isoPlate", "name": "ISO plate", "sheet": top_sheet}],
            "pmi": [
                {
                    "id": "boreSize",
                    "kind": "size",
                    "reference": hole,
                    "plane_origin": [-10, 0, 10],
                    "source_revision": 1,
                    "source_geometry_key": key,
                    "tolerance": {
                        "kind": "deviation",
                        "upper_mm": 0.015,
                        "lower_mm": 0,
                    },
                }
            ],
        }
    )
    store = Store(destination)
    store.init(project)
    published = execute(store, project, "publish", {})
    print(
        json.dumps(
            {
                "project": str(store.root),
                "drawings": [
                    {
                        "id": d["document"],
                        "pdf": str(store.artifact(d["pdf"])),
                        "package": str(store.artifact(d["artifact"])),
                    }
                    for d in published["documents"]
                ],
                "mbd": execute(store, project, "export_mbd", {})["artifact"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    create(parser.parse_args().destination)
