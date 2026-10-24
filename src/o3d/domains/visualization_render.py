"""Bounded CPU raster product renderer; no physical simulation or photo claim."""
from __future__ import annotations

import hashlib
import math
import time

import numpy as np
from PIL import Image, ImageColor

from .visualization import Appearance, cancelled


def rgb(color):
    return np.array(ImageColor.getrgb(color), dtype=float)/255


def basis(camera):
    yaw, pitch, roll = camera.yaw, camera.pitch, camera.roll
    back = np.array([-math.sin(yaw)*math.cos(pitch), -math.cos(yaw)*math.cos(pitch), math.sin(pitch)])
    right = np.array([math.cos(yaw), -math.sin(yaw), 0.0])
    up = np.cross(back,right)
    return right*math.cos(roll)+up*math.sin(roll), up*math.cos(roll)-right*math.sin(roll), back


def triangles(geometry):
    for body in geometry["bodies"]:
        mesh = body.get("mesh") or {}
        if "faces" in mesh:
            for face in mesh["faces"]:
                vertices = np.array(face["vertices"], dtype=float)
                for tri in face["triangles"]:
                    yield body, face["id"], vertices[tri]
        else:
            vertices = np.array(mesh.get("positions", []), dtype=float)
            faces = mesh.get("tri_face", [])
            for index, tri in enumerate(mesh.get("triangles", [])):
                face = faces[index] if index < len(faces) else -1
                yield body, f"{body['id']}:face:{face}", vertices[tri]


def validate_targets(geometry, scene):
    bodies = {b["id"] for b in geometry["bodies"]}
    faces = {face for _, face, _ in triangles(geometry)}
    targets = set(scene.appearances) | set(scene.transforms) | set(scene.hidden) | set(scene.isolated)
    missing = targets - bodies - faces - {"*"}
    if missing:
        raise ValueError("Unresolved scene targets; repair assignments: " + ", ".join(sorted(missing)[:5]))
    if set(scene.transforms)-bodies:
        raise ValueError("Presentation translations require exact body targets")
    if "*" in scene.hidden or "*" in scene.isolated:
        raise ValueError("Visibility actions require exact body or face targets")


def clip_polygon(vertices, normal, offset):
    output = []
    for a,b in zip(vertices, vertices[1:]+vertices[:1]):
        da,db = float(np.dot(a,normal)-offset), float(np.dot(b,normal)-offset)
        if da <= 0:
            output.append(a)
        if (da <= 0) != (db <= 0):
            output.append(a+(b-a)*(da/(da-db)))
    return output


def load_textures(store, assets, decode=True):
    textures = {}
    if decode and sum(a.width*a.height for a in assets.values())>8_388_608:
        raise ValueError("Active textures exceed eight million decoded pixels")
    for digest, asset in assets.items():
        path = (store.root / asset.file).resolve()
        root = (store.root / "assets" / "visualization").resolve()
        if not root.is_relative_to(store.root.resolve()) or not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Project texture is missing")
        if path.stat().st_size>8*1024*1024:
            raise ValueError("Project texture exceeds 8 MiB")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Project texture hash mismatch")
        cached = store.cache / "visualization-assets" / f"{digest}.png"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(content)
        with Image.open(path) as image:
            if image.size != (asset.width,asset.height):
                raise ValueError("Project texture dimensions changed")
            if decode:
                textures[digest] = np.asarray(image.convert("RGBA"), dtype=float)/255
    return textures


def apply_texture(base, points, mapping, textures):
    points = points-np.array(mapping.origin)
    if mapping.projection == "cylindrical_z":
        # One authored image width spans the cylinder circumference; Y is axial mm.
        uv = np.stack((np.arctan2(points[...,1],points[...,0])/(2*math.pi)+0.5, points[...,2]/mapping.size_mm[1]+0.5),axis=-1)
    else:
        axes = {"xy":(0,1),"yz":(1,2),"zx":(2,0)}[mapping.projection]
        uv = points[...,axes].copy()
        c,s = math.cos(mapping.rotation),math.sin(mapping.rotation)
        uv = uv @ np.array([[c,-s],[s,c]])
        uv = uv/np.array(mapping.size_mm)+0.5
    if mapping.projection == "cylindrical_z":
        angle = mapping.rotation/(2*math.pi)
        uv[...,0] = (uv[...,0]+angle)%1
    valid = np.all((uv>=0)&(uv<=1),axis=-1)
    if mapping.repeat:
        uv %= 1
        valid[:] = True
    texture = textures[mapping.asset]
    x = np.clip((uv[...,0]*(texture.shape[1]-1)).astype(int),0,texture.shape[1]-1)
    y = np.clip(((1-uv[...,1])*(texture.shape[0]-1)).astype(int),0,texture.shape[0]-1)
    pixel = texture[y,x]
    alpha = pixel[...,3]*mapping.opacity*valid
    return base*(1-alpha[...,None])+pixel[...,:3]*alpha[...,None]


def render_image(store, geometry, scene, assets, cancel=None):
    """Perspective-correct triangle rasterization with per-pixel depth and direct lights."""
    validate_targets(geometry, scene)
    needed={a.texture.asset for a in scene.appearances.values() if a.texture}
    if needed-assets.keys():
        raise ValueError("Project texture asset metadata is missing")
    textures = load_textures(store, {digest:assets[digest] for digest in needed})
    width,height = scene.width,scene.height
    image = np.empty((height,width,4),dtype=float)
    image[:,:,:3] = rgb(scene.background)
    image[:,:,3] = 0 if scene.transparent else 1
    depth = np.full((height,width),np.inf)
    right,up,back = basis(scene.camera)
    target = np.array(scene.camera.target)
    eye = target+back*scene.camera.distance
    half = scene.camera.distance*(math.tan(scene.camera.fov/2) if scene.camera.fov else 0.4)
    scale = height/(2*half)
    primitives = []
    source_triangles = 0
    hidden, isolated = set(scene.hidden),set(scene.isolated)
    for body,face,points in triangles(geometry):
        source_triangles += 1
        if source_triangles > 200_000:
            raise ValueError("CPU raster scene exceeds 200,000 triangles")
        if not body.get("visible",True) or body["id"] in hidden or face in hidden or (isolated and body["id"] not in isolated and face not in isolated):
            continue
        translation = np.array(scene.transforms.get(body["id"],[0,0,0]))
        points = points+translation
        polygon = list(points)
        for section in scene.sections:
            if section.enabled:
                normal = np.array(section.normal,dtype=float)
                normal /= np.linalg.norm(normal)
                polygon = clip_polygon(polygon,normal,section.offset_mm)
                if len(polygon)<3:
                    break
        if len(polygon)<3:
            continue
        # Cut the near plane rather than projecting behind-eye vertices.
        polygon = clip_polygon(polygon,back,float(np.dot(eye,back)-0.001))
        for i in range(1,len(polygon)-1):
            tri = np.array([polygon[0],polygon[i],polygon[i+1]])
            normal = np.cross(tri[1]-tri[0],tri[2]-tri[0])
            norm = np.linalg.norm(normal)
            if norm<1e-12:
                continue
            normal /= norm
            if np.dot(normal,eye-tri.mean(0))<0:
                normal *= -1
            # Explicit order: face override, occurrence/body override, default.
            appearance = scene.appearances.get(face,scene.appearances.get(body["id"],scene.appearances.get("*",Appearance())))
            primitives.append((float(np.mean((eye-tri)@back)),tri,normal,appearance,translation))
    # Far-to-near makes conventional alpha compositing deterministic. This remains
    # an approximation for intersecting transparent surfaces; no refraction claim.
    work = 0
    for index,(_,tri,normal,appearance,translation) in enumerate(sorted(primitives,key=lambda p:-p[0])):
        if index%32==0:
            cancelled(cancel)
        z = (eye-tri)@back
        factor = scene.camera.distance/z if scene.camera.fov else np.ones(3)
        xy = np.stack(((tri-target)@right*factor*scale+width/2, height/2-(tri-target)@up*factor*scale),axis=1)
        xmin,ymin = np.maximum(np.floor(xy.min(0)),[0,0]).astype(int)
        xmax,ymax = np.minimum(np.ceil(xy.max(0)),[width-1,height-1]).astype(int)
        if xmax<xmin or ymax<ymin:
            continue
        work += (xmax-xmin+1)*(ymax-ymin+1)
        if work > 150_000_000:
            raise ValueError("Raster work budget exceeded; reduce image size or mesh complexity")
        xs,ys = np.meshgrid(np.arange(xmin,xmax+1)+0.5,np.arange(ymin,ymax+1)+0.5)
        a,b,c = xy
        denominator = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(denominator)<1e-12:
            continue
        u = ((b[1]-c[1])*(xs-c[0])+(c[0]-b[0])*(ys-c[1]))/denominator
        v = ((c[1]-a[1])*(xs-c[0])+(a[0]-c[0])*(ys-c[1]))/denominator
        bary = np.stack((u,v,1-u-v),axis=-1)
        inside = np.all(bary>=-1e-8,axis=-1)
        if scene.camera.fov:
            corrected = bary/z
            corrected /= np.maximum(corrected.sum(-1,keepdims=True),1e-20)
        else:
            corrected = bary
        zz = corrected@z
        region = depth[ymin:ymax+1,xmin:xmax+1]
        mask = inside&(zz<region+1e-7)
        if scene.style == "wireframe":
            mask &= bary.min(-1)<0.015
        if not mask.any():
            continue
        world = corrected@tri
        base = np.broadcast_to(rgb(appearance.color),world.shape).copy()
        if appearance.texture:
            base = apply_texture(base,world-translation,appearance.texture,textures)
        illumination = base*scene.ambient
        view = eye-world
        view /= np.maximum(np.linalg.norm(view,axis=-1,keepdims=True),1e-12)
        for light in scene.lights:
            direction = np.array(light.vector,dtype=float)
            if light.kind == "directional":
                rotation = scene.environment_rotation
                direction = direction @ np.array([[math.cos(rotation),math.sin(rotation),0],[-math.sin(rotation),math.cos(rotation),0],[0,0,1]])
                direction /= np.linalg.norm(direction)
                intensity = light.intensity
            else:
                direction = direction-world
                distance = np.maximum(np.linalg.norm(direction,axis=-1),1e-6)
                direction /= distance[...,None]
                intensity = light.intensity*(light.reference_distance_mm/np.maximum(distance,light.reference_distance_mm*0.01))**2
                if light.kind == "spot":
                    axis = np.array(light.target)-np.array(light.vector)
                    axis /= np.linalg.norm(axis)
                    intensity *= ((-direction)@axis)>=math.cos(math.radians(light.cone_degrees/2))
            diffuse = np.maximum(direction@normal,0)
            half_vector = direction+view
            half_vector /= np.maximum(np.linalg.norm(half_vector,axis=-1,keepdims=True),1e-12)
            exponent = max(2,2/(appearance.roughness**2)-2)
            specular = np.maximum(half_vector@normal,0)**exponent
            f0 = (1-appearance.metallic)*0.04+base*appearance.metallic
            contribution = base*(1-appearance.metallic)*np.asarray(diffuse)[...,None]+f0*specular[...,None]
            illumination += contribution*rgb(light.color)*np.asarray(intensity)[...,None]
        color = np.clip(illumination*(2**scene.exposure_ev),0,1)
        if scene.style == "shaded_edges":
            color[bary.min(-1)<0.007] *= 0.7
        tile = image[ymin:ymax+1,xmin:xmax+1]
        alpha = appearance.opacity
        old_alpha = tile[:,:,3]
        out_alpha = alpha+old_alpha*(1-alpha)
        composed = (color*alpha+tile[:,:,:3]*old_alpha[...,None]*(1-alpha))/np.maximum(out_alpha[...,None],1e-12)
        tile[:,:,:3][mask] = composed[mask]
        tile[:,:,3][mask] = out_alpha[mask]
        region[mask] = zz[mask]
    cancelled(cancel)
    return Image.fromarray(np.rint(np.clip(image,0,1)*255).astype(np.uint8)), {
        "renderer":"cpu-raster", "algorithm":"triangle_zbuffer_direct_blinn_phong", "photorealistic":False,
        "dimensions":[width,height], "source_triangles":source_triangles, "display_triangles":len(primitives),
        "covered_pixels":int(np.isfinite(depth).sum()), "alpha":"straight", "texture_mapping":"world_mm_before_presentation_translation",
        "limitations":["No shadows, reflections, global illumination, HDR or physical transmission", "Intersecting transparent surfaces use sorted alpha approximation", "Clipped sections are uncapped surfaces"]}


def native_render(store, project, scene, path, cancel):
    """Invoke the shipped native ray backend, refusing scene features it cannot lower."""
    from ..native_document import opened_native
    if scene.transforms or scene.sections or scene.hidden or scene.isolated or any(a.texture for a in scene.appearances.values()):
        raise ValueError("Native CPU raytrace currently accepts opaque unsectioned assembled bodies without host texture bindings; select CPU raster for this scene")
    if scene.style != "shaded" or scene.transparent:
        raise ValueError("Native CPU raytrace requires shaded style and opaque background")
    if any(a.opacity != 1 for a in scene.appearances.values()):
        raise ValueError("Physical transmission is not implemented by scene opacity; use CPU raster alpha explicitly")
    with opened_native(project,store) as engine:
        capabilities = engine.execute("render.backends")
        native_scene = engine.execute("oxygen.scene", {"mesh":False})
        for body in native_scene["bodies"]:
            look = scene.appearances.get(body["id"],scene.appearances.get("*"))
            if look:
                engine.execute("appearance.assign", {"bodies":[body["name"]], "color":look.color,
                    "pbr":{"metallic":look.metallic,"roughness":look.roughness,"dielectric_ior":1.5}})
        if any(":face:" in key for key in scene.appearances):
            raise ValueError("Native raytrace per-face host appearance bindings require explicit native face mapping")
        options = {"lights":[],"punctual_lights":[],"environment":{"color":[1,1,1],"intensity":scene.ambient}}
        for light in scene.lights:
            if light.kind=="directional":
                vector=np.array(light.vector,dtype=float)
                rotation=scene.environment_rotation
                vector=vector@np.array([[math.cos(rotation),math.sin(rotation),0],[-math.sin(rotation),math.cos(rotation),0],[0,0,1]])
                options["lights"].append({"direction":list(vector),"color":list(rgb(light.color)),"intensity":light.intensity})
            else:
                item = {"position_mm":light.vector,"color":list(rgb(light.color)),"intensity":light.intensity,
                    "reference_distance_mm":light.reference_distance_mm,"minimum_distance_mm":0.1}
                if light.kind=="spot":
                    item["cone"] = {"emitted_axis":[b-a for a,b in zip(light.vector,light.target)],"inner_half_angle_radians":math.radians(light.cone_degrees*0.4),"outer_half_angle_radians":math.radians(light.cone_degrees/2)}
                options["punctual_lights"].append(item)
        settings = {"camera":scene.camera.model_dump()|{"up":"z"},"width":scene.width,"height":scene.height,
            "background":list(ImageColor.getrgb(scene.background)),"exposure_ev":scene.exposure_ev,
            "job":{"method":"cpu_raytrace","options":options}}
        preflight = engine.execute("render.preflight", {"settings":settings})
        started = engine.execute("render.start", {"settings":settings})
        job = started["job"]
        end = time.monotonic()+180
        while True:
            if cancel is not None and cancel.is_set():
                engine.execute("render.cancel", {"job":job})
                cancelled(cancel)
            status = engine.execute("render.status", {"job":job})
            phase = status["progress"]["phase"]
            if phase == "completed":
                break
            if phase in ("failed","cancelled"):
                raise ValueError(f"Native rendering {phase}: {status.get('error')}")
            if time.monotonic()>end:
                engine.execute("render.cancel", {"job":job})
                raise ValueError("Native render exceeded 180 seconds")
            if cancel is not None:
                cancel.wait(0.03)
            else:
                time.sleep(0.03)
        exported = engine.execute("render.export", {"job":job,"path":str(path.resolve())})
    return {"renderer":"native-cpu-raytrace","photorealistic":False,"capabilities":capabilities,
        "preflight":preflight,"native_export":exported,"dimensions":[scene.width,scene.height],
        "limitations":["Native direct-light CPU raytrace preview; no general global illumination or photorealistic acceptance established"]}
