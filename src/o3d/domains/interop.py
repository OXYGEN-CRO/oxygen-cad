"""Editable exchange and bounded surface/mesh workflows on the native document.

Only provenance lives in this domain. Imported geometry lives in the upstream
Document, and every mutation returns a proposal for the caller's Store transaction.
CAD and mesh libraries are deliberately imported inside worker operations.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SOURCE_BYTES = 50 * 1024 * 1024
UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
EXTENSIONS = {"step", "stp", "stl", "obj", "dxf", "svg", "solidcraft", "json"}


class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^asset_[a-f0-9]{24}$")
    name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file: str
    format: Literal["step", "stp", "stl", "obj", "dxf", "svg", "solidcraft", "json"]
    source_units: Literal["mm", "cm", "m", "in", "ft", "file"] = "mm"
    scale_to_mm: float = Field(default=1, gt=0)
    source_asset: str | None = None
    operation: str = "upload"
    feature: int | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def immutable_path(self):
        if self.file != f"assets/{self.sha256}.{self.format}":
            raise ValueError("Assets must use a content-addressed project asset path")
        if self.id != f"asset_{self.sha256[:24]}":
            raise ValueError("Asset identity must match its content hash")
        if not math.isfinite(self.scale_to_mm):
            raise ValueError("Unit scale must be finite")
        return self


class InteropData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    assets: list[Asset] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_assets(self):
        if len({asset.id for asset in self.assets}) != len(self.assets):
            raise ValueError("Duplicate asset identities")
        return self


def validate(data: dict) -> dict:
    return InteropData.model_validate(data).model_dump(mode="json")


def _data(project):
    return InteropData.model_validate(getattr(project, "domains", {}).get("interop", {}))


def _check_cancel(cancel):
    if cancel is not None:
        # Job adapters use either an event or a cancellation predicate.
        stopped = cancel.is_set() if hasattr(cancel, "is_set") else cancel() if callable(cancel) else False
        if stopped:
            raise InterruptedError("Exchange operation cancelled")


def _asset_path(store, asset: Asset) -> Path:
    source = store.root / asset.file
    path = source.resolve()
    if source.is_symlink() or (store.root / "assets").is_symlink() or not path.is_relative_to(store.root / "assets") or not path.is_file():
        raise ValueError("Immutable source asset is missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
        raise ValueError("Immutable source asset hash does not match its provenance")
    return path


def _find_asset(project, asset_id):
    asset = next((a for a in _data(project).assets if a.id == asset_id), None)
    if asset is None:
        raise ValueError("Select an imported source asset")
    return asset


def _put_asset(store, content: bytes, name: str, extension: str, **metadata) -> Asset:
    if not content or len(content) > MAX_SOURCE_BYTES:
        raise ValueError("Source must contain 1 byte to 50 MiB")
    if extension not in EXTENSIONS:
        raise ValueError("Supported exchange formats: STEP, STL, OBJ, DXF, SVG and native designs")
    digest = hashlib.sha256(content).hexdigest()
    asset = Asset(id=f"asset_{digest[:24]}", name=Path(name).name, sha256=digest,
                  file=f"assets/{digest}.{extension}", format=extension, **metadata)
    path = store.root / asset.file
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Immutable project assets cannot be symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Immutable sources may survive a cancelled/rejected proposal, but are never
    # reachable from the project until the Store commits that proposal.
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing content-addressed asset is corrupted")
    return asset


def _with_assets(project, *assets):
    updated = project.model_copy(deep=True)
    data = _data(updated)
    for asset in assets:
        existing = next((i for i, a in enumerate(data.assets) if a.id == asset.id), None)
        if existing is None:
            data.assets.append(asset)
        elif asset.feature is not None:
            data.assets[existing] = asset
    updated.domains["interop"] = validate(data.model_dump(mode="json"))
    return updated


def _proposal(project, result):
    return {"changed": True, "project": project.model_dump(mode="json"), "result": result}


def _native(project, store, command, params):
    from ..native_document import prepare_native_command
    return prepare_native_command(project, command, params, store=store)


def _load_mesh(path, extension):
    import trimesh
    mesh = trimesh.load(io.BytesIO(path.read_bytes()), file_type=extension, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError("Source contains no triangle mesh")
    if len(mesh.faces) > 1_000_000:
        raise ValueError("Interactive mesh operations are limited to one million triangles")
    import numpy as np
    if not np.isfinite(mesh.vertices).all():
        raise ValueError("Mesh contains non-finite coordinates")
    # STL stores every triangle with separate vertices. Weld exact positions for
    # topological analysis, retaining duplicate/degenerate faces as diagnostics.
    mesh.merge_vertices()
    return mesh


def mesh_diagnostics(mesh) -> dict:
    """Measured topological diagnostics; no claim to detect self intersections."""
    import numpy as np
    import trimesh
    if len(mesh.faces) == 0:
        return {"vertices": len(mesh.vertices), "triangles": 0, "watertight": False,
                "warnings": ["Mesh has no triangles"]}
    _, incidence = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    unique_faces = len(np.unique(np.sort(mesh.faces, axis=1), axis=0))
    warnings = []
    boundary = int(np.count_nonzero(incidence == 1))
    nonmanifold = int(np.count_nonzero(incidence > 2))
    degenerate = int(np.count_nonzero(~mesh.nondegenerate_faces()))
    if boundary:
        warnings.append(f"{boundary} boundary edges; the mesh is open")
    if nonmanifold:
        warnings.append(f"{nonmanifold} edges have more than two incident triangles")
    if not mesh.is_winding_consistent:
        warnings.append("Triangle winding is inconsistent")
    if degenerate:
        warnings.append(f"{degenerate} degenerate triangles")
    return {"vertices": len(mesh.vertices), "triangles": len(mesh.faces),
            "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "duplicate_triangles": len(mesh.faces) - unique_faces,
            "degenerate_triangles": degenerate, "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "connected_components": len(trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)), min_len=1)),
            "bounds_mm": mesh.bounds.tolist(), "extents_mm": mesh.extents.tolist(),
            "area_mm2": float(mesh.area),
            "volume_mm3": float(mesh.volume) if mesh.is_volume else None,
            "euler_number": int(mesh.euler_number), "warnings": warnings,
            "limits": ["Self-intersection detection is unavailable; watertight topology alone does not establish a valid solid."]}


def _drawing_scale(text, declared):
    if declared != "file":
        return declared, UNIT_SCALE[declared]
    # DXF $INSUNITS group 70: 0/unitless, 1/in, 2/ft, 4/mm, 5/cm, 6/m.
    pairs = list(zip(text.splitlines()[::2], text.splitlines()[1::2]))
    for i, (code, value) in enumerate(pairs):
        if code.strip() == "9" and value.strip() == "$INSUNITS":
            for group, unit in pairs[i + 1:i + 5]:
                if group.strip() == "70":
                    units = {"1": "in", "2": "ft", "4": "mm", "5": "cm", "6": "m"}.get(unit.strip())
                    if units:
                        return units, UNIT_SCALE[units]
                    break
    raise ValueError("DXF has no supported $INSUNITS. Select its source units explicitly")


def _import_asset(store, project, asset, payload, cancel=None):
    _check_cancel(cancel)
    project = _with_assets(project, asset)
    if asset.format == "json":
        snapshot = next((a for a in _data(project).assets if a.format == "solidcraft" and a.source_asset == asset.id), None)
        if snapshot is None:
            raise ValueError("Native JSON requires its validated .solidcraft snapshot; upload the JSON source again")
        return _import_asset(store, project, snapshot, payload, cancel)
    if asset.format == "solidcraft":
        project, result = _native(project, store, "component.insert_design", {"asset_id": asset.id, "name": asset.name, "reuse": bool(payload.get("reuse", False))})
    elif asset.format in {"step", "stp"}:
        project, result = _native(project, store, "file.insert_step", {"asset_id": asset.id, "name": asset.name,
                                          "up": payload.get("up", "z"), "names": payload.get("names", "auto")})
    elif asset.format in {"stl", "obj"}:
        project, result = _native(project, store, "file.insert_mesh", {"asset_id": asset.id, "name": asset.name})
    else:
        text = _asset_path(store, asset).read_text(encoding="utf-8-sig")
        native_scale = asset.scale_to_mm
        if asset.format == "dxf":
            # The upstream DXF reader already applies $INSUNITS. Apply only
            # an explicit override relative to those units, never a second
            # conversion of an inch drawing that is already in millimetres.
            try:
                _, declared_scale = _drawing_scale(text, "file")
            except ValueError:
                declared_scale = 1.0
            native_scale /= declared_scale
        project, result = _native(project, store, "sketch.insert_" + asset.format,
                                 {"text": text, "scale": native_scale,
                                  "plane": payload.get("plane", "XY"), "at": payload.get("at", [0, 0]),
                                  "name": asset.name, "tolerance": payload.get("tolerance", 0.0001)})
        # Finishing makes imported curves available to feature consumers and reload.
        project, _ = _native(project, store, "sketch.finish", {})
    if isinstance(result, dict) and isinstance(result.get("feature"), int):
        asset = asset.model_copy(update={"feature": result["feature"]})
        project = _with_assets(project, asset)
    _check_cancel(cancel)
    return _proposal(project, {"asset": asset.model_dump(mode="json"), "native": result})


def _upload(store, project, payload, cancel):
    name = Path(str(payload.get("name", ""))).name
    extension = name.rsplit(".", 1)[-1].lower()
    if extension not in EXTENSIONS:
        raise ValueError("Choose a STEP, STL, OBJ, DXF, SVG or native .solidcraft/.json design")
    encoded = payload.get("content_base64", "")
    if not isinstance(encoded, str) or len(encoded) > MAX_SOURCE_BYTES * 4 // 3 + 8:
        raise ValueError("Source exceeds the 50 MiB upload limit")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid base64 source content") from error
    units = payload.get("units", "file" if extension in {"step", "stp", "dxf", "svg", "solidcraft", "json"} else "mm")
    if units not in {*UNIT_SCALE, "file"}:
        raise ValueError("Choose mm, cm, m, in, ft or declared file units")
    if extension in {"solidcraft", "json"}:
        if units != "file":
            raise ValueError("Native designs retain their declared dimensions; choose declared file units")
        source = _put_asset(store, content, name, extension, source_units="file")
        # Validate the real upstream document before the asset can be registered
        # or inserted. Sources with unresolved external links are refused first.
        from ..native_document import _check_imported_design
        from ..native import NativeSession
        path = _asset_path(store, source)
        _check_imported_design(path)
        with NativeSession() as session:
            session.execute("doc.open", {"path": str(path)})
            document = session.execute("oxygen.document.get")["document"]
            if extension == "json":
                # Upstream relink accepts its native container. Preserve JSON
                # bytes and register a genuine native snapshot for the source
                # revision, so insertion and later relinking use one format.
                with tempfile.TemporaryDirectory(prefix="oxygen-native-source-", dir=store.cache) as temporary:
                    target = Path(temporary) / (Path(name).stem + ".solidcraft")
                    session.execute("file.save_as", {"path": str(target), "cache": False, "preview": False})
                    snapshot = _put_asset(store, target.read_bytes(), target.name, "solidcraft",
                                          source_units="file", source_asset=source.id, operation="native_source_snapshot")
                project = _with_assets(project, source)
                source = snapshot
        if payload.get("register_only", False):
            project = _with_assets(project, source)
            return _proposal(project, {"asset": source.model_dump(mode="json"), "registered": True,
                                      "native_source": {"name": document.get("name"), "features": len(document.get("features", []))}})
    elif extension in {"step", "stp"}:
        if not content.lstrip().startswith(b"ISO-10303-21;"):
            raise ValueError("Expected an ISO-10303-21 STEP exchange file")
        if units != "file":
            raise ValueError("STEP uses its declared file units; overriding them would alter the part")
        source = _put_asset(store, content, name, extension, source_units="file")
    elif extension in {"dxf", "svg"}:
        text = content.decode("utf-8-sig")
        if extension == "dxf":
            units, scale = _drawing_scale(text, units)
        elif units != "file":
            raise ValueError("SVG uses declared dimensions or 96-DPI CSS pixels; choose declared file units")
        else:
            scale = 1.0
        source = _put_asset(store, content, name, extension, source_units=units, scale_to_mm=scale)
    else:
        if units == "file":
            raise ValueError("STL and OBJ have no reliable length unit; select source units")
        source = _put_asset(store, content, name, extension, source_units=units, scale_to_mm=UNIT_SCALE[units])
        mesh = _load_mesh(_asset_path(store, source), extension)
        mesh.apply_scale(source.scale_to_mm)
        diagnostics = mesh_diagnostics(mesh)
        source = source.model_copy(update={"diagnostics": diagnostics})
        project = _with_assets(project, source)
        # Normalize a unitless exchange file once. Original bytes and source units
        # remain immutable and linked; native geometry is always in millimetres.
        if units != "mm":
            normalized = _put_asset(store, mesh.export(file_type="stl"), name + " (mm).stl", "stl",
                                    source_units="mm", source_asset=source.id, operation="normalize_units",
                                    diagnostics=diagnostics)
            return _import_asset(store, project, normalized, payload, cancel)
    return _import_asset(store, project, source, payload, cancel)


def _artifact(store, project, extension, content):
    digest = hashlib.sha256(content).hexdigest()[:24]
    relative = f"interop/{project.revision}/{digest}.{extension}"
    path = store.cache / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"artifact": relative, "bytes": len(content), "format": extension,
            "units": "mm", "sha256": hashlib.sha256(content).hexdigest()}


def _export(store, project, payload, cancel):
    from ..native_document import opened_native
    extension = payload.get("format", "step")
    if extension not in {"step", "stl", "obj", "dxf", "json"}:
        raise ValueError("Export supports STEP, STL, OBJ, DXF and native JSON")
    _check_cancel(cancel)
    with opened_native(project, store=store) as session:
        if extension == "dxf":
            params = {"sketch": payload["sketch"]} if payload.get("sketch") is not None else {}
            result = session.execute("sketch.export_dxf", params)
            # Native sketch export declares millimetres in $INSUNITS.
            content = result["dxf"].encode()
        else:
            with tempfile.TemporaryDirectory(prefix="oxygen-exchange-", dir=store.cache) as temporary:
                target = Path(temporary) / f"design.{extension}"
                params = {"path": str(target), "format": extension}
                bodies = payload.get("bodies")
                if bodies:
                    if not isinstance(bodies, list) or not all(isinstance(v, str) for v in bodies):
                        raise ValueError("Export bodies must be a list of body names")
                    params["bodies"] = bodies
                result = session.execute("file.export", params)
                content = target.read_bytes()
    _check_cancel(cancel)
    return _artifact(store, project, extension, content) | {"native": {k: v for k, v in result.items() if k != "path"}}


def _mesh_operation(store, project, operation, payload, cancel):
    import numpy as np
    import trimesh
    asset = _find_asset(project, payload.get("asset_id"))
    if asset.format not in {"stl", "obj"}:
        raise ValueError("Choose an STL or OBJ source; export native bodies as a mesh to analyze them")
    mesh = _load_mesh(_asset_path(store, asset), asset.format)
    mesh.apply_scale(asset.scale_to_mm)
    before = mesh_diagnostics(mesh)
    if operation == "mesh.analyze":
        return {"asset_id": asset.id, "diagnostics": before}
    _check_cancel(cancel)
    details = {}
    if operation == "mesh.repair":
        mesh.update_faces(mesh.unique_faces())
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        mesh.merge_vertices()
        trimesh.repair.fix_normals(mesh, multibody=True)
        if payload.get("fill_small_holes", True):
            # Trimesh fills triangle/quad holes only; general gap filling is absent.
            trimesh.repair.fill_holes(mesh)
        details["limits"] = ["Only triangle and quad holes are filled. Nonmanifold edges and self-intersections may remain."]
    elif operation == "mesh.transform":
        scale = float(payload.get("scale", 1))
        translate = np.asarray(payload.get("translate", [0, 0, 0]), dtype=float)
        if not math.isfinite(scale) or scale <= 0 or translate.shape != (3,) or not np.isfinite(translate).all():
            raise ValueError("Provide a positive finite scale and a finite XYZ translation")
        mesh.apply_scale(scale)
        mesh.apply_translation(translate)
    elif operation == "mesh.subdivide":
        if len(mesh.faces) > 250_000:
            raise ValueError("Subdivision would exceed the one-million-triangle limit")
        vertices, faces = trimesh.remesh.subdivide(mesh.vertices, mesh.faces)
        mesh = trimesh.Trimesh(vertices, faces, process=False)
    elif operation == "mesh.cluster":
        cell = float(payload.get("cell_size_mm", 0))
        if not math.isfinite(cell) or cell <= 0:
            raise ValueError("Specify a positive clustering cell size in mm")
        cells = np.floor(mesh.vertices / cell).astype(np.int64)
        _, inverse = np.unique(cells, axis=0, return_inverse=True)
        count = np.bincount(inverse)
        vertices = np.column_stack([np.bincount(inverse, weights=mesh.vertices[:, axis]) / count for axis in range(3)])
        max_move = float(np.linalg.norm(mesh.vertices - vertices[inverse], axis=1).max())
        mesh = trimesh.Trimesh(vertices, inverse[mesh.faces], process=False)
        mesh.update_faces(mesh.unique_faces())
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        details = {"maximum_vertex_displacement_mm": max_move,
                   "limits": ["Vertex clustering may change topology. Displacement measures source vertices, not a surface Hausdorff bound. Inspect diagnostics before using the result."]}
    elif operation == "mesh.smooth":
        iterations = payload.get("iterations", 3)
        strength = float(payload.get("strength", 0.2))
        if not isinstance(iterations, int) or not 1 <= iterations <= 50 or not math.isfinite(strength) or not 0 < strength <= 1:
            raise ValueError("Smoothing requires 1–50 iterations and strength greater than zero and at most one")
        original_vertices = mesh.vertices.copy()
        trimesh.smoothing.filter_laplacian(mesh, lamb=strength, iterations=iterations, volume_constraint=False)
        details = {"maximum_vertex_displacement_mm": float(np.linalg.norm(mesh.vertices - original_vertices, axis=1).max()),
                   "limits": ["Laplacian smoothing can shrink the shape. Source vertices remain available in the immutable source."]}
    elif operation == "mesh.cut":
        normal = np.asarray(payload.get("normal", [0, 0, 1]), dtype=float)
        origin = np.asarray(payload.get("origin", [0, 0, 0]), dtype=float)
        if normal.shape != (3,) or origin.shape != (3,) or not np.isfinite(normal).all() or not np.isfinite(origin).all() or np.linalg.norm(normal) < 1e-12:
            raise ValueError("Cut requires a finite plane origin and nonzero normal")
        mesh = trimesh.intersections.slice_mesh_plane(mesh, plane_normal=normal, plane_origin=origin, cap=False)
        details = {"limits": ["Plane cut keeps the side toward the normal and leaves an open boundary. Automatic capping is unavailable."]}
    elif operation == "mesh.section":
        normal = np.asarray(payload.get("normal", [0, 0, 1]), dtype=float)
        origin = np.asarray(payload.get("origin", [0, 0, 0]), dtype=float)
        if normal.shape != (3,) or origin.shape != (3,) or not np.isfinite(normal).all() or not np.isfinite(origin).all() or np.linalg.norm(normal) < 1e-12:
            raise ValueError("Section requires a finite plane origin and nonzero normal")
        lines = trimesh.intersections.mesh_plane(mesh, plane_normal=normal, plane_origin=origin)
        if not len(lines):
            raise ValueError("Section plane does not intersect the mesh")
        # Preserve the section as 3D DXF LINE entities, including original coordinates.
        entries = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
        for a, b in lines:
            entries.extend(["0", "LINE", "8", "0"])
            for point, codes in [(a, [10, 20, 30]), (b, [11, 21, 31])]:
                for code, value in zip(codes, point):
                    entries.extend([str(code), format(float(value), ".15g")])
        entries.extend(["0", "ENDSEC", "0", "EOF"])
        return _artifact(store, project, "dxf", ("\n".join(entries) + "\n").encode()) | {"segments": len(lines)}
    else:
        raise ValueError(f"Unsupported mesh operation: {operation}")
    if not len(mesh.faces):
        raise ValueError("Operation removed every triangle; reduce the requested edit")
    mesh.merge_vertices()
    after = mesh_diagnostics(mesh)
    _check_cancel(cancel)
    derived = _put_asset(store, mesh.export(file_type="stl"), asset.name + f" ({operation}).stl", "stl",
                         source_asset=asset.id, operation=operation, diagnostics=after)
    if payload.get("insert", True):
        reply = _import_asset(store, project, derived, {}, cancel)
        reply["result"].update({"before": before, "after": after, **details})
        return reply
    project = _with_assets(project, derived)
    return _proposal(project, {"asset": derived.model_dump(mode="json"), "before": before, "after": after, **details})


def _step_operation(store, project, operation, payload, cancel):
    import build123d as bd
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeFix import ShapeFix_Shape
    asset = _find_asset(project, payload.get("asset_id"))
    if asset.format not in {"step", "stp"}:
        raise ValueError("Choose a STEP source")
    shape = bd.import_step(_asset_path(store, asset))

    def diagnostics(s):
        box = s.bounding_box()
        return {"valid": bool(BRepCheck_Analyzer(s.wrapped).IsValid()), "solids": len(s.solids()),
                "shells": len(s.shells()), "faces": len(s.faces()), "edges": len(s.edges()),
                "volume_mm3": float(s.volume), "area_mm2": float(s.area),
                "bounds_mm": [list(box.min), list(box.max)], "units": "mm"}

    before = diagnostics(shape)
    if operation == "step.analyze":
        return {"asset_id": asset.id, "diagnostics": before}
    tolerance = float(payload.get("tolerance_mm", 0.0001))
    if not math.isfinite(tolerance) or tolerance <= 0 or tolerance > 1:
        raise ValueError("Healing tolerance must be greater than zero and at most 1 mm")
    _check_cancel(cancel)
    fixer = ShapeFix_Shape(shape.wrapped)
    fixer.SetPrecision(tolerance)
    fixer.SetMaxTolerance(tolerance)
    fixer.Perform()
    fixed = bd.Compound.cast(fixer.Shape())
    after = diagnostics(fixed)
    if not after["valid"]:
        raise ValueError("OCCT healing did not produce valid topology; source remains unchanged")
    with tempfile.TemporaryDirectory(prefix="oxygen-heal-", dir=store.cache) as temporary:
        target = Path(temporary) / "healed.step"
        bd.export_step(fixed, target)
        derived = _put_asset(store, target.read_bytes(), asset.name + " (healed).step", "step",
                             source_units="file", source_asset=asset.id, operation="step.heal", diagnostics=after)
    _check_cancel(cancel)
    reply = _import_asset(store, project, derived, {}, cancel)
    reply["result"].update({"before": before, "after": after, "tolerance_mm": tolerance,
                            "volume_change_mm3": after["volume_mm3"] - before["volume_mm3"],
                            "limits": ["Healing repairs B-rep tolerances and topology. It does not infer missing design intent or restore an arbitrary missing face."]})
    return reply


def capabilities():
    return {"formats": sorted(EXTENSIONS), "native_units": "mm",
            "surface": {"available": ["patch", "extrude", "revolve", "sweep", "loft", "offset", "ruled", "blend", "stitch", "trim", "untrim", "unstitch", "thicken", "extend", "reverse_normal", "fair"],
                        "unavailable": {"fillet": "The pinned upstream surface.fillet handler is explicitly planned and has no geometric implementation."}},
            "mesh": {"available": ["analyze", "repair", "transform", "subdivide", "cluster", "smooth", "cut", "section", "convert"],
                     "convert_limits": "Native reconstruction accepts closed constant-section mechanical meshes; arbitrary scans and organic shapes are rejected.",
                     "unavailable": {"remesh": "General isotropic remeshing is not supplied by the pinned engines.", "self_intersection_repair": "No reliable triangle self-intersection repair engine is configured."}},
            "form": {"available": [], "unavailable": {"control_cage": "The pinned upstream has low-level T-spline mathematics, but no public persisted Form control-cage command/editor or general T-spline-to-B-rep workflow. Surface fairing edits B-splines and does not provide Form parity."}},
            "construction": {"available": ["plane.offset", "plane.at_angle", "plane.midplane", "plane.two_edges", "plane.three_points", "plane.tangent", "plane.along_path", "plane.perpendicular", "axis.through_cylinder", "axis.normal_to_face", "axis.two_planes", "axis.two_points", "axis.edge", "point.vertex", "point.two_edges", "point.three_planes", "point.center", "point.edge_plane", "point.along_path"]}}


def execute(store, project, operation: str, payload: dict, cancel=None) -> dict:
    """Worker entry: compute only; caller commits proposals with revision checks."""
    _check_cancel(cancel)
    store.cache.mkdir(parents=True, exist_ok=True)
    if operation == "capabilities":
        return capabilities()
    if operation == "import":
        return _upload(store, project, payload, cancel)
    if operation == "insert_asset":
        return _import_asset(store, project, _find_asset(project, payload.get("asset_id")), payload, cancel)
    if operation == "export":
        return _export(store, project, payload, cancel)
    if operation.startswith("form.") or operation == "surface.fillet":
        unavailable = capabilities()["form" if operation.startswith("form.") else "surface"]["unavailable"]
        raise ValueError(next(iter(unavailable.values())))
    if operation in {"step.analyze", "step.heal"}:
        return _step_operation(store, project, operation, payload, cancel)
    if operation.startswith("mesh.") and operation != "mesh.convert":
        return _mesh_operation(store, project, operation, payload, cancel)
    if operation.startswith(("surface.", "construct.")) or operation == "mesh.convert":
        updated, result = _native(project, store, operation, payload)
        _check_cancel(cancel)
        return _proposal(updated, result)
    raise ValueError(f"Unknown interop operation: {operation}")
