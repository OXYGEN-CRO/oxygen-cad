"""Exact CAD compilation and measured, face-addressable viewport artifacts."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import build123d as bd
import numpy as np

from .model import EdgeTreatment, Extrude, Finding, Hole, Profile, Project, digest, geometry_key, verification_key
from .store import Store, write_json


def vector(value):
    return [float(value.X), float(value.Y), float(value.Z)]


def signature(shape):
    c = vector(shape.center())
    values = [shape.geom_type.name, *[round(x, 6) for x in c]]
    if isinstance(shape, bd.Face):
        values += [round(shape.area, 6)]
        if shape.geom_type == bd.GeomType.PLANE:
            values += [round(x, 6) for x in vector(shape.normal_at())]
    else:
        values += [round(shape.length, 6)]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()[:20]


def face_map(shape):
    """Semantic extreme planes, with strict signatures for all other faces.

    A semantic role is exposed only when it resolves to exactly one face.
    No list index is ever used as a persistent native selection.
    """
    faces = list(shape.faces())
    result = {f"face:{signature(f)}": f for f in faces}
    box = shape.bounding_box()
    for axis, coord in enumerate("xyz"):
        for side, bound in (("min", vector(box.min)[axis]), ("max", vector(box.max)[axis])):
            selected = [f for f in faces if f.geom_type == bd.GeomType.PLANE
                        and abs(abs(vector(f.normal_at())[axis]) - 1) < 1e-6
                        and abs(vector(f.center())[axis] - bound) < 1e-5]
            if len(selected) == 1:
                result[f"part:{coord}{side}"] = selected[0]
    return result


def resolve_face(shape, name):
    mapping = face_map(shape)
    if name not in mapping:
        raise ValueError(f"Missing or ambiguous face reference {name}; explicitly select a replacement")
    return mapping[name]


def select_edges(shape, selection):
    edges = list(shape.edges())
    if selection == "part:all_edges":
        return edges
    if selection == "part:vertical_edges":
        return [e for e in edges if e.geom_type == bd.GeomType.LINE and abs(abs(vector(e.tangent_at())[2]) - 1) < 1e-6]
    if selection in ("part:top_edges", "part:bottom_edges"):
        face = resolve_face(shape, "part:zmax" if selection == "part:top_edges" else "part:zmin")
        return list(face.edges())
    matches = [e for e in edges if f"edge:{signature(e)}" == selection]
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous edge reference {selection}; select a replacement")
    return matches


def compile_occt_shape(project: Project):
    profiles = {}
    shape = None
    for feature in project.features:
        if feature.suppressed:
            continue
        try:
            if isinstance(feature, Profile):
                plane = {"XY": bd.Plane.XY, "XZ": bd.Plane.XZ, "YZ": bd.Plane.YZ}[feature.plane]
                if feature.shape == "rectangle":
                    sketch = bd.Rectangle(project.resolve(feature.width), project.resolve(feature.height))
                elif feature.shape == "circle":
                    sketch = bd.Circle(project.resolve(feature.radius))
                else:
                    sketch = bd.Polygon(*feature.points, align=None)
                profiles[feature.id] = (plane * sketch).moved(bd.Location(feature.origin, feature.rotation))
            elif isinstance(feature, Extrude):
                if feature.profile not in profiles:
                    raise ValueError("The referenced profile is suppressed or unavailable")
                body = bd.extrude(profiles[feature.profile], amount=project.resolve(feature.distance))
                if shape is None:
                    if feature.operation == "cut":
                        raise ValueError("The first extrusion must add material")
                    shape = body
                else:
                    old_volume = shape.volume
                    shape = shape.fuse(body) if feature.operation == "add" else shape.cut(body)
                    if abs(shape.volume - old_volume) < 1e-8:
                        raise ValueError("Operation made no measured change to the solid")
            elif isinstance(feature, Hole):
                if shape is None:
                    raise ValueError("A hole needs an existing solid")
                axis = "XYZ".index(feature.axis)
                box = shape.bounding_box()
                low, high = vector(box.min), vector(box.max)
                other_axes = [i for i in range(3) if i != axis]
                old_volume = shape.volume
                for row in range(feature.rows):
                    for column in range(feature.columns):
                        center = list(feature.center)
                        center[other_axes[0]] += column * feature.spacing[0]
                        center[other_axes[1]] += row * feature.spacing[1]
                        if feature.through:
                            center[axis] = low[axis] - 1
                            depth = high[axis] - low[axis] + 2
                            normal = [int(i == axis) for i in range(3)]
                        else:
                            depth = project.resolve(feature.depth)
                            normal = [(-1 if feature.reverse else 1) * int(i == axis) for i in range(3)]
                        cutter = bd.Plane(origin=center, z_dir=normal) * bd.Cylinder(project.resolve(feature.diameter) / 2, depth, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
                        shape = shape.cut(cutter)
                if old_volume - shape.volume < 1e-8:
                    raise ValueError("Hole pattern did not intersect the solid")
            elif isinstance(feature, EdgeTreatment):
                if shape is None:
                    raise ValueError("Edge treatment needs an existing solid")
                edges = select_edges(shape, feature.selection)
                if not edges:
                    raise ValueError("Edge selection is empty")
                size = project.resolve(feature.size)
                shape = shape.fillet(size, edges) if feature.type == "fillet" else shape.chamfer(size, None, edges)
            elif feature.type == "native":
                raise ValueError("Native command features require geometry_engine=solidcraft")
            if shape is not None and not shape.is_valid:
                raise ValueError("Kernel reports invalid geometry")
        except Exception as error:
            raise ValueError(f"Feature {feature.id}: {error}") from error
    if shape is None or len(shape.solids()) != 1 or shape.volume <= 0:
        raise ValueError("The project must produce exactly one connected, positive-volume solid")
    return shape.moved(bd.Location(project.placement.translation, project.placement.rotation))


def compile_shape(project: Project):
    if project.geometry_engine == "occt":
        return compile_occt_shape(project)
    import tempfile
    from .native import compile_native
    with tempfile.TemporaryDirectory(prefix="oxygen-geometry-") as folder:
        return compile_native(project, Path(folder))[0]


def face_metadata(shape):
    mapping = face_map(shape)
    output = []
    for face in shape.faces():
        strict_id = f"face:{signature(face)}"
        roles = [name for name, value in mapping.items() if name.startswith("part:") and value.is_same(face)]
        vertices, triangles = face.tessellate(0.15, 0.15)
        output.append({"id": roles[0] if roles else strict_id, "signature": strict_id,
                       "aliases": roles, "kind": face.geom_type.name, "area_mm2": face.area,
                       "center": vector(face.center()), "normal": vector(face.normal_at()) if face.geom_type == bd.GeomType.PLANE else None,
                       "vertices": [vector(v) for v in vertices], "triangles": [list(t) for t in triangles]})
    return output


def associate_native_faces(body, descriptors):
    """Attach native pick IDs only to uniquely verified planar STEP faces.

    Native triangle face numbers and STEP traversal order are independent. Match
    directed plane, area and centroid, then compare the complete trimmed planar
    footprints. Curved/ambiguous/unverifiable patches deliberately get no alias.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from shapely.errors import GEOSException

    distance_tolerance = 1e-6  # mm; stricter than the subsequent Netgen transfer.
    angular_tolerance = 1e-7
    mesh = body.get("mesh") or {}
    points = np.asarray(mesh.get("positions", []), dtype=float)
    triangles = mesh.get("triangles", [])
    face_ids = mesh.get("tri_face", [])
    issues, groups = [], {}
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all() or len(face_ids) != len(triangles):
        body["face_association_issues"] = [{"reason": "Native mesh has no verifiable face identities"}]
        return
    for triangle, face_id in zip(triangles, face_ids):
        groups.setdefault(face_id, []).append(triangle)

    def footprint(vertices, indices, origin, normal):
        if len(indices) > 50_000:
            raise ValueError("Planar face association exceeds its triangle budget")
        tangent = np.cross(normal, np.eye(3)[np.argmin(np.abs(normal))])
        tangent /= np.linalg.norm(tangent)
        other = np.cross(normal, tangent)
        projected = (vertices - origin) @ np.array([tangent, other]).T
        polygons = [Polygon(projected[t]) for t in indices]
        if any(not p.is_valid or p.area <= 0 for p in polygons):
            raise ValueError("Degenerate planar triangle footprint")
        merged = unary_union(polygons)
        if not merged.is_valid or merged.is_empty:
            raise ValueError("Invalid planar triangle footprint")
        # Overlapping triangles must not balance missing area elsewhere.
        if abs(sum(p.area for p in polygons) - merged.area) > max(1e-6, merged.area * 1e-8):
            raise ValueError("Overlapping native face triangles")
        return merged

    candidates = {}
    for face_id, indices in groups.items():
        pick_id = f"{body['id']}:face:{face_id}"
        try:
            indices = np.asarray(indices)
            if indices.ndim != 2 or indices.shape[1] != 3 or not np.issubdtype(indices.dtype, np.integer) or np.any(indices < 0) or np.any(indices >= len(points)):
                raise ValueError("Invalid native triangle connectivity")
            patch = points[indices]
            crosses = np.cross(patch[:, 1] - patch[:, 0], patch[:, 2] - patch[:, 0])
            sizes = np.linalg.norm(crosses, axis=1)
            if np.any(sizes <= 1e-12):
                raise ValueError("Degenerate native face triangles")
            area = float(sizes.sum() / 2)
            center = np.average(patch.mean(axis=1), axis=0, weights=sizes)
            normal = crosses.sum(axis=0)
            normal_size = np.linalg.norm(normal)
            if normal_size <= 1e-12:
                raise ValueError("Native patch has no directed planar normal")
            normal /= normal_size
            if not np.isfinite(normal).all() or np.max(np.linalg.norm(crosses / sizes[:, None] - normal, axis=1)) > angular_tolerance:
                raise ValueError("Native patch is not a directed planar face")
            matches = []
            for index, face in enumerate(descriptors):
                if face["kind"] != "PLANE" or face.get("normal") is None:
                    continue
                area_tolerance = max(1e-6, face["area_mm2"] * 1e-8)
                exact_center, exact_normal = np.asarray(face["center"]), np.asarray(face["normal"])
                if abs(face["area_mm2"] - area) > area_tolerance or np.linalg.norm(exact_center - center) > distance_tolerance or np.linalg.norm(exact_normal - normal) > angular_tolerance:
                    continue
                if np.max(np.abs((patch.reshape(-1, 3) - exact_center) @ exact_normal)) > distance_tolerance:
                    continue
                native = footprint(points, indices, exact_center, exact_normal)
                exact = footprint(np.asarray(face["vertices"]), np.asarray(face["triangles"]), exact_center, exact_normal)
                if native.symmetric_difference(exact).area <= area_tolerance and native.boundary.hausdorff_distance(exact.boundary) <= distance_tolerance:
                    matches.append(index)
            candidates[pick_id] = matches
        except (ValueError, GEOSException) as error:
            issues.append({"native_face_id": pick_id, "reason": str(error)})
    # A unique forward candidate is insufficient if another native patch claims it.
    uses = {}
    for matches in candidates.values():
        for index in matches:
            uses[index] = uses.get(index, 0) + 1
    for pick_id, matches in candidates.items():
        if len(matches) == 1 and uses[matches[0]] == 1:
            face = descriptors[matches[0]]
            face["aliases"] = list(dict.fromkeys([*face.get("aliases", []), pick_id]))
        else:
            issues.append({"native_face_id": pick_id, "reason": "No unique verified planar STEP face"})
    body["face_association_issues"] = issues


def load_reference(store: Store, reference):
    path = store.root / reference.file
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != reference.sha256:
        raise ValueError(f"Reference {reference.id} is missing or its immutable asset hash changed")
    shape = bd.import_step(path)
    if not shape.is_valid or not shape.solids():
        raise ValueError(f"Reference {reference.id} contains no valid solids")
    return shape.moved(bd.Location(reference.placement.translation, reference.placement.rotation))


def build(store: Store, project: Project):
    key = geometry_key(project)
    destination = store.cache / "builds" / key
    destination.mkdir(parents=True, exist_ok=True)
    if project.geometry_engine == "solidcraft":
        from .native import compile_native
        shape, engine = compile_native(project, destination.resolve())
    else:
        shape, engine = compile_occt_shape(project), {"engine": "occt"}
    scene = engine.pop("scene", None)
    engine.pop("canonical", None)
    engine.pop("placed_canonical", None)
    artifacts = {}
    if shape is not None:
        if not bd.export_brep(shape, destination / "part.brep"):
            raise ValueError("BREP export failed")
        if project.geometry_engine == "occt":
            bd.export_step(shape, destination / "part.step")
        if project.geometry_engine == "occt":
            bd.export_stl(shape, destination / "part.stl", tolerance=0.05, angular_tolerance=0.1)
        artifacts.update({name: f"builds/{key}/part.{extension}" for name, extension in (("step", "step"), ("stl", "stl"), ("brep", "brep"))})
    if (destination / "part.stl").is_file():
        artifacts["stl"] = f"builds/{key}/part.stl"
    faces = face_metadata(shape) if shape is not None else []
    if scene:
        # Preserve body-specific identities/topology alongside legacy aggregate viewport data.
        for body, independent in zip([b for b in scene["bodies"] if b["geometry_kind"] != "mesh"], engine["independent_bodies"]):
            body_shape = bd.import_step(destination / independent["step"])
            body["faces"] = face_metadata(body_shape)
            for face in body["faces"]:
                face["body_id"] = body["id"]
            associate_native_faces(body, body["faces"])
            body["artifacts"] = {"step": f"builds/{key}/{independent['step']}"}
        write_json(destination / "scene.json", scene)
        artifacts["scene"] = f"builds/{key}/scene.json"
    references = []
    for ref in project.references:
        other = load_reference(store, ref)
        ref_faces = face_metadata(other)
        for index, face in enumerate(ref_faces):
            face["id"] = f"asset:{ref.sha256}:{index}"
        references.append({"id": ref.id, "name": ref.name, "faces": ref_faces, "visible": ref.visible})
    has_mesh = bool(scene and any(b["geometry_kind"] == "mesh" for b in scene["bodies"]))
    has_surface = bool(scene and any(b["geometry_kind"] == "surface" for b in scene["bodies"]))
    if scene and scene["bodies"]:
        low = [min(b["bbox"]["min"][i] for b in scene["bodies"]) for i in range(3)]
        high = [max(b["bbox"]["max"][i] for b in scene["bodies"]) for i in range(3)]
        bounds, dimensions = [low, high], [high[i]-low[i] for i in range(3)]
        volume = sum(s.volume for s in shape.solids()) if shape is not None and not has_mesh and not has_surface else None
        center = vector(shape.center(bd.CenterOf.MASS)) if shape is not None and volume and not has_surface else None
        if has_mesh:
            # Aggregate exact-format buttons must never silently omit mesh bodies.
            artifacts.pop("step", None)
            artifacts.pop("brep", None)
    elif shape is not None:
        bbox = shape.bounding_box()
        bounds = [vector(bbox.min), vector(bbox.max)]
        dimensions = vector(bbox.size)
        volume = sum(s.volume for s in shape.solids())
        center = vector(shape.center(bd.CenterOf.MASS)) if volume > 0 else vector(shape.center())
    else:
        bounds, dimensions, volume, center = [[0,0,0],[0,0,0]], [0,0,0], 0, [0,0,0]
    result = {"key": key, "revision": project.revision, "engine": engine, "volume_mm3": volume,
              "dimensions_mm": dimensions, "bounds": bounds, "center_of_mass": center,
              "faces": faces, "references": references, "scene": scene,
              "edges": [{"id": f"edge:{signature(e)}", "center": vector(e.center()), "length_mm": e.length} for e in shape.edges()] if shape is not None else [],
              "artifacts": artifacts}
    if scene:
        all_ids = [b["id"] for b in scene["bodies"]]
        brep_ids = [b["id"] for b in scene["bodies"] if b["geometry_kind"] != "mesh"]
        result["artifact_scope"] = {"stl": {"body_ids":all_ids},
            "exact_per_body": {"body_ids":brep_ids,"excluded_mesh_body_ids":[b["id"] for b in scene["bodies"] if b["geometry_kind"] == "mesh"]}}
        result["exact_solid_volume_mm3"] = sum(s.volume for s in shape.solids()) if shape is not None else 0
    if project.geometry_engine == "solidcraft":
        result["artifacts"].update({"native_design": f"builds/{key}/part.solidcraft", "engine_script": f"builds/{key}/engine-script.json"})
    write_json(destination / "build.json", result)
    write_json(store.cache / "last-build.json", result)
    if project.native_document is not None:
        from .build_summary import publish_build_summary
        publish_build_summary(destination / "build.json", result)
        publish_build_summary(store.cache / "last-build.json", result)
    return result


CANONICAL_MASS_BASIS = "canonical-native-body-materials-v1"


def canonical_mass(store: Store, project: Project, cached: dict, shape):
    """Measure assigned native materials; the engine's Default density is not input.

    A fresh native query avoids trusting an old build's material measurements.
    Each current world body must map uniquely to an explicitly assigned definition
    and an independently exported exact solid. No partial total can certify mass.
    """
    from .native import UPSTREAM_COMMIT
    from .native_document import query_native

    evidence = {"basis": CANONICAL_MASS_BASIS, "geometry_key": geometry_key(project),
                "native_document_sha256": digest(project.native_document),
                "native_engine": {"upstream_commit": UPSTREAM_COMMIT, "protocol": 1},
                "source": "Current native inspect.measure and explicit native_document.materials assignments",
                "bodies": [], "issues": [], "state": "needs_input"}
    try:
        if cached.get("key") != evidence["geometry_key"]:
            raise ValueError("Cached solid belongs to different geometry")
        measured = query_native(project, "inspect.measure", store=store)
        evidence["measurements_sha256"] = digest(measured)
        bodies = (cached.get("scene") or {}).get("bodies", [])
        rows = measured.get("bodies", [])
        if not bodies or not isinstance(rows, list) or len(bodies) != len(rows):
            raise ValueError("Native mass measurements do not cover every current solid body")
        if len({b["name"] for b in bodies}) != len(bodies) or len({r["name"] for r in rows}) != len(rows):
            raise ValueError("Native mass body identities are ambiguous")
        assignments = project.native_document.get("materials", {})
        if not isinstance(assignments, dict):
            raise ValueError("Native material assignments are unavailable")
        for body in bodies:
            name = body["name"]
            definition = body.get("definition_name") or name
            row = next((r for r in rows if r.get("name") == name), None)
            assigned = assignments.get(definition)
            if (row is None or not isinstance(assigned, str) or not assigned.strip()
                    or assigned.casefold() == "default" or row.get("material", "").casefold() == "default"):
                evidence["issues"].append(f"Body {name} needs an explicit usable native material assignment; Default is not engineering input")
                continue
            if row.get("material", "").casefold() != assigned.casefold():
                evidence["issues"].append(f"Body {name} material differs from its canonical assignment")
                continue
            values = [row.get(k) for k in ("volume_mm3", "density_g_cm3", "mass_g")]
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in values):
                evidence["issues"].append(f"Body {name} has no finite positive measured volume, assigned density and mass")
                continue
            volume, density, mass = values
            if row.get("geometry_kind") != "solid" or not math.isclose(mass, volume * density / 1000, rel_tol=1e-9, abs_tol=1e-12):
                evidence["issues"].append(f"Body {name} has inconsistent native solid mass measurements")
                continue
            independent = bd.import_step(store.artifact(body["artifacts"]["step"]))
            if not independent.solids() or not math.isclose(volume, independent.volume, rel_tol=1e-3, abs_tol=1e-8):
                evidence["issues"].append(f"Body {name} native volume disagrees with its independent exact solid")
                continue
            evidence["bodies"].append({"body_id": body["id"], "name": name, "definition_name": definition,
                "material": row["material"], "density_g_cm3": density, "volume_mm3": volume, "mass_g": mass,
                "assignment_source": f"native_document.materials[{definition}]",
                "density_source": "Explicitly selected material in the pinned native library or saved native user_materials; not a material certificate"})
        if evidence["issues"] or len(evidence["bodies"]) != len(bodies):
            return None, evidence
        if not math.isclose(sum(b["volume_mm3"] for b in evidence["bodies"]), shape.volume, rel_tol=1e-3, abs_tol=1e-8):
            raise ValueError("Native body volumes disagree with the checked aggregate solid")
        mass = sum(b["mass_g"] for b in evidence["bodies"])
        if not math.isfinite(mass) or mass <= 0:
            raise ValueError("Canonical total mass is not finite and positive")
        evidence.update(state="complete", mass_g=mass)
        return mass, evidence
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        evidence["issues"].append(f"Canonical mass evidence unavailable: {error}")
        return None, evidence


def check(store: Store, project: Project):
    key = geometry_key(project)
    path = store.cache / "builds" / key
    if not (path / "build.json").exists():
        build(store, project)
    if not (path / "part.brep").exists():
        raise ValueError("Engineering checks require B-rep geometry; mesh-only and sketch-only documents are not solids")
    cached = json.loads((path / "build.json").read_text())
    if any(b["geometry_kind"] != "solid" for b in (cached.get("scene") or {}).get("bodies", [])):
        raise ValueError("Solid engineering checks require only exact solid bodies; use mesh/surface diagnostics for mixed documents")
    shape = bd.import_brep(path / "part.brep")
    if not shape.solids() or shape.volume <= 0:
        raise ValueError("Solid engineering checks require positive-volume solid bodies; use surface diagnostics for open surfaces")
    mass_evidence = None
    if project.native_document is not None:
        mass_g, mass_evidence = canonical_mass(store, project, cached, shape)
    else:
        mass_g = shape.volume * project.material.density_g_cm3 / 1000 if project.material else None
    findings = [Finding(code="valid_solid", severity="info", message=f"{len(shape.solids())} valid solid bodies", value=shape.volume)]
    import trimesh
    mesh = trimesh.load(path / "part.stl", force="mesh")
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        findings.append(Finding(code="mesh_integrity", severity="error", message="Independent exported-mesh audit failed"))
    mesh_error = abs(abs(mesh.volume) - shape.volume) / shape.volume
    if mesh_error > 0.01:
        findings.append(Finding(code="volume_mismatch", severity="error", message="Exported mesh and exact-solid volume differ by more than 1%", value=mesh_error, limit=0.01))
    refs = {}
    for ref in project.references:
        other = load_reference(store, ref)
        clearance = shape.distance_to(other)
        intersection = shape.intersect(other)
        interference = sum(abs(s.volume) for s in intersection) if isinstance(intersection, (list, bd.ShapeList)) else abs(intersection.volume) if intersection else 0
        refs[ref.id] = {"clearance_mm": clearance, "interference_mm3": interference}
        if ref.check_interference and interference > 1e-5:
            findings.append(Finding(code="interference", severity="error", feature=ref.id, message=f"Interference with {ref.name}", value=interference, limit=0))
    for req in project.requirements:
        if not req.enabled or req.kind in ("max_displacement", "max_temperature", "min_frequency", "min_buckling_factor", "max_stress"):
            continue
        if req.kind == "max_mass":
            if mass_g is None:
                reason = "; ".join(mass_evidence["issues"]) if mass_evidence else "Mass requirement needs material density"
                findings.append(Finding(code="missing_material", severity="warning", feature=req.id, message=reason))
                continue
            value = mass_g
            passed = value <= req.value
        elif req.kind == "max_dimension":
            value = vector(shape.bounding_box().size)["XYZ".index(req.axis)]
            passed = value <= req.value
        else:
            if req.reference not in refs:
                findings.append(Finding(code="missing_reference", severity="error", feature=req.id, message="Clearance requirement references a missing component"))
                continue
            value = refs[req.reference]["clearance_mm"]
            passed = value >= req.value and refs[req.reference]["interference_mm3"] <= 1e-5
        findings.append(Finding(code=req.kind, severity="info" if passed else "error", feature=req.id, message=f"{req.id}: {'passed' if passed else 'failed'}", value=value, limit=req.value))
    result = {"revision": project.revision, "key": key, "input_key": verification_key(project), "state": "failed" if any(f.severity == "error" for f in findings) else "needs_input" if any(f.severity == "warning" for f in findings) else "passed",
              "findings": [f.model_dump() for f in findings], "references": refs, "mesh_volume_relative_error": mesh_error,
              "mass_g": mass_g}
    if mass_evidence is not None:
        result["mass_evidence"] = mass_evidence
    write_json(path / "checks.json", result)
    return result
