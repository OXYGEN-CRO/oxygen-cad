"""Agent screenshots and self-contained, revision-linked release packages."""
import csv
import html
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .model import geometry_key, study_key
from .store import write_json


def render(store, project):
    key = geometry_key(project)
    build_path = store.cache / "builds" / key / "build.json"
    if not build_path.exists():
        from .geometry import build
        build(store, project)
    geometry = json.loads(build_path.read_text())
    image = Image.new("RGB", (1280, 900), "#eef1f4")
    draw = ImageDraw.Draw(image)
    angle, elevation = np.radians(38), np.radians(28)
    right = np.array([np.cos(angle), -np.sin(angle), 0])
    up = np.array([-np.sin(elevation) * np.sin(angle), -np.sin(elevation) * np.cos(angle), np.cos(elevation)])
    forward = np.cross(right, up)
    vertices = np.array([v for face in geometry["faces"] for v in face["vertices"]])
    origin = (vertices.max(0) + vertices.min(0)) / 2
    projected = np.stack(((vertices - origin) @ right, (vertices - origin) @ up), axis=1)
    scale = min(1040 / max(np.ptp(projected[:, 0]), 1), 650 / max(np.ptp(projected[:, 1]), 1))
    polygons = []
    light = np.array([0.3, -0.4, 0.85])
    for face in geometry["faces"]:
        points = np.array(face["vertices"])
        for indices in face["triangles"]:
            triangle = points[indices]
            normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
            normal /= max(np.linalg.norm(normal), 1e-12)
            shade = 0.55 + 0.4 * abs(float(normal @ light))
            color = tuple(int(v * shade) for v in (97, 150, 183))
            coords = [(640 + float((v - origin) @ right) * scale, 475 - float((v - origin) @ up) * scale) for v in triangle]
            polygons.append((float((triangle - origin).mean(0) @ forward), coords, color))
    for _, points, color in sorted(polygons, key=lambda p: p[0]):
        draw.polygon(points, fill=color)
    draw.text((40, 30), f"OXYGEN CAD / {project.name}", fill="#182b3d")
    draw.text((40, 55), f"Revision {project.revision} | {geometry['volume_mm3']:.2f} mm3 | Dimensions " + " x ".join(f"{v:.2f}" for v in geometry["dimensions_mm"]) + " mm", fill="#566878")
    path = store.cache / "builds" / key / "preview.png"
    image.save(path)
    return {"artifact": f"builds/{key}/preview.png", "revision": project.revision}


def export_bundle(store, project):
    from .geometry import check
    build_path = store.cache / "builds" / geometry_key(project) / "build.json"
    if not build_path.exists():
        raise ValueError("Current geometry has not built successfully; export cannot use an older revision")
    studies = store.study_states(project)
    selected = studies[project.active_study_id]
    state = {"build": json.loads(build_path.read_text()), "analysis": selected["result"], "analysis_state": selected["state"]}
    checks = check(store, project)
    parent = store.cache / "exports" / str(project.revision)
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="release-", dir=parent))
    for name in ("step", "stl"):
        shutil.copy2(store.artifact(state["build"]["artifacts"][name]), folder / f"part.{name}")
    for kind, filename in (("native_design", "part.solidcraft"), ("engine_script", "engine-script.json")):
        if kind in state["build"]["artifacts"]:
            shutil.copy2(store.artifact(state["build"]["artifacts"][kind]), folder / filename)
    write_json(folder / "o3d.json", project.model_dump(mode="json"))
    analysis = state["analysis"]
    study_packages = {}
    portable_studies = {}
    for study_id, record in studies.items():
        current_result = record["result"]
        if current_result is None:
            continue
        package_folder = "analysis" if study_id == project.active_study_id else f"analyses/{study_id}"
        study_packages[study_id] = package_folder
        study_folder = folder / package_folder
        study_folder.mkdir(parents=True, exist_ok=True)
        names = {relative:{"study_package":"study-package.json", "region_map":"region-map.json"}.get(name,name)
                 for name,relative in current_result.get("artifacts",{}).items()}
        names[current_result["field_artifact"]] = "field.json"
        def portable(value, prefix=""):
            if isinstance(value,dict):return {k:portable(v,prefix) for k,v in value.items()}
            if isinstance(value,list):return [portable(v,prefix) for v in value]
            if isinstance(value,str) and value in names:return prefix+names[value]
            return value
        write_json(study_folder / "result.json", portable(current_result))
        portable_studies[study_id] = portable(current_result,package_folder+"/")
        shutil.copy2(store.artifact(current_result["field_artifact"]), study_folder / "field.json")
        for name, relative in current_result.get("artifacts", {}).items():
            target_name = {"study_package":"study-package.json", "region_map":"region-map.json"}.get(name, name)
            target = (study_folder / target_name).resolve()
            if not target.is_relative_to(study_folder.resolve()):
                raise ValueError("Analysis artifact name escapes the release package")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(store.artifact(relative), target)
    report_studies = {i:{**record,"result":portable_studies.get(i)} for i,record in studies.items()}
    report = {"project": project.name, "revision": project.revision, "geometry_key": geometry_key(project), "study_key": study_key(project),
              "geometry_engine": state["build"].get("engine"), "geometry_checks": checks, "analysis_state": state["analysis_state"], "analysis": portable_studies.get(project.active_study_id),
              "active_study_id": project.active_study_id, "studies":report_studies, "study_packages":study_packages,
              "dimensions_mm": state["build"]["dimensions_mm"], "volume_mm3": state["build"]["volume_mm3"]}
    write_json(folder / "report.json", report)
    (folder / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>Oxygen CAD engineering report</title>"
        "<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;color:#203044}pre{white-space:pre-wrap;background:#f1f4f6;padding:24px}</style>"
        f"<h1>{html.escape(project.name)}</h1><p>Revision {project.revision}. "
        "Results apply to the recorded model and assumptions; unresolved or failed checks remain visible below.</p>"
        f"<pre>{html.escape(json.dumps(report, indent=2))}</pre>")
    with (folder / "bom.csv").open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["item", "type", "quantity", "material", "source"])
        writer.writerow([project.name, "make", 1, project.material.name if project.material else "Unspecified", "part.step"])
        for ref in project.references:
            writer.writerow([ref.name, "reference", 1, "", ref.sha256])
    with zipfile.ZipFile(folder / "release.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path != folder / "release.zip":
                archive.write(path, path.relative_to(folder))
    return {"revision": project.revision, "artifact": str((folder / "release.zip").relative_to(store.cache)),
            "report": str((folder / "report.html").relative_to(store.cache))}
