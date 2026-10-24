"""Revision-bound derivative packages over the existing native CAD release."""
from __future__ import annotations

import hashlib
import html
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from .artifacts import export_bundle, render
from .model import geometry_key
from .store import Store, write_json

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document_signature(project) -> str:
    document = project.model_dump(mode="json")
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def project_signature(store: Store) -> str:
    return document_signature(store.load())


def input_assets(store: Store) -> dict[str, str]:
    result = {}
    for asset in store.load().references:
        path = (store.root / asset.file).resolve()
        if not path.is_relative_to(store.root) or not path.is_file():
            raise ValueError(f"Missing or invalid reference asset: {asset.id}")
        actual = digest(path)
        if actual != asset.sha256:
            raise ValueError(f"Reference asset content changed: {asset.id}")
        result[asset.file] = actual
        for suffix in (".LICENSE.txt", ".catalog.json"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.is_file():
                if sidecar.is_symlink(): raise ValueError("Reference provenance cannot be a symlink")
                result[str(sidecar.relative_to(store.root))] = digest(sidecar)
    for path in sorted((store.cache / "catalog-links").glob("*.json")):
        if path.is_symlink():
            raise ValueError("Catalog provenance cannot be a symlink")
        result[str(path.relative_to(store.root))] = digest(path)
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


def create(store: Store, section_z: float, overhang_degrees: float = 45) -> dict:
    """Build a frozen handoff from already built geometry; no source edits or solve."""
    if not 0 < overhang_degrees < 90:
        raise ValueError("overhang-degrees must be between 0 and 90; select it from applicable process rules")
    project = store.load()
    signature, assets, tools = document_signature(project), input_assets(store), toolchain()
    if store.state()["geometry_state"] != "current":
        raise ValueError("Build current geometry before creating a handoff")
    release = export_bundle(store, project)
    preview = render(store, project)
    parent = store.cache / "handoffs"
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"r{project.revision}-", dir=parent))
    try:
        # The native exporter creates a fresh trusted folder; copy its portable contents.
        source = store.artifact(release["artifact"]).parent
        for item in source.iterdir():
            if item.name != "release.zip":
                destination = folder / "native" / item.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)
        shutil.copy2(store.artifact(release["artifact"]), folder / "native-release.zip")
        shutil.copy2(store.artifact(preview["artifact"]), folder / "preview.png")
        derivatives = folder / "derivatives"
        derivatives.mkdir()
        drawing = helper("oxygen-cad-drawing", "drawing.py").drawing(
            source / "part.step", derivatives / "drawing.pdf", project.name, str(project.revision))
        section = helper("oxygen-cad-dxf", "profiles.py").section(source / "part.step", section_z, derivatives / "section.dxf")
        mesh = helper("oxygen-cad-dfam", "mesh_review.py").measure(source / "part.stl", overhang=overhang_degrees)
        # Do not bake server-specific temporary paths into the portable derivative evidence.
        drawing.update(output="derivatives/drawing.pdf", source="native/part.step")
        section["file"] = "derivatives/section.dxf"
        mesh["file"] = "native/part.stl"
        write_json(folder / "derivative-report.json", {"drawing": drawing, "section": section, "mesh_screen": mesh})
        # Include immutable reference assets and attribution, so the project can be reopened.
        for relative in assets:
            destination = folder / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(store.root / relative, destination)
        shutil.copy2(source / "o3d.json", folder / "o3d.json")
        for name in ("AGENTS.md", "project.schema.json"):
            if (store.root / name).is_file(): shutil.copy2(store.root / name, folder / name)
        report = json.loads((source / "report.json").read_text())
        findings = {"geometry_checks": report["geometry_checks"], "analysis_state": report["analysis_state"],
                    "unverified": ["physical fit and strength", "complete minimum-wall/tool-access analysis", "supplier acceptance", "slicing/printing"]}
        write_json(folder / "findings.json", findings)
        (folder / "index.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Oxygen handoff</title>"
            "<style>body{font:16px system-ui;max-width:1000px;margin:40px auto}img{max-width:100%}pre{white-space:pre-wrap}</style>"
            f"<h1>{html.escape(project.name)}</h1><p>Frozen project revision {project.revision}. "
            "Check current status with o3d handoff status before using this package for a changed design. "
            "For unpacked files use o3d handoff status --manifest manifest.json.</p>"
            "<img src='preview.png' alt='Native part preview'><p><a href='derivatives/drawing.pdf'>Nominal drawing</a> · "
            "<a href='derivatives/section.dxf'>Planar section DXF</a> · <a href='native/report.html'>Engineering report</a></p>"
            f"<pre>{html.escape(json.dumps(findings, indent=2))}</pre>")
        if project_signature(store) != signature or input_assets(store) != assets:
            raise ValueError("Project changed while generating the handoff; regenerate from a current revision")
        files = {str(p.relative_to(folder)): digest(p) for p in sorted(folder.rglob("*")) if p.is_file()}
        manifest = {"schema_version": 1, "revision": project.revision, "project_sha256": signature,
                    "geometry_key": geometry_key(project), "reference_inputs": assets, "toolchain": tools,
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
        write_json(parent / "latest.json", {"manifest": manifest_relative})
        result = status(store, manifest_relative)
        result.update(artifact=str((folder / "handoff.zip").relative_to(store.cache)), report=str((folder / "index.html").relative_to(store.cache)))
        return result
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
