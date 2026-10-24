"""Versioned, non-destructive product scenes and bounded presentation publishing.

Appearance metadata never writes physical materials or engineering placements.  Native
scene meshes are the only geometry source; a missing core fails explicitly.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import uuid
import zipfile
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Coordinate = Annotated[float, Field(ge=-1e7, le=1e7)]
Vec = Annotated[list[Coordinate], Field(min_length=3, max_length=3)]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$")]
Target = Annotated[str, Field(min_length=1, max_length=2048, pattern=r"^[^\x00-\x1f]+$")]


class Data(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Camera(Data):
    target: Vec = [0, 0, 0]
    yaw: float = Field(default=-math.pi / 4, ge=-1000, le=1000)
    pitch: float = Field(default=0.61548, ge=-math.pi / 2, le=math.pi / 2)
    roll: float = Field(default=0, ge=-1000, le=1000)
    distance: float = Field(default=200, gt=0.001, le=1e7)
    fov: float = Field(default=0, ge=0, le=2.6)


class Asset(Data):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file: str = Field(pattern=r"^assets/visualization/[a-f0-9]{64}\.png$")
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)


class Mapping(Data):
    asset: str = Field(pattern=r"^[a-f0-9]{64}$")
    projection: Literal["xy", "yz", "zx", "cylindrical_z"] = "xy"
    origin: Vec = [0, 0, 0]
    size_mm: Annotated[list[Coordinate], Field(min_length=2, max_length=2)] = [20, 20]
    rotation: float = Field(default=0, ge=-1000, le=1000)
    opacity: float = Field(default=1, ge=0, le=1)
    repeat: bool = False

    @model_validator(mode="after")
    def positive_size(self):
        if min(self.size_mm) <= 1e-6:
            raise ValueError("Texture sizes must be positive millimeters")
        return self


class Appearance(Data):
    color: str = Field(default="#739bad", pattern=r"^#[a-fA-F0-9]{6}$")
    metallic: float = Field(default=0, ge=0, le=1)
    roughness: float = Field(default=0.4, ge=0.045, le=1)
    opacity: float = Field(default=1, ge=0.01, le=1)
    texture: Mapping | None = None


class Section(Data):
    name: Name = "Section"
    normal: Vec = [1, 0, 0]
    offset_mm: float = Field(default=0, ge=-1e7, le=1e7)
    enabled: bool = True

    @model_validator(mode="after")
    def nonzero_normal(self):
        if sum(x*x for x in self.normal) < 1e-12:
            raise ValueError("Section normal cannot be zero")
        return self


class Light(Data):
    name: Name = "Key"
    kind: Literal["directional", "point", "spot"] = "directional"
    vector: Vec = [0.3, -0.4, 0.85]
    color: str = Field(default="#ffffff", pattern=r"^#[a-fA-F0-9]{6}$")
    intensity: float = Field(default=0.85, ge=0, le=100)
    target: Vec = [0, 0, 0]
    cone_degrees: float = Field(default=40, gt=0, le=170)
    reference_distance_mm: float = Field(default=100, gt=0, le=1e7)

    @model_validator(mode="after")
    def direction(self):
        if self.kind == "directional" and sum(x*x for x in self.vector) < 1e-12:
            raise ValueError("Directional light direction cannot be zero")
        if self.kind == "spot" and self.vector == self.target:
            raise ValueError("Spot light target must differ from position")
        return self


class Scene(Data):
    name: Name = "Product"
    camera: Camera = Field(default_factory=Camera)
    width: int = Field(default=640, ge=16, le=2048)
    height: int = Field(default=480, ge=16, le=2048)
    background: str = Field(default="#eef1f4", pattern=r"^#[a-fA-F0-9]{6}$")
    transparent: bool = False
    exposure_ev: float = Field(default=0, ge=-8, le=8)
    ambient: float = Field(default=0.25, ge=0, le=4)
    environment_rotation: float = Field(default=0, ge=-1000, le=1000)
    lights: list[Light] = Field(default_factory=lambda: [Light()], max_length=8)
    style: Literal["shaded_edges", "shaded", "wireframe"] = "shaded_edges"
    appearances: dict[Target, Appearance] = Field(default_factory=dict, max_length=1024)
    hidden: list[Target] = Field(default_factory=list, max_length=2048)
    isolated: list[Target] = Field(default_factory=list, max_length=2048)
    sections: list[Section] = Field(default_factory=list, max_length=6)
    transforms: dict[Target, Vec] = Field(default_factory=dict, max_length=2048)
    renderer: Literal["cpu-raster", "native-cpu-raytrace"] = "cpu-raster"

    @model_validator(mode="after")
    def budget(self):
        if self.width * self.height > 2_097_152:
            raise ValueError("Render exceeds two megapixels")
        return self


class Keyframe(Data):
    time: float = Field(ge=0, le=120)
    camera: Camera | None = None
    transforms: dict[Target, Vec] = Field(default_factory=dict, max_length=2048)
    hidden: list[Target] | None = Field(default=None, max_length=2048)
    callout: str = Field(default="", max_length=300)


class Storyboard(Data):
    name: Name = "Assembly"
    scene: Name = "Product"
    duration: float = Field(default=3, gt=0, le=120)
    fps: int = Field(default=10, ge=1, le=30)
    keyframes: list[Keyframe] = Field(default_factory=lambda: [Keyframe(time=0)], min_length=1, max_length=256)

    @model_validator(mode="after")
    def ordered_frames(self):
        times = [k.time for k in self.keyframes]
        if sorted(set(times)) != times or times[0] != 0 or times[-1] > self.duration:
            raise ValueError("Keyframes need unique increasing times starting at zero, within duration")
        if math.ceil(self.duration * self.fps) > 600:
            raise ValueError("Storyboard exceeds 600 frames")
        return self


class Visualization(Data):
    schema_version: Literal[1] = 1
    active_scene: Name = "Product"
    scenes: list[Scene] = Field(default_factory=lambda: [Scene()], min_length=1, max_length=32)
    storyboards: list[Storyboard] = Field(default_factory=list, max_length=16)
    assets: dict[str, Asset] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def references(self):
        names = [s.name for s in self.scenes]
        if len(set(names)) != len(names) or self.active_scene not in names:
            raise ValueError("Scene names must be unique and active scene must exist")
        if len({s.name for s in self.storyboards}) != len(self.storyboards):
            raise ValueError("Storyboard names must be unique")
        for board in self.storyboards:
            if board.scene not in names:
                raise ValueError(f"Unknown storyboard scene: {board.scene}")
        for key, asset in self.assets.items():
            if key != asset.sha256 or asset.file != f"assets/visualization/{key}.png":
                raise ValueError("Asset path must match its content hash")
        for scene in self.scenes:
            for appearance in scene.appearances.values():
                if appearance.texture and appearance.texture.asset not in self.assets:
                    raise ValueError("Texture asset is missing")
        return self


def validate(data):
    return Visualization.model_validate(data).model_dump(mode="json")


def cancelled(cancel):
    if cancel is not None and cancel.is_set():
        from ..solver import Cancelled
        raise Cancelled("Visualization cancelled")


def _data(project, payload):
    return Visualization.model_validate(payload.get("data", getattr(project, "domains", {}).get("visualization", {})))


def capture_scene(store, project):
    """Read native geometry without evaluating host-controlled code or editing a design."""
    from ..native_document import opened_native
    with opened_native(project, store) as engine:
        return engine.execute("oxygen.scene", {"mesh": True})


def sample(scene: Scene, board: Storyboard, time: float):
    """Absolute, non-destructive timeline; transforms never accumulate between frames."""
    t = min(max(float(time), 0), board.duration)
    previous = board.keyframes[0]
    following = previous
    for key in board.keyframes:
        if key.time <= t:
            previous = key
        if key.time > t:
            following = key
            break
    else:
        following = previous
    ratio = 0 if following.time == previous.time else (t-previous.time)/(following.time-previous.time)
    # Missing values carry forward from the most recent explicit action.
    left = scene.model_copy(deep=True)
    callout = ""
    for key in board.keyframes:
        if key.time > previous.time:
            break
        if key.camera:
            left.camera = key.camera.model_copy(deep=True)
        left.transforms.update(copy.deepcopy(key.transforms))
        if key.hidden is not None:
            left.hidden = key.hidden.copy()
        callout = key.callout
    if following is not previous:
        if following.camera:
            a, b = left.camera.model_dump(), following.camera.model_dump()
            left.camera = Camera(**{k: ([x+(y-x)*ratio for x,y in zip(v,b[k])] if isinstance(v,list)
                else v+(b[k]-v)*ratio) for k,v in a.items()})
        for target, end in following.transforms.items():
            start = left.transforms.get(target, [0,0,0])
            left.transforms[target] = [a+(b-a)*ratio for a,b in zip(start,end)]
    return left, callout


def _import_image(store, payload):
    from PIL import Image
    encoded = payload.get("base64", "")
    if not isinstance(encoded, str) or len(encoded) > 8_000_000:
        raise ValueError("Image upload exceeds 6 MiB")
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as source:
        if source.width > 4096 or source.height > 4096 or source.width*source.height > 8_388_608:
            raise ValueError("Texture exceeds image dimensions budget")
        image = source.convert("RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    content = buffer.getvalue()
    if len(content)>8*1024*1024:
        raise ValueError("Normalized texture exceeds 8 MiB; reduce image dimensions")
    digest = hashlib.sha256(content).hexdigest()
    relative = f"assets/visualization/{digest}.png"
    path = store.root / relative
    if not path.resolve().is_relative_to(store.root.resolve()):
        raise ValueError("Project asset path escapes project")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    cached = store.cache / "visualization-assets" / f"{digest}.png"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(content)
    return Asset(sha256=digest, file=relative, width=image.width, height=image.height).model_dump()


def _result_folder(store):
    folder = store.cache / "visualization" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    return folder


def _manifest(store, project, data, scene, folder, outputs, **details):
    from ..store import write_json
    document = project.model_dump(mode="json")
    result = {"schema_version": 1, "revision": project.revision,
        "document_sha256": hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
        "scene_sha256": hashlib.sha256(scene.model_dump_json().encode()).hexdigest(),
        "scene": scene.model_dump(mode="json"), "artifacts": outputs, **details}
    write_json(folder / "result.json", result)
    result["manifest"] = str((folder / "result.json").relative_to(store.cache))
    return result


def execute(store, project, operation, payload, cancel=None):
    cancelled(cancel)
    if operation == "import_image":
        return {"asset": _import_image(store, payload)}
    if operation == "capabilities":
        from ..native import NativeSession
        with NativeSession() as engine:
            native = engine.execute("render.backends")
        return {"renderer": "cpu-raster", "algorithm": "triangle z-buffer, direct Blinn-Phong illumination, straight alpha",
            "photorealistic": False, "native": native, "image": ["png"], "animation": ["png_sequence", "gif"] + (["mp4"] if shutil.which("ffmpeg") else []),
            "limitations": ["CPU raster has no global illumination, shadows, reflections, refraction or HDR lighting", "Sections are clipped surfaces with cut edges, without solid caps", "No hosted sharing or cloud worker"],
            "lights_units": "relative intensity; positional falloff uses explicit reference distance"}
    if operation == "scene":
        from .visualization_render import load_textures
        load_textures(store, _data(project, payload).assets, decode=False)
        return capture_scene(store, project)
    if operation == "gallery":
        results = []
        for path in sorted((store.cache / "visualization").glob("*/result.json"), key=lambda p:p.stat().st_mtime, reverse=True)[:30]:
            result = json.loads(path.read_text())
            result["stale"] = result["revision"] != project.revision
            result["manifest"] = str(path.relative_to(store.cache))
            results.append(result)
        return {"results": results}
    data = _data(project, payload)
    scene_name = payload.get("scene", data.active_scene)
    scene = next((s for s in data.scenes if s.name == scene_name), None)
    if scene is None:
        raise ValueError("Unknown saved scene")
    if operation == "sample":
        board = next((b for b in data.storyboards if b.name == payload.get("storyboard")), None)
        if board is None:
            raise ValueError("Unknown storyboard")
        sampled, text = sample(next(s for s in data.scenes if s.name == board.scene), board, payload.get("time", 0))
        return {"scene": sampled.model_dump(mode="json"), "callout": text}
    if operation not in ("render", "publish"):
        raise ValueError(f"Unknown visualization operation: {operation}")
    geometry = capture_scene(store, project)
    from .visualization_render import render_image, validate_targets, native_render
    validate_targets(geometry, scene)
    folder = _result_folder(store)
    relative = lambda p: str(p.relative_to(store.cache))
    if operation == "render":
        if scene.renderer == "native-cpu-raytrace":
            evidence = native_render(store, project, scene, folder / "image.png", cancel)
        else:
            image, evidence = render_image(store, geometry, scene, data.assets, cancel)
            image.save(folder / "image.png")
        cancelled(cancel)
        return _manifest(store, project, data, scene, folder, {"image": relative(folder/"image.png")}, **evidence)
    board = next((b for b in data.storyboards if b.name == payload.get("storyboard")), None)
    if board is None:
        raise ValueError("Unknown storyboard")
    scene = next(s for s in data.scenes if s.name == board.scene)
    if scene.renderer != "cpu-raster":
        raise ValueError("Animation currently requires CPU raster; select that renderer explicitly")
    format_name = payload.get("format", "png_sequence")
    if format_name not in ("png_sequence", "gif", "mp4"):
        raise ValueError("Unsupported animation format")
    if format_name == "mp4" and not shutil.which("ffmpeg"):
        raise ValueError("MP4 requires an installed ffmpeg encoder; PNG sequence and GIF are available")
    if format_name == "mp4" and (scene.width%2 or scene.height%2):
        raise ValueError("H.264 yuv420p export requires even pixel dimensions")
    if format_name == "gif" and 100%board.fps:
        raise ValueError("GIF needs a frame rate dividing 100 (1, 2, 4, 5, 10, 20, 25); use PNG sequence for other rates")
    count = math.ceil(board.duration*board.fps)
    if scene.width*scene.height*count > 120_000_000:
        raise ValueError("Animation exceeds 120 million output pixels; reduce resolution/duration/FPS")
    from PIL import ImageDraw
    frames = []
    for index in range(count):
        cancelled(cancel)
        # Include both endpoint poses, retaining declared movie duration count/fps.
        t = board.duration*index/max(count-1, 1)
        current, text = sample(scene, board, t)
        image, evidence = render_image(store, geometry, current, data.assets, cancel)
        if text:
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, image.height-36, image.width, image.height), fill=(18,22,26,235))
            draw.text((12,image.height-25), text, fill="white")
        path = folder / f"frame-{index:04d}.png"
        image.save(path)
        frames.append(path)
    outputs = {}
    if format_name == "png_sequence":
        with zipfile.ZipFile(folder / "frames.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in frames:
                cancelled(cancel)
                archive.write(path, path.name)
            archive.writestr("timing.json", json.dumps({"fps":board.fps,"duration":count/board.fps,"frames":count,"endpoint_inclusive":True}))
        outputs["sequence"] = relative(folder/"frames.zip")
    elif format_name == "gif":
        from PIL import Image
        # GIF timing is centiseconds. Refuse lossy timing, rather than claiming exact FPS.
        images = [Image.open(p).convert("RGB") for p in frames]
        images[0].save(folder/"movie.gif", save_all=True, append_images=images[1:], duration=1000//board.fps, loop=0, disposal=2)
        for image in images:
            image.close()
        outputs["movie"] = relative(folder/"movie.gif")
    else:
        from ..solver import run_process
        run_process([shutil.which("ffmpeg"), "-y", "-framerate", str(board.fps), "-i", str(folder/"frame-%04d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder/"movie.mp4")], folder, folder/"encoder.log", cancel, timeout=180)
        outputs["movie"] = relative(folder/"movie.mp4")
    outputs.update({"first_frame":relative(frames[0]),"last_frame":relative(frames[-1])})
    return _manifest(store, project, data, scene, folder, outputs, renderer="cpu-raster", photorealistic=False,
        storyboard=board.model_dump(mode="json"), fps=board.fps, duration=count/board.fps, frame_count=count,
        dimensions=[scene.width,scene.height], format=format_name, endpoint_inclusive=True)
