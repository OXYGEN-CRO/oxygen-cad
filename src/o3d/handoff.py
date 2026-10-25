"""Revision-bound derivative packages over the existing native CAD release."""
from __future__ import annotations

import hashlib
import html
import importlib.metadata
import importlib.util
import json
import io
import math
import stat
from pathlib import Path
import shutil
import tempfile
import zipfile

from .artifacts import export_bundle, render
from .model import geometry_key
from .store import Conflict, Store, write_json
from .identity import authorized
from .projects import contained, declared_artifacts, verify_assets

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document_signature(project) -> str:
    document = project.model_dump(mode="json")
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def project_signature(store: Store) -> str:
    return document_signature(store.load())


def input_assets(store: Store) -> dict[str, str]:
    """Canonical immutable inputs, linked/native assets and declared cached evidence."""
    project = store.load()
    document = project.model_dump(mode="json")
    result = dict(verify_assets(store.root, [document]))
    for relative in declared_artifacts(document):
        name = ".o3d/" + relative
        result[name] = digest(contained(store.root, name))
    for relative in list(result):
        path = contained(store.root, relative)
        for suffix in (".LICENSE.txt", ".catalog.json"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                name = sidecar.relative_to(store.root).as_posix()
                result[name] = digest(contained(store.root, name))
    for path in sorted((store.cache / "catalog-links").glob("*.json")):
        name = path.relative_to(store.root).as_posix()
        result[name] = digest(contained(store.root, name))
    return result


def toolchain() -> dict:
    from .native import engine_binary
    files = sorted((ROOT / "src/o3d").glob("*.py"))
    files += [ROOT / "skills" / folder / "scripts" / name for folder, name in [
        ("oxygen-cad-drawing", "drawing.py"), ("oxygen-cad-dxf", "profiles.py"), ("oxygen-cad-dfam", "mesh_review.py")]]
    try:
        packages = {name: importlib.metadata.version(name) for name in ("build123d", "ezdxf", "trimesh", "numpy", "reportlab")}
    except importlib.metadata.PackageNotFoundError as error:
        raise RuntimeError("Install the CAD environment with the [skills] extra before generating a handoff") from error
    return {"files": {str(p.relative_to(ROOT)): digest(p) for p in files}, "packages": packages,
            "native_engine_sha256": digest(Path(engine_binary()))}


def helper(folder: str, name: str):
    path = ROOT / "skills" / folder / "scripts" / name
    spec = importlib.util.spec_from_file_location("o3d_handoff_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release(store, release):
    """Freeze only canonical manifest-listed bytes, never stale export-directory files."""
    archive_path = contained(store.cache, release["artifact"])
    if archive_path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("Canonical release exceeds the 512 MiB handoff limit")
    content = archive_path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or len(names) > 10000 or sum(entry.file_size for entry in entries) > 512 * 1024 * 1024:
            raise ValueError("Invalid or oversized canonical release archive")
        for entry in entries:
            contained(store.cache, entry.filename, exists=False)
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Canonical release entries must be regular files")
        manifest = json.loads(archive.read("release-manifest.json"))
        if manifest.get("format") != "oxygen-cad-release" or manifest.get("schema_version") != 1:
            raise ValueError("Expected a current canonical CAD release")
        if set(names) != set(manifest["files"]) | {"release-manifest.json"}:
            raise ValueError("Canonical release has unlisted or missing files")
        files = {}
        for name, metadata in manifest["files"].items():
            raw = archive.read(name)
            if metadata != {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}:
                raise ValueError("Canonical release file hash changed: " + name)
            files[name] = raw
        files["release-manifest.json"] = archive.read("release-manifest.json")
    return content, files


def single_solid_scope(report):
    """Derivative and supplier checks deliberately support exactly one exact solid."""
    geometry = report.get("geometry", {})
    bodies = geometry.get("bodies", [])
    if geometry.get("body_count") != 1 or not geometry.get("all_solid") or len(bodies) != 1 or bodies[0].get("geometry_kind") != "solid":
        raise ValueError("Handoff derivatives and supplier preflight currently require exactly one closed exact solid. "
                         "This scene has multiple bodies, surfaces, meshes or no solid. Export the complete canonical CAD release "
                         "for assembly review, or create a separate single-body project; bodies are never flattened into one part.")
    body_ids = [bodies[0]["id"]]
    exports = report.get("geometry_artifacts", {})
    aggregate = report.get("scope", {}).get("aggregate_exports", {})
    if not all(kind in exports and aggregate.get(kind, {}).get("body_ids") == body_ids for kind in ("step", "stl")):
        raise ValueError("Single-solid handoff requires complete report-scoped STEP and STL exports; rebuild and export current geometry")
    selectable = {"native/" + exports[kind]: {"format": kind, "body_ids": body_ids, "scope": "complete_single_solid"}
                  for kind in ("step", "stl")}
    for kind, path in bodies[0].get("artifacts", {}).items():
        if kind in {"step", "stl"}:
            selectable["native/" + path] = {"format": kind, "body_ids": body_ids, "scope": "complete_single_solid"}
    return {"mode": "single_exact_solid", "body_ids": body_ids, "bodies": bodies,
            "step": "native/" + exports["step"], "stl": "native/" + exports["stl"],
            "selectable_artifacts": selectable, "canonical_scope": report["scope"]}


def create(store: Store, section_z: float, overhang_degrees: float = 45, *, expected_revision: int | None = None) -> dict:
    """Freeze the complete canonical release; derivatives support one exact solid."""
    if not math.isfinite(section_z) or not 0 < overhang_degrees < 90:
        raise ValueError("section-z must be finite and overhang-degrees must be between 0 and 90; select applicable process rules")
    with store.lock(), authorized(store, "export"):
        project = store._load()
        if expected_revision is not None and project.revision != expected_revision:
            raise Conflict(f"Revision conflict: expected {expected_revision}, current {project.revision}. Re-inspect before creating a handoff.")
    signature, assets, tools = document_signature(project), input_assets(store), toolchain()
    if store.state()["geometry_state"] != "current":
        raise ValueError("Build current geometry before creating a handoff")
    release = export_bundle(store, project)
    release_bytes, frozen = _release(store, release)
    report = json.loads(frozen["report.json"])
    scope = single_solid_scope(report)
    if report["revision"] != project.revision or report["geometry_key"] != geometry_key(project):
        raise Conflict("Canonical export differs from selected handoff snapshot")
    preview = render(store, project)
    parent = contained(store.cache, "handoffs", exists=False)
    with store.lock(), authorized(store, "export"):
        if document_signature(store._load()) != signature:
            raise Conflict("Project changed while generating the handoff; regenerate from a current revision")
        parent.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=f"r{project.revision}-", dir=parent))
    try:
        for name, raw in frozen.items():
            destination = contained(folder, "native/" + name, exists=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        (folder / "native-release.zip").write_bytes(release_bytes)
        shutil.copy2(store.artifact(preview["artifact"]), folder / "preview.png")
        derivatives = folder / "derivatives"
        derivatives.mkdir()
        drawing = helper("oxygen-cad-drawing", "drawing.py").drawing(
            folder / scope["step"], derivatives / "drawing.pdf", project.name, str(project.revision))
        section = helper("oxygen-cad-dxf", "profiles.py").section(folder / scope["step"], section_z, derivatives / "section.dxf")
        mesh = helper("oxygen-cad-dfam", "mesh_review.py").measure(folder / scope["stl"], overhang=overhang_degrees)
        drawing.update(output="derivatives/drawing.pdf", source=scope["step"])
        section["file"] = "derivatives/section.dxf"
        mesh["file"] = scope["stl"]
        write_json(folder / "derivative-report.json", {"drawing": drawing, "section": section, "mesh_screen": mesh, "geometry_scope": scope})
        for relative in assets:
            destination = contained(folder, relative, exists=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(contained(store.root, relative), destination)
            if digest(destination) != assets[relative]:
                raise Conflict("Source dependency changed while copying handoff: " + relative)
        (folder / "o3d.json").write_bytes(frozen["o3d.json"])
        for name in ("AGENTS.md", "project.schema.json"):
            if (store.root / name).is_file():
                shutil.copy2(contained(store.root, name), folder / name)
        findings = {"geometry_checks": report["geometry_checks"], "analysis_state": report["analysis_state"],
                    "geometry_scope": scope,
                    "unverified": ["physical fit and strength", "complete minimum-wall/tool-access analysis", "supplier acceptance", "slicing/printing"]}
        write_json(folder / "findings.json", findings)
        (folder / "index.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Oxygen handoff</title>"
            "<style>body{font:16px system-ui;max-width:1000px;margin:40px auto}img{max-width:100%}pre{white-space:pre-wrap}</style>"
            f"<h1>{html.escape(project.name)}</h1><p>Frozen project revision {project.revision}. "
            "Check current status before using this package for a changed design. "
            "Reopen the complete project from native/project.oxygen.zip to retain native documents, history, studies and dependencies.</p>"
            "<img src='preview.png' alt='Native solid preview'><p><a href='derivatives/drawing.pdf'>Nominal drawing</a> · "
            "<a href='derivatives/section.dxf'>Planar section DXF</a> · <a href='native/report.html'>Engineering report</a></p>"
            f"<pre>{html.escape(json.dumps(findings, indent=2))}</pre>")
        if project_signature(store) != signature or input_assets(store) != assets:
            raise Conflict("Project changed while generating the handoff; regenerate from a current revision")
        files = {str(p.relative_to(folder)): digest(p) for p in sorted(folder.rglob("*")) if p.is_file()}
        manifest = {"schema_version": 1, "revision": project.revision, "project_sha256": signature,
                    "geometry_key": geometry_key(project), "reference_inputs": assets, "toolchain": tools,
                    "geometry_scope": scope, "canonical_project_archive": "native/project.oxygen.zip",
                    "parameters": {"section_z_mm": section_z, "overhang_degrees": overhang_degrees},
                    "files": files, "findings": findings}
        write_json(folder / "manifest.json", manifest)
        with zipfile.ZipFile(folder / "handoff.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.name != "handoff.zip":
                    archive.write(path, path.relative_to(folder))
        write_json(folder / "receipt.json", {"archive_sha256": digest(folder / "handoff.zip"),
                                             "manifest_sha256": digest(folder / "manifest.json")})
        manifest_relative = str((folder / "manifest.json").relative_to(store.cache))
        result = status(store, manifest_relative)
        with store.lock(), authorized(store, "export"):
            if document_signature(store._load()) != signature or result["state"] != "current":
                raise Conflict("Project changed while generating the handoff; regenerate from a current revision")
            write_json(parent / "latest.json", {"manifest": manifest_relative})
        result.update(artifact=str((folder / "handoff.zip").relative_to(store.cache)), report=str((folder / "index.html").relative_to(store.cache)), geometry_scope=scope)
        return result
    except PermissionError:
        shutil.rmtree(folder)
        raise
    except Exception as error:
        write_json(folder / "failure.json", {"error": str(error), "revision": project.revision})
        raise


def status(store: Store, manifest: str | None = None) -> dict:
    if manifest is None:
        pointer = store.cache / "handoffs/latest.json"
        if not pointer.is_file():
            return {"state": "not_run", "reasons": ["No handoff has been generated"]}
        manifest = json.loads(pointer.read_text())["manifest"]
    candidate = Path(manifest)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        cached = (store.cache / candidate).resolve()
        path = cached if cached.is_file() else (store.root / candidate).resolve()
    if not path.is_relative_to(store.root) or not path.is_file():
        raise ValueError("Handoff manifest must exist inside the selected project")
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported handoff manifest schema")
    reasons = []
    if data["project_sha256"] != project_signature(store): reasons.append("project_changed")
    try:
        if data["reference_inputs"] != input_assets(store): reasons.append("reference_inputs_changed")
    except ValueError:
        reasons.append("reference_inputs_changed")
    if data["toolchain"] != toolchain(): reasons.append("toolchain_changed")
    for relative, expected in data["files"].items():
        artifact = (path.parent / relative).resolve()
        if not artifact.is_relative_to(path.parent.resolve()) or not artifact.is_file() or digest(artifact) != expected:
            reasons.append("artifact_changed:" + relative)
    receipt = path.parent / "receipt.json"
    archive = path.parent / "handoff.zip"
    archived = path.is_relative_to(store.cache / "handoffs")
    archive_state = "not_applicable_to_unpacked_files"
    if archived and (not receipt.is_file() or not archive.is_file()):
        reasons.append("archive_missing")
        archive_state = "missing"
    elif archived:
        recorded = json.loads(receipt.read_text())
        if recorded.get("manifest_sha256") != digest(path): reasons.append("manifest_changed")
        if recorded.get("archive_sha256") != digest(archive): reasons.append("archive_changed")
        archive_state = "verified" if not any(r in reasons for r in ("manifest_changed", "archive_changed")) else "changed"
    return {"state": "stale" if reasons else "current", "revision": data["revision"],
            "current_revision": store.load().revision, "manifest": manifest, "reasons": reasons,
            "archive_state": archive_state, "findings": data["findings"]}
