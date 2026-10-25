"""Native viewport IDs must identify independently read STEP/Netgen faces."""
import build123d as bd
import numpy as np
import pytest

from o3d.domains.simulation import execute
from o3d.geometry import associate_native_faces, build, face_metadata
from o3d.model import Project, geometry_key
from o3d.native_document import execute_native, query_native
from o3d.simulation_models import AnalysisModel, LoadCase, MeshRecord, Region, SimulationDomain, StudyRecord, Support
from o3d.store import Store


def viewport_faces(scene):
    # Measure actual native triangle patches independently. Browser acceptance
    # exercises nativeFaces/picking; this backend test also runs without Node.
    output = []
    for body in scene["bodies"]:
        mesh = body["mesh"]
        points, triangles = np.array(mesh["positions"]), np.array(mesh["triangles"])
        faces = []
        for face_id in sorted(set(mesh["tri_face"])):
            patch = points[triangles[np.array(mesh["tri_face"]) == face_id]]
            crosses = np.cross(patch[:, 1] - patch[:, 0], patch[:, 2] - patch[:, 0])
            weights = np.linalg.norm(crosses, axis=1)
            normal = crosses.sum(axis=0)
            faces.append({"id": f"{body['id']}:face:{face_id}", "body_id": body["id"],
                "center": np.average(patch.mean(axis=1), axis=0, weights=weights),
                "normal": normal / np.linalg.norm(normal), "area_mm2": weights.sum() / 2})
        output.append({"id": body["id"], "faces": faces})
    return output


@pytest.mark.parametrize("element_family",["C3D4","C3D10"])
def test_canonical_viewport_faces_transfer_to_exact_transformed_fe_regions(tmp_path,element_family):
    store = Store(tmp_path)
    store.init(Project(name="Face association acceptance"))

    def edit(command, params):
        return execute_native(store, store.load().revision, "human", command, params)["result"]

    component = edit("component.create", {"name": "Fixture", "grounded": False})
    edit("solid.box", {"length": 10, "width": 20, "height": 30, "body_name": "Prism"})
    copied = edit("occurrence.copy", {"occurrence": component["occurrence"], "translate": [50, 0, 0]})
    edit("occurrence.move", {"occurrence": copied["occurrence"], "axis": [0, 0, 1], "angle": "30 deg"})
    project = store.load()
    picks = viewport_faces(query_native(project, "oxygen.scene", {"mesh": True}, store))
    built = build(store, project)
    bodies = {body["id"]: body for body in built["scene"]["bodies"]}
    selections = []
    for body in picks:
        exact = bodies[body["id"]]
        assert exact["face_association_issues"] == []
        assert len(body["faces"]) == 6
        for pick in body["faces"]:
            assert pick["body_id"] == body["id"]
            matches = [face for face in exact["faces"] if pick["id"] in face["aliases"]]
            assert len(matches) == 1
            np.testing.assert_allclose(matches[0]["center"], pick["center"], atol=1e-7)
            np.testing.assert_allclose(matches[0]["normal"], pick["normal"], atol=1e-7)
            assert abs(matches[0]["area_mm2"] - pick["area_mm2"]) < 1e-7
            # Both +/- local-X faces and top, on the original and rotated copy.
            if abs(pick["area_mm2"] - 600) < 1e-7 or pick["normal"][2] > .9:
                selections.append((body["id"], pick))
    assert len(selections) == 6
    regions = [Region(id=f"pick-{i}", body_id=body_id, selection=pick["id"])
               for i, (body_id, pick) in enumerate(selections)]
    domain = SimulationDomain(models=[AnalysisModel(id="model", name="Actual CAD", geometry_key=geometry_key(project),
        body_ids=list(bodies), regions=regions)], studies=[StudyRecord(id="study", name="Region transfer", model_id="model",
        cases=[LoadCase(id="case", name="Selected faces", supports=[Support(id=f"support-{i}", region_id=region.id)
            for i, region in enumerate(regions)])])], active_study_id="study")
    result = execute(store, project, "mesh", {"data": domain.model_dump(mode="json"), "mesh_size_mm": 6, "refinements": 1,"element_family":element_family})
    mesh = MeshRecord.model_validate(result["data"]["models"][0]["mesh"])
    for region, (_, pick) in zip(mesh.regions, selections):
        assert region.selection == pick["id"]
        assert region.body_id == pick["body_id"]
        triangles = np.array([[mesh.nodes[n] for n in triangle] for triangle in region.triangles])
        crosses = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        areas = np.linalg.norm(crosses, axis=1) / 2
        np.testing.assert_allclose(np.average(triangles.mean(axis=1), axis=0, weights=areas), pick["center"], atol=1e-7)
        np.testing.assert_allclose(crosses.sum(axis=0) / np.linalg.norm(crosses.sum(axis=0)), pick["normal"], atol=1e-7)
        assert abs(areas.sum() - pick["area_mm2"]) < 1e-7
        node_ids = {n for triangle in region.triangles for n in triangle}
        # Unweighted node centroids can move tangentially with the triangulation;
        # every node and its centroid must remain on the actually picked plane.
        points = np.array([mesh.nodes[n] for n in node_ids])
        assert np.max(np.abs((points - pick["center"]) @ pick["normal"])) < 1e-7


def test_ambiguous_native_patches_and_different_boundaries_get_no_alias():
    shape = bd.Box(10, 20, 30)
    descriptors = face_metadata(shape)
    face = next(face for face in descriptors if face["id"] == "part:zmax")
    body = {"id": "fixture", "mesh": {"positions": face["vertices"], "triangles": face["triangles"] * 2,
        "tri_face": [4] * len(face["triangles"]) + [9] * len(face["triangles"])}}
    associate_native_faces(body, descriptors)
    assert len(body["face_association_issues"]) == 2
    assert all(not alias.startswith("fixture:face:") for item in descriptors for alias in item["aliases"])

    # Same plane, normal, area and centroid, but a different trimmed surface.
    descriptors = face_metadata(bd.Pos(5, 10, 30) * bd.Rectangle(10, 20))
    diamond = {"id": "diamond", "mesh": {"positions": [[-15, 10, 30], [5, 5, 30], [25, 10, 30], [5, 15, 30]],
        "triangles": [[0, 1, 2], [0, 2, 3]], "tri_face": [7, 7]}}
    associate_native_faces(diamond, descriptors)
    assert len(diamond["face_association_issues"]) == 1
    assert all("diamond:face:7" not in item["aliases"] for item in descriptors)
