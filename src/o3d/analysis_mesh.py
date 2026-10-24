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


def validate_mesh(mesh, expected_volume=None, max_nodes=200000):
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
    for name, triangles in mesh["surfaces"].items():
        if not triangles or any(len(t) != 6 or any(n not in nodes for n in t) for t in triangles):
            raise ValueError(f"Invalid quadratic surface mesh for {name}")
    volume = sum(volumes)
    error = abs(volume - expected_volume) / expected_volume if expected_volume else None
    # Corner-only volume underestimates curved high-order cells; this is a coarse
    # connectivity/scale audit, not an exact curved-Jacobian certificate.
    if error is not None and error > .1:
        raise ValueError("Mesh corner volume differs from CAD by more than 10%; check geometry transfer/units")
    return {"nodes": len(nodes), "elements": len(volumes), "connected_components": 1,
            "corner_volume_mm3": volume, "corner_volume_relative_error": error,
            "minimum_corner_quality": minimum_quality, "element_type": "C3D10",
            "curved_jacobian_certified": False}


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
    quality = validate_mesh(mesh, transfer["expected_volume_mm3"], project.study.max_nodes)
    write_json(run / "mesh-quality.json", quality)
    return mesh, quality


def exterior_triangles(elements):
    counts = {}
    for element in elements:
        for indices in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
            triangle = tuple(element[i] for i in indices)
            key = tuple(sorted(triangle))
            counts[key] = (counts.get(key, (0, triangle))[0] + 1, triangle)
    return [list(tri) for count, tri in counts.values() if count == 1]
