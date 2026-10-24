"""The in-repository Rust engine. Python owns project transactions and verification."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile

from .model import EdgeTreatment, Extrude, Hole, NativeFeature, Profile, Project

UPSTREAM_COMMIT = "0520910bfd4ff6d24c850f4a20a604f6128f87bf"


def engine_binary() -> str:
    configured = os.environ.get("O3D_ENGINE_BINARY")
    binary = Path(configured) if configured else Path(__file__).resolve().parents[2] / "engine/target/release/oxygen-engine"
    if not binary.is_file() and not configured:
        located = shutil.which("oxygen-engine")
        if located:
            return located
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("Rust engine is not built. Run cargo build --release --locked --manifest-path engine/Cargo.toml, or use the Docker runtime.")
    return str(binary)


class NativeSession:
    """One persistent, isolated engine process per compilation; no network server."""

    def __enter__(self):
        self.log = tempfile.TemporaryFile(mode="w+t")
        self.process = subprocess.Popen([engine_binary()], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, text=True, bufsize=1)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.commands = []
        return self

    def execute(self, command: str, params=None):
        request = {"command": command, "params": params or {}}
        encoded = json.dumps(request, allow_nan=False)
        if len(encoded.encode()) > 16 * 1024 * 1024:
            raise ValueError("Engine request exceeds 16 MiB")
        try:
            self.process.stdin.write(encoded + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError("Rust engine exited unexpectedly") from error
        if not self.selector.select(timeout=120):
            self.process.kill()
            raise RuntimeError("Rust engine operation exceeded 120 seconds")
        line = self.process.stdout.readline()
        if not line:
            self.log.seek(0)
            raise RuntimeError("Rust engine exited: " + self.log.read()[-2000:])
        reply = json.loads(line)
        if not reply.get("ok"):
            raise ValueError(reply.get("error", "Rust engine failed"))
        self.commands.append(request)
        return reply["result"]

    def __exit__(self, *_):
        self.selector.close()
        self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.log.close()


def _resolved(project, value):
    if isinstance(value, str) and value.startswith("$"):
        return project.resolve(value)
    if isinstance(value, dict):
        return {k: _resolved(project, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolved(project, v) for v in value]
    return value


def compile_native(project: Project, destination: Path):
    """Compile both native feature commands and the existing editable feature graph."""
    import build123d as bd
    from OCP.gp import gp_Vec
    from .geometry import select_edges

    def vector(v):
        return [v.X, v.Y, v.Z]

    profiles = {}
    with NativeSession() as session:
        metadata = session.execute("oxygen.version")
        if metadata.get("upstream_commit") != UPSTREAM_COMMIT or metadata.get("protocol") != 1:
            raise RuntimeError("Rust engine version does not match this project's pinned toolchain")

        def measure():
            return session.execute("inspect.measure")

        def sketch(profile, name):
            base = {"XY": bd.Plane.XY, "XZ": bd.Plane.XZ, "YZ": bd.Plane.YZ}[profile.plane]
            transform = bd.Location(profile.origin, profile.rotation).wrapped.Transformation()
            x_dir = bd.Vector(base.x_dir.wrapped.Transformed(transform))
            y_dir = bd.Vector(base.y_dir.wrapped.Transformed(transform))
            session.execute("sketch.create", {"name": name, "plane": {
                "origin": list(profile.origin), "x_dir": vector(x_dir), "y_dir": vector(y_dir)}})
            if profile.shape == "rectangle":
                w, h = project.resolve(profile.width), project.resolve(profile.height)
                session.execute("sketch.rectangle.two_point", {"p0": [-w/2, -h/2], "p1": [w/2, h/2]})
            elif profile.shape == "circle":
                session.execute("sketch.circle.center", {"center": [0, 0], "radius": project.resolve(profile.radius)})
            else:
                session.execute("sketch.line", {"points": profile.points, "closed": True})
            session.execute("sketch.finish")

        for feature in project.features:
            if feature.suppressed:
                continue
            try:
                if isinstance(feature, Profile):
                    profiles[feature.id] = feature
                    continue
                if isinstance(feature, NativeFeature):
                    session.execute(feature.command, _resolved(project, feature.params))
                elif isinstance(feature, Extrude):
                    if feature.profile not in profiles:
                        raise ValueError("The referenced profile is suppressed or unavailable")
                    before = measure()
                    if feature.operation == "cut" and not before["body_count"]:
                        raise ValueError("The first extrusion must add material")
                    sketch(profiles[feature.profile], f"{feature.profile}_{feature.id}")
                    distance = project.resolve(feature.distance)
                    session.execute("solid.extrude", {"distance": abs(distance), "direction": "negative" if distance < 0 else "positive",
                        "operation": "cut" if feature.operation == "cut" else "join" if before["body_count"] else "new",
                        "body_name": "Oxygen part", "name": feature.name or feature.id})
                    if abs(measure()["total"]["volume_mm3"] - before["total"]["volume_mm3"]) < 1e-8:
                        raise ValueError("Operation made no measured change to the solid")
                elif isinstance(feature, Hole):
                    before = measure()
                    if before["body_count"] != 1:
                        raise ValueError("A hole needs one existing solid")
                    axis = "XYZ".index(feature.axis)
                    bbox = before["bodies"][0]["bbox"]
                    normal = [int(i == axis) for i in range(3)]
                    origin = list(feature.center)
                    if feature.through:
                        origin[axis] = bbox["min"][axis] - 1
                        depth = bbox["max"][axis] - bbox["min"][axis] + 2
                    else:
                        normal[axis] = -1 if feature.reverse else 1
                        depth = project.resolve(feature.depth)
                    plane = bd.Plane(origin=origin, z_dir=normal)
                    session.execute("sketch.create", {"name": feature.id, "plane": {
                        "origin": origin, "x_dir": vector(plane.x_dir), "y_dir": vector(plane.y_dir)}})
                    other_axes = [i for i in range(3) if i != axis]
                    for row in range(feature.rows):
                        for column in range(feature.columns):
                            point = list(origin)
                            point[other_axes[0]] += column * feature.spacing[0]
                            point[other_axes[1]] += row * feature.spacing[1]
                            delta = bd.Vector(point) - bd.Vector(origin)
                            center = [delta.dot(plane.x_dir), delta.dot(plane.y_dir)]
                            session.execute("sketch.circle.center", {"center": center, "diameter": project.resolve(feature.diameter)})
                    session.execute("sketch.finish")
                    session.execute("solid.extrude", {"distance": depth, "operation": "cut", "name": feature.name or feature.id})
                    if before["total"]["volume_mm3"] - measure()["total"]["volume_mm3"] < 1e-8:
                        raise ValueError("Hole pattern did not intersect the solid")
                elif isinstance(feature, EdgeTreatment):
                    # Resolve the same public semantic/strict selections used by the review UI,
                    # against an independent STEP import. The actual operation is performed in Rust.
                    selection_step = destination / "selection.step"
                    session.execute("file.export", {"path": str(selection_step)})
                    selected = select_edges(bd.import_step(selection_step), feature.selection)
                    if not selected:
                        raise ValueError("Edge selection is empty")
                    edges = [vector(e.position_at(0.5)) for e in selected]
                    session.execute("solid." + feature.type, {"edges": edges,
                        "radius" if feature.type == "fillet" else "distance": project.resolve(feature.size)})
            except Exception as error:
                raise ValueError(f"Feature {feature.id}: {error}") from error
        measured = measure()
        if measured["body_count"] != 1 or measured["total"]["volume_mm3"] <= 0:
            raise ValueError("The project must produce exactly one connected, positive-volume solid")
        body = measured["bodies"][0]["name"]
        if any(project.placement.rotation):
            quaternion = bd.Location((0, 0, 0), project.placement.rotation).wrapped.Transformation().GetRotation()
            direction = gp_Vec()
            angle = quaternion.GetVectorAndAngle(direction)[0]
            if abs(angle) > 1e-12:
                session.execute("solid.move", {"bodies": [body], "axis": [direction.X(), direction.Y(), direction.Z()], "angle": math.degrees(angle)})
        if any(project.placement.translation):
            session.execute("solid.move", {"bodies": [body], "translate": project.placement.translation})
        if project.material is not None:
            session.execute("material.create", {"name": "Oxygen project material", "density_g_cm3": project.material.density_g_cm3})
            session.execute("material.assign", {"bodies": [body], "material": "Oxygen project material"})
        measured = measure()
        recipe = {"commands": [c for c in session.commands if not c["command"].startswith(("oxygen.", "inspect.", "file."))]}
        session.execute("file.export", {"path": str(destination / "part.step")})
        session.execute("file.save_as", {"path": str(destination / "part.solidcraft")})
        (destination / "engine-script.json").write_text(json.dumps(recipe, indent=2) + "\n")
        (destination / "engine-measures.json").write_text(json.dumps(measured, indent=2) + "\n")
    shape = bd.import_step(destination / "part.step")
    if not shape.is_valid or len(shape.solids()) != 1 or shape.volume <= 0:
        raise ValueError("Independent OCCT validation rejected the Rust engine STEP export")
    disagreement = abs(shape.volume - measured["total"]["volume_mm3"]) / shape.volume
    if disagreement > 1e-3:
        raise ValueError(f"Independent OCCT volume disagrees with Rust measurements ({disagreement:.3%})")
    return shape, metadata | {"independent_volume_relative_error": disagreement}
