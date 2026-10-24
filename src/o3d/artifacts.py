"""Agent screenshots and self-contained, revision-linked release packages."""
from copy import deepcopy
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from .model import geometry_key, study_key
from .store import write_json


def _summary(geometry):
    """Describe the complete scene without assigning a volume to surfaces/meshes."""
    scene = geometry.get("scene")
    bodies = scene["bodies"] if scene is not None else ([{
        "id": "part", "name": "Part", "geometry_kind": "solid", "bbox": {
            "min": geometry["bounds"][0], "max": geometry["bounds"][1]},
    }] if geometry.get("faces") else [])
    counts = {kind: sum(b["geometry_kind"] == kind for b in bodies) for kind in ("solid", "surface", "mesh")}
    bounds = [[min((b["bbox"]["min"][i] for b in bodies), default=0) for i in range(3)],
              [max((b["bbox"]["max"][i] for b in bodies), default=0) for i in range(3)]]
    all_solid = bool(bodies) and counts["solid"] == len(bodies)
    return {"body_count": len(bodies), "geometry_kinds": counts, "all_solid": all_solid,
            "bounds": bounds, "dimensions_mm": [b-a for a, b in zip(*bounds)],
            "volume_mm3": geometry.get("volume_mm3") if all_solid else None,
            "solid_volume_mm3": geometry.get("exact_solid_volume_mm3", geometry.get("volume_mm3")) if counts["solid"] else None,
            "bodies": [{k: b[k] for k in ("id", "name", "geometry_kind", "bbox")} for b in bodies]}


def _triangles(geometry):
    scene = geometry.get("scene")
    if scene is not None:
        from .domains.visualization_render import triangles
        for body, _face, triangle in triangles(scene):
            if body.get("visible", True):
                yield triangle
    else:
        for face in geometry["faces"]:
            points = np.asarray(face["vertices"], dtype=float)
            for indices in face["triangles"]:
                yield points[indices]


def render(store, project):
    key = geometry_key(project)
    build_path = store.cache / "builds" / key / "build.json"
    if not build_path.exists():
        from .geometry import build
        build(store, project)
    geometry = json.loads(build_path.read_text())
    summary = _summary(geometry)
    image = Image.new("RGB", (1280, 900), "#eef1f4")
    draw = ImageDraw.Draw(image)
    triangles = list(_triangles(geometry))
    if triangles:
        angle, elevation = np.radians(38), np.radians(28)
        right = np.array([np.cos(angle), -np.sin(angle), 0])
        up = np.array([-np.sin(elevation) * np.sin(angle), -np.sin(elevation) * np.cos(angle), np.cos(elevation)])
        forward = np.cross(right, up)
        vertices = np.concatenate(triangles)
        origin = (vertices.max(0) + vertices.min(0)) / 2
        projected = np.stack(((vertices - origin) @ right, (vertices - origin) @ up), axis=1)
        scale = min(1040 / max(np.ptp(projected[:, 0]), 1), 650 / max(np.ptp(projected[:, 1]), 1))
        polygons = []
        light = np.array([0.3, -0.4, 0.85])
        for triangle in triangles:
            normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
            normal /= max(np.linalg.norm(normal), 1e-12)
            shade = 0.55 + 0.4 * abs(float(normal @ light))
            color = tuple(int(v * shade) for v in (97, 150, 183))
            coords = [(640 + float((v - origin) @ right) * scale, 475 - float((v - origin) @ up) * scale) for v in triangle]
            polygons.append((float((triangle - origin).mean(0) @ forward), coords, color))
        for _, points, color in sorted(polygons, key=lambda p: p[0], reverse=True):
            draw.polygon(points, fill=color)
    else:
        draw.text((480, 450), "No visible triangulated bodies", fill="#566878")
    draw.text((40, 30), f"OXYGEN CAD / {project.name}", fill="#182b3d")
    volume = (f"Solid volume {summary['volume_mm3']:.2f} mm3" if summary["all_solid"]
              else "Aggregate volume not defined for this geometry")
    draw.text((40, 55), f"Revision {project.revision} | {volume} | Dimensions " +
              " x ".join(f"{v:.2f}" for v in summary["dimensions_mm"]) + " mm", fill="#566878")
    path = store.cache / "builds" / key / "preview.png"
    image.save(path)
    return {"artifact": f"builds/{key}/preview.png", "revision": project.revision}


def _checks(store, project, geometry, summary):
    if summary["all_solid"]:
        from .geometry import check
        return check(store, project) | {"scope": "All exact solid bodies and their aggregate exported mesh"}
    return {"revision": project.revision, "key": geometry_key(project), "state": "inconclusive", "mass_g": None,
            "scope": "Independent build diagnostics only; whole-project solid engineering checks do not apply",
            "findings": [{"code": "geometry_scope", "severity": "warning",
                          "message": "Open surfaces, meshes or an empty scene have no aggregate exact-solid volume or mass; solid fit and requirement checks were not run"}],
            "unevaluated_requirements": [r.model_dump(mode="json") for r in project.requirements if r.enabled],
            "independent_bodies": geometry.get("engine", {}).get("independent_bodies", []),
            "independent_meshes": geometry.get("engine", {}).get("independent_meshes", [])}


def _copy_artifact(store, relative, folder, name, names):
    # Names are host allocated, including nested analysis artifact names.
    from .projects import contained
    target = contained(folder, name, exists=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(store.artifact(relative), target)
    names.add(name)
    return name


def _geometry_exports(store, geometry, summary, folder, names):
    artifacts = {}
    for kind, filename in (("step", "part.step"), ("stl", "part.stl"), ("brep", "part.brep"),
                           ("native_design", "part.solidcraft"), ("engine_script", "engine-script.json")):
        if kind in geometry["artifacts"]:
            artifacts[kind] = _copy_artifact(store, geometry["artifacts"][kind], folder, filename, names)
    scene = deepcopy(geometry.get("scene"))
    if scene is not None:
        for index, (body, description) in enumerate(zip(scene["bodies"], summary["bodies"])):
            local = {}
            for kind, relative in body.get("artifacts", {}).items():
                # Body-specific exact artifacts are STEP today; refuse unknown path formats.
                if kind != "step":
                    raise ValueError(f"Unsupported body artifact format: {kind}")
                local[kind] = _copy_artifact(store, relative, folder, f"geometry/body-{index:03d}/part.step", names)
                import build123d as bd
                exact = bd.import_step(folder / local[kind])
                description["area_mm2"] = exact.area
                description["volume_mm3"] = sum(s.volume for s in exact.solids()) if body["geometry_kind"] == "solid" else None
            mesh = body.get("mesh")
            if mesh:
                mesh_name = f"geometry/body-{index:03d}/mesh.json"
                write_json(folder / mesh_name, mesh)
                names.add(mesh_name)
                local["mesh"] = mesh_name
                if body["geometry_kind"] == "mesh":
                    import trimesh
                    triangles = np.asarray(mesh["triangles"], dtype=int)
                    vertices = np.asarray(mesh["positions"], dtype=float)
                    exported = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
                    stl_name = f"geometry/body-{index:03d}/mesh.stl"
                    (folder / stl_name).write_bytes(exported.export(file_type="stl"))
                    names.add(stl_name)
                    local["stl"] = stl_name
            body["artifacts"] = description["artifacts"] = local
        write_json(folder / "scene.json", scene)
        names.add("scene.json")
        artifacts["scene"] = "scene.json"
        solid_bodies = [b for b in summary["bodies"] if b["geometry_kind"] == "solid"]
        if solid_bodies and all(b.get("volume_mm3") is not None for b in solid_bodies):
            summary["solid_volume_mm3"] = sum(b["volume_mm3"] for b in solid_bodies)
    else:
        for body in summary["bodies"]:
            body["artifacts"] = {k: v for k, v in artifacts.items() if k in {"step", "stl", "brep"}}
    engine = deepcopy(geometry.get("engine", {}))
    body_artifacts = {b["id"]: b.get("artifacts", {}) for b in summary["bodies"]}
    for body in engine.get("independent_bodies", []):
        if "step" in body_artifacts.get(body["id"], {}):
            body["step"] = body_artifacts[body["id"]]["step"]
    write_json(folder / "build.json", geometry | {"scene": scene, "artifacts": artifacts, "engine": engine})
    names.add("build.json")
    return artifacts, engine


def export_bundle(store, project):
    from .projects import portable_archive
    from .recovery import sync_directory
    build_path = store.cache / "builds" / geometry_key(project) / "build.json"
    if not build_path.exists():
        raise ValueError("Current geometry has not built successfully; export cannot use an older revision")
    geometry = json.loads(build_path.read_text())
    studies = store.study_states(project)
    active_study = studies[project.active_study_id]
    analysis_state = active_study["state"]
    summary = _summary(geometry)
    checks = _checks(store, project, geometry, summary)
    folder = store.cache / "exports" / str(project.revision)
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=folder.parent) as temporary:
        staged = Path(temporary)
        names = {"o3d.json", "report.json", "report.html", "bom.csv", "project.oxygen.zip"}
        artifacts, engine = _geometry_exports(store, geometry, summary, staged, names)
        write_json(staged / "o3d.json", project.model_dump(mode="json"))
        analysis_artifacts = {}
        study_packages, portable_studies = {}, {}
        for study_id, record in studies.items():
            analysis = record["result"]
            if analysis is None:
                continue
            package = "analysis" if study_id == project.active_study_id else f"analyses/{study_id}"
            study_packages[study_id] = package
            local_names = {relative: {"study_package": "study-package.json", "region_map": "region-map.json"}.get(name, name)
                           for name, relative in analysis.get("artifacts", {}).items()}
            local_names[analysis["field_artifact"]] = "field.json"

            def portable_result(value, prefix=""):
                if isinstance(value, dict):
                    return {k: portable_result(v, prefix) for k, v in value.items()}
                if isinstance(value, list):
                    return [portable_result(v, prefix) for v in value]
                if isinstance(value, str) and value in local_names:
                    return prefix + local_names[value]
                return value

            write_json(staged / package / "result.json", portable_result(analysis))
            names.add(f"{package}/result.json")
            portable_studies[study_id] = portable_result(analysis, package + "/")
            package_artifacts = {"field": _copy_artifact(store, analysis["field_artifact"], staged, f"{package}/field.json", names)}
            for name, relative in analysis.get("artifacts", {}).items():
                target_name = {"study_package": "study-package.json", "region_map": "region-map.json"}.get(name, name)
                package_artifacts[name] = _copy_artifact(store, relative, staged, f"{package}/{target_name}", names)
            if study_id == project.active_study_id:
                analysis_artifacts = package_artifacts
        portable = portable_archive(store, staged / "project.oxygen.zip", expected_project=project)
        # Extract immutable source assets from the guarded snapshot, not changing live files.
        source_assets = []
        with zipfile.ZipFile(staged / "project.oxygen.zip") as archive:
            for name in sorted(portable["manifest"]["files"]):
                if name.startswith("assets/"):
                    target = staged / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
                    names.add(name)
                    source_assets.append(name)
        scope = {"geometry_kinds": summary["geometry_kinds"],
                 "aggregate_exports": {kind: {"artifact": artifacts[kind],
                     "body_ids": geometry.get("artifact_scope", {}).get(kind, {}).get("body_ids",
                         [b["id"] for b in summary["bodies"] if b["geometry_kind"] != "mesh"])}
                     for kind in ("step", "brep", "stl") if kind in artifacts},
                 "build_artifact_scope": geometry.get("artifact_scope", {}),
                 "mesh_exports": "Per-body native triangle data; mesh bodies also have per-body STL",
                 "volume_basis": "All closed exact solids" if summary["all_solid"] else "No aggregate volume/mass; solid_volume_mm3 is an exact-solid subtotal only",
                 "solid_engineering_checks": "performed" if summary["all_solid"] else "not_applicable_to_complete_scene",
                 "source_assets": source_assets}
        report = {"project": project.name, "revision": project.revision, "geometry_key": geometry_key(project), "study_key": study_key(project),
                  "geometry_engine": engine, "geometry_checks": checks, "analysis_state": analysis_state, "analysis": portable_studies.get(project.active_study_id),
                  "active_study_id": project.active_study_id, "study_packages": study_packages,
                  "studies": {study_id: record | {"result": portable_studies.get(study_id)} for study_id, record in studies.items()},
                  "dimensions_mm": summary["dimensions_mm"], "volume_mm3": summary["volume_mm3"],
                  "mass_g": checks.get("mass_g"), "geometry": summary, "geometry_artifacts": artifacts,
                  "analysis_artifacts": analysis_artifacts, "scope": scope}
        write_json(staged / "report.json", report)
        (staged / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>Oxygen CAD engineering report</title>"
            "<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;color:#203044}pre{white-space:pre-wrap;background:#f1f4f6;padding:24px}</style>"
            f"<h1>{html.escape(project.name)}</h1><p>Revision {project.revision}. "
            "Results apply to the recorded model and assumptions; geometry scope, unresolved and failed checks remain visible below.</p>"
            f"<pre>{html.escape(json.dumps(report, indent=2))}</pre>")
        with (staged / "bom.csv").open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["item", "type", "quantity", "material", "source", "geometry_kind"])
            for body in summary["bodies"]:
                available = body.get("artifacts", {})
                source = next((available[k] for k in ("step", "stl", "mesh") if k in available), artifacts.get("native_design", "o3d.json"))
                writer.writerow([body["name"], "make", 1, project.material.name if project.material else "Unspecified", source, body["geometry_kind"]])
            if not summary["bodies"]:
                writer.writerow([project.name, "design", 1, "Unspecified", artifacts.get("native_design", "o3d.json"), "none"])
            for ref in project.references:
                writer.writerow([ref.name, "reference", 1, "", ref.file, "solid"])
        manifest = {"format": "oxygen-cad-release", "schema_version": 1, "revision": project.revision,
                    "project_archive": "project.oxygen.zip", "scope": portable["manifest"]["scope"] | {"geometry": scope},
                    "files": {name: {"sha256": hashlib.sha256((staged / name).read_bytes()).hexdigest(),
                                     "bytes": (staged / name).stat().st_size} for name in sorted(names)}}
        write_json(staged / "release-manifest.json", manifest)
        with zipfile.ZipFile(staged / "release.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(names | {"release-manifest.json"}):
                entry = zipfile.ZipInfo(name)
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, (staged / name).read_bytes())
        with (staged / "release.zip").open("rb") as source:
            os.fsync(source.fileno())
        with store.lock():
            if store._load() != project:
                raise ValueError("Project changed during export; retry export from the current revision")
            folder.mkdir(parents=True, exist_ok=True)
            for name in sorted(names | {"release-manifest.json"}):
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged / name, target)
            # Readers see the complete previous ZIP until this single atomic publication.
            os.replace(staged / "release.zip", folder / "release.zip")
            sync_directory(folder)
    return {"revision": project.revision, "artifact": f"exports/{project.revision}/release.zip", "report": f"exports/{project.revision}/report.html"}
