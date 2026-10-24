"""CAD-to-analysis transfer and backend-independent mesh validation."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .model import geometry_key
from .store import write_json


def prepare_geometry(store, project):
    import build123d as bd
    from .geometry import build, resolve_face, signature, vector
    folder = store.cache / "builds" / geometry_key(project)
    if not (folder / "build.json").exists():
        build(store, project)
    shape = bd.import_brep(folder / "part.brep")
    regions, aliases, identities = [], {}, {}
    groups = [("fixed", project.study.fixed_faces), ("load", [f.face for f in project.study.forces]),
              ("temperature", [v.face for v in project.study.temperatures]),
              ("heat", [v.face for v in project.study.heat_sources])]
    for prefix, names in groups:
        for index, name in enumerate(names):
            face = resolve_face(shape, name)
            if face.geom_type != bd.GeomType.PLANE:
                raise ValueError(f"{name}: this adapter currently requires a planar analysis region")
            identity = signature(face)
            if identity not in identities:
                key = f"region_{len(regions)}"
                identities[identity] = key
                box = face.bounding_box()
                regions.append({"name": key, "selection": name, "signature": identity,
                                "center": vector(face.center()), "area": face.area,
                                "normal": vector(face.normal_at()), "bounds": [vector(box.min), vector(box.max)]})
            aliases[f"{prefix}_{index}"] = identities[identity]
    return {"step": str((folder / "part.step").resolve()), "regions": regions, "aliases": aliases,
            "expected_volume_mm3": shape.volume, "geometry_key": geometry_key(project), "units": "mm"}


def validate_mesh(mesh, expected_volume=None, max_nodes=200000, curved_audit=None):
    nodes = {int(k): np.asarray(v, dtype=float) for k, v in mesh["nodes"].items()}
    if not nodes or len(nodes) > max_nodes:
        raise ValueError(f"Mesh must have 1–{max_nodes} nodes")
    if any(p.shape != (3,) or not np.isfinite(p).all() for p in nodes.values()):
        raise ValueError("Mesh coordinates must be finite 3D positions")
    if not mesh["elements"]:
        raise ValueError("Mesher produced no volume elements")
    used, parent, volumes, signs = set(), {}, [], set()
    def find(n):
        parent.setdefault(n, n)
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    minimum_quality = 1.0
    for element in mesh["elements"]:
        if len(element) != 10 or len(set(element)) != 10 or any(n not in nodes for n in element):
            raise ValueError("Mesh must contain valid, unique-node C3D10 tetrahedra")
        points = np.array([nodes[n] for n in element[:4]])
        determinant = float(np.linalg.det((points[1:] - points[0]).T))
        lengths = [np.linalg.norm(points[i] - points[j]) for i in range(4) for j in range(i)]
        if abs(determinant) <= 1e-12 or not max(lengths):
            raise ValueError("Degenerate volume element")
        signs.add(determinant > 0)
        volumes.append(abs(determinant) / 6)
        # Six-volume / longest-edge^3; a deliberately conservative degeneracy check.
        minimum_quality = min(minimum_quality, abs(determinant) / max(lengths)**3)
        used.update(element)
        root = find(element[0])
        for n in element[1:]:
            parent[find(n)] = root
    if len(signs) != 1:
        raise ValueError("Inconsistent tetrahedral orientations")
    if used != set(nodes):
        raise ValueError("Mesh contains unconnected or unused nodes")
    if len({find(n) for n in used}) != 1:
        raise ValueError("Disconnected volume mesh is unsupported by the single-body adapter")
    if minimum_quality < 1e-7:
        raise ValueError("Mesh contains near-degenerate tetrahedra")
    from .quadratic import boundary_faces
    exterior, edges = boundary_faces(mesh["elements"],require_connected=True)
    for name, triangles in mesh["surfaces"].items():
        if not triangles or any(len(t) != 6 or len(set(t))!=6 or any(n not in nodes for n in t) for t in triangles):
            raise ValueError(f"Invalid quadratic surface mesh for {name}")
        if len({tuple(sorted(t[:3])) for t in triangles})!=len(triangles):
            raise ValueError(f"Duplicated boundary triangle in {name}")
        for triangle in triangles:
            if tuple(sorted(triangle[:3])) not in exterior:
                raise ValueError(f"Region {name} includes a non-exterior volume face")
            if any(edges[tuple(sorted((triangle[a],triangle[b])))]!=mid for (a,b),mid in zip(((0,1),(1,2),(2,0)),triangle[3:])):
                raise ValueError(f"Region {name} has an invalid quadratic face-node ordering")
    volume = sum(volumes)
    error = abs(volume - expected_volume) / expected_volume if expected_volume else None
    # Corner-only volume underestimates curved high-order cells; this is a coarse
    # connectivity/scale audit, not an exact curved-Jacobian certificate.
    if curved_audit is None and error is not None and error > .1:
        raise ValueError("Mesh corner volume differs from CAD by more than 10%; check geometry transfer/units")
    result = {"nodes": len(nodes), "elements": len(volumes), "connected_components": 1,
            "corner_volume_mm3": volume, "corner_volume_relative_error": error,
            "minimum_corner_quality": minimum_quality, "element_type": "C3D10",
            "curved_jacobian_checked": False,"curved_jacobian_certified": False}
    if curved_audit is not None:
        from .mesh_quality_worker import mesh_fingerprint
        if curved_audit.get("mesh_sha256")!=mesh_fingerprint(mesh) or curved_audit.get("element_type")!="C3D10":
            raise ValueError("Curved-element audit does not match the exact solver mesh")
        if curved_audit.get("element_ids")!=list(range(1,len(volumes)+1)):
            raise ValueError("Curved-element audit has incomplete element identities")
        lower=np.asarray(curved_audit.get("jacobian_lower_mm3",[]));upper=np.asarray(curved_audit.get("jacobian_upper_mm3",[]))
        integrated=np.asarray(curved_audit.get("integrated_volume_mm3",[]))
        if any(v.shape!=(len(volumes),) or not np.isfinite(v).all() for v in (lower,upper,integrated)):
            raise ValueError("Incomplete or nonfinite curved-element bounds")
        if np.any(lower>upper) or np.any(integrated<=0):raise ValueError("Invalid curved-element bounds or volume")
        invalid=np.flatnonzero((lower<=0)|(upper<=0)|(lower<=1e-6*upper))
        if len(invalid):
            raise ValueError(f"Nonpositive or near-degenerate curved Jacobian in {len(invalid)} elements; solver IDs {(invalid[:10]+1).tolist()}")
        curved_volume=float(integrated.sum())
        curved_error=abs(curved_volume-expected_volume)/expected_volume if expected_volume else None
        if curved_error is not None and curved_error>.1:
            raise ValueError("Integrated curved mesh volume differs from CAD by more than 10%; check geometry transfer/units")
        result.update({"curved_jacobian_checked":True,"curved_jacobian_method":curved_audit["method"],
                       "quality_backend":curved_audit["backend"],"minimum_jacobian_lower_mm3":float(lower.min()),
                       "minimum_jacobian_lower_to_upper":float((lower/upper).min()),
                       "integrated_volume_mm3":curved_volume,"integrated_volume_relative_error":curved_error,
                       "max_element_volume_mm3":float(integrated.max()),"mesh_sha256":curved_audit["mesh_sha256"],
                       "curved_jacobian_scope":curved_audit["scope"]})
    return result


def generate_mesh(run, transfer, project, cancel=None):
    from .solver import mesher_python, run_process
    request = transfer | {"mesh_size": project.study.mesh_size_mm, "max_nodes": project.study.max_nodes}
    write_json(run / "mesh-request.json", request)
    script = "gmsh_worker.py" if project.study.mesher == "gmsh" else "mesh_worker.py"
    run_process([mesher_python(), Path(__file__).with_name(script), run / "mesh-request.json", run / "mesh.json"],
                run, run / "mesher.log", cancel)
    mesh = json.loads((run / "mesh.json").read_text())
    for name, canonical in transfer["aliases"].items():
        mesh["surfaces"][name] = mesh["surfaces"][canonical]
    # Reject malformed connectivity before passing it to the native quality
    # library. Then check the exact solver-order mesh in a separate process.
    validate_mesh(mesh,max_nodes=project.study.max_nodes)
    run_process([mesher_python(),Path(__file__).with_name("mesh_quality_worker.py"),run/"mesh.json",run/"curved-quality.json"],
                run,run/"quality.log",cancel,project.study.solver_timeout_seconds)
    audit=json.loads((run/"curved-quality.json").read_text())
    quality = validate_mesh(mesh, transfer["expected_volume_mm3"], project.study.max_nodes,audit)
    from .quadratic import surface_integrals
    regions=[]
    for region in transfer["regions"]:
        triangles=mesh["surfaces"][region["name"]]
        ids={n for triangle in triangles for n in triangle}
        points=np.array([mesh["nodes"][str(n)] if str(n) in mesh["nodes"] else mesh["nodes"][n] for n in ids])
        center=np.asarray(region["center"]);normal=np.asarray(region["normal"])
        plane_error=float(np.max(np.abs((points-center) @ normal)))
        if plane_error>max(1e-5,np.sqrt(region["area"])*1e-7):
            raise ValueError(f"Selected region {region['selection']} is not on its declared CAD plane")
        _,area,moment=surface_integrals(mesh,triangles)
        area_error=abs(area-region["area"])/region["area"]
        center_error=float(np.linalg.norm(moment/area-center))
        if area_error>.02 or center_error>max(1e-4,np.sqrt(region["area"])*.01):
            raise ValueError(f"Selected region {region['selection']} area or centroid does not match CAD within the transfer envelope")
        regions.append({"name":region["name"],"selection":region["selection"],"integrated_area_mm2":area,
                        "area_relative_error":area_error,"centroid_error_mm":center_error,"plane_error_mm":plane_error})
    quality["regions"]=regions
    write_json(run / "mesh-quality.json", quality)
    return mesh, quality


def exterior_triangles(elements):
    from .quadratic import boundary_faces
    faces,_=boundary_faces(elements)
    return [list(tri) for a,b,c,ab,bc,ca in faces.values() for tri in ((a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca))]
