"""Canonical native geometry association acceptance, after core integration."""

import pytest
import math

from o3d.domains.drawings import execute
from o3d.model import Project, geometry_key
from o3d.store import Store

pytestmark = pytest.mark.skipif(
    "native_document" not in Project.model_fields,
    reason="Requires the centrally integrated native document envelope",
)


def test_native_named_diameter_updates_from_independent_exact_geometry(tmp_path):
    from o3d.domains import save_domain
    from o3d.native_document import execute_native

    store = Store(tmp_path)
    store.init(Project())
    reply = execute_native(
        store,
        1,
        "human",
        "solid.cylinder",
        {"radius": 5, "height": 10, "body_name": "Shaft"},
    )
    project = store.load()
    refs = execute(store, project, "references", {})["references"]
    attached = next(
        r["reference"]
        for r in refs
        if r["reference"]["kind"] == "native_face" and r["geometry"].get("cylinder")
    )
    data = {
        "schema_version": 1,
        "documents": [
            {
                "id": "shaft",
                "source_revision": project.revision,
                "source_geometry_key": geometry_key(project),
                "sheets": [
                    {
                        "id": "sheet1",
                        "views": [
                            {"id": "top", "orientation": "top", "position": [90, 80]}
                        ],
                        "annotations": [
                            {
                                "id": "diameter",
                                "kind": "diameter",
                                "view": "top",
                                "position": [25, -20],
                                "references": [attached],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    project = save_domain(store, "drawings", data, project.revision, "human")
    first = execute(store, project, "publish", {})["documents"][0]
    assert first["pages"][0]["measurements"][0]["value_mm"] == pytest.approx(10)
    native_feature = project.native_document["features"][0]["id"]
    execute_native(
        store,
        project.revision,
        "human",
        "timeline.edit",
        {"feature": native_feature, "set": {"radius": "8"}},
    )
    project = store.load()
    assert execute(store, project, "inspect", {})["documents"][0]["state"] == "stale"
    with pytest.raises(ValueError, match="stale"):
        execute(store, project, "publish", {})
    refreshed = execute(store, project, "refresh", {})
    assert not refreshed["documents"][0]["issues"]
    project = save_domain(
        store, "drawings", refreshed["data"], project.revision, "human"
    )
    current = execute(store, project, "publish", {})["documents"][0]
    assert current["pages"][0]["measurements"][0]["value_mm"] == pytest.approx(16)
    assert current["source_revision"] == project.revision - 1


def test_native_scale_does_not_reuse_legacy_hole_nominal(tmp_path):
    from o3d.native_document import execute_native
    from o3d.domains.drawing_worker import load_geometry, resolve
    from o3d.domains.drawings import Reference

    project = Project.model_validate(
        {
            "features": [
                {"id": "profile", "type": "profile", "width": 60, "height": 40},
                {
                    "id": "plate",
                    "type": "extrude",
                    "profile": "profile",
                    "distance": 10,
                },
                {
                    "id": "bore",
                    "type": "hole",
                    "axis": "Z",
                    "center": [0, 0, 0],
                    "diameter": 10,
                },
            ]
        }
    )
    store = Store(tmp_path)
    store.init(project)
    execute_native(
        store, 1, "human", "solid.scale", {"bodies": ["Oxygen part"], "factor": 2}
    )
    project = store.load()
    shape, _, bodies = load_geometry(store, project)
    with pytest.raises(ValueError, match="detached from the canonical"):
        resolve(Reference(kind="hole", target="bore"), project, shape, bodies)
    refs = execute(store, project, "references", {})["references"]
    cylinder = next(
        r
        for r in refs
        if r["reference"]["kind"] == "native_face" and r["geometry"].get("cylinder")
    )
    actual = resolve(
        Reference.model_validate(cylinder["reference"]), project, shape, bodies
    )
    assert actual["value_mm"] == pytest.approx(20)


def test_repeated_native_component_bom_and_balloon_are_associative(tmp_path):
    from o3d.domains import save_domain
    from o3d.native_document import execute_native

    store = Store(tmp_path)
    store.init(Project())

    def edit(command, params):
        return execute_native(store, store.load().revision, "human", command, params)[
            "result"
        ]

    made = edit("component.create", {"name": "Bracket", "grounded": False})
    edit(
        "solid.box",
        {
            "length": 20,
            "width": 10,
            "height": 3,
            "body_name": "BracketBody",
            "name": "BracketBox",
        },
    )
    edit("occurrence.copy", {"occurrence": made["occurrence"], "translate": [30, 0, 0]})
    edit(
        "component.metadata.set",
        {"component": made["component"], "part_number": "BRACKET-001"},
    )
    project = store.load()
    from o3d.domains.drawing_worker import load_geometry, bom_rows

    _, _, bodies = load_geometry(store, project)
    rows = bom_rows(store, project, bodies)
    assert len(rows) == 1 and rows[0]["quantity"] == 2
    reference = execute(store, project, "references", {})["references"][0]["reference"]
    key = rows[0]["key"]
    data = {
        "documents": [
            {
                "id": "assembly",
                "source_revision": project.revision,
                "source_geometry_key": geometry_key(project),
                "sheets": [
                    {
                        "id": "sheet1",
                        "views": [
                            {"id": "top", "orientation": "top", "position": [90, 80]}
                        ],
                        "annotations": [
                            {
                                "id": "balloon",
                                "view": "top",
                                "kind": "balloon",
                                "references": [reference],
                                "item": key,
                                "position": [30, -20],
                            }
                        ],
                        "tables": [
                            {"id": "parts", "kind": "parts", "position": [10, 130]}
                        ],
                    }
                ],
            }
        ]
    }
    project = save_domain(store, "drawings", data, project.revision, "human")
    page = execute(store, project, "publish", {})["documents"][0]["pages"][0]
    assert page["measurements"][0]["text"] == "1"
    assert page["bom"][0]["quantity"] == 2 and page["bom"][0]["key"] == key
    assert any(t["text"] == "2" for t in page["texts"])


def test_exact_assembly_section_excludes_fastener_from_cut(tmp_path):
    from o3d.domains import save_domain
    from o3d.native_document import execute_native
    from o3d.domains.drawing_worker import load_geometry

    project = Project.model_validate(
        {
            "features": [
                {"id": "outline", "type": "profile", "width": 30, "height": 30},
                {
                    "id": "housing",
                    "type": "extrude",
                    "profile": "outline",
                    "distance": 20,
                },
                {
                    "id": "bore",
                    "type": "hole",
                    "axis": "Z",
                    "center": [0, 0, 0],
                    "diameter": 8,
                },
            ]
        }
    )
    store = Store(tmp_path)
    store.init(project)
    execute_native(
        store,
        1,
        "human",
        "solid.cylinder",
        {"radius": 3, "height": 30, "body_name": "Fastener"},
    )
    execute_native(
        store,
        2,
        "human",
        "solid.move",
        {"bodies": ["Fastener"], "translate": [0, 0, -5]},
    )
    project = store.load()
    _, _, bodies = load_geometry(store, project)
    fastener = next(
        key for key, (body, _) in bodies.items() if body["name"] == "Fastener"
    )
    data = {
        "documents": [
            {
                "id": "section",
                "source_revision": project.revision,
                "source_geometry_key": geometry_key(project),
                "sheets": [
                    {
                        "id": "sheet1",
                        "views": [
                            {"id": "top", "orientation": "top", "position": [80, 90]},
                            {
                                "id": "cut",
                                "kind": "section",
                                "parent": "top",
                                "orientation": "front",
                                "position": [180, 90],
                                "exclude_bodies": [fastener],
                                "label": "A-A",
                            },
                        ],
                    }
                ],
            }
        ]
    }
    project = save_domain(store, "drawings", data, project.revision, "human")
    page = execute(store, project, "preview", {})["documents"][0]["pages"][0]
    expected = (30 * 30 * 20 - math.pi * 4**2 * 20) / 2 + math.pi * 3**2 * 30
    assert page["views"][1]["volume_mm3"] == pytest.approx(expected, rel=1e-6)
    assert any(path["style"] == "hatch" for path in page["paths"])
