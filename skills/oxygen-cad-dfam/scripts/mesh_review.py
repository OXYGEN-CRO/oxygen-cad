"""Measure STL/OBJ/PLY geometry; all lengths require explicitly confirmed mm."""
import argparse
import json
from pathlib import Path
import sys


def distances(triangles, origin, direction, epsilon=1e-7):
    import numpy as np
    e1, e2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    h = np.cross(direction, e2); det = np.einsum('ij,ij->i', e1, h)
    inv = np.divide(1.0, det, out=np.zeros_like(det), where=np.abs(det) > 1e-12)
    s = origin - triangles[:, 0]; u = inv * np.einsum('ij,ij->i', s, h)
    q = np.cross(s, e1); v = inv * (q @ direction); t = inv * np.einsum('ij,ij->i', e2, q)
    return t[(np.abs(det) > 1e-12) & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > epsilon)]


def measure(path, samples=128, overhang=45):
    import numpy as np
    import trimesh
    mesh = trimesh.load(path, force='mesh', process=False)
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise ValueError('Expected a nonempty triangle mesh')
    if len(mesh.faces) > 200000:
        raise ValueError('More than 200000 faces; use a documented lower-resolution mesh for sampling')
    if not np.isfinite(mesh.vertices).all():
        raise ValueError('Nonfinite mesh vertices')
    # STL repeats vertex coordinates per facet; weld identical positions for topology only.
    mesh.merge_vertices()
    bounds, areas, normals = mesh.bounds, mesh.area_faces, mesh.face_normals
    span = float(np.linalg.norm(mesh.extents)); epsilon = max(span * 1e-7, 1e-7)
    indices = np.random.default_rng(0).choice(len(mesh.faces), size=min(samples, len(mesh.faces)), replace=False,
                                            p=areas / areas.sum())
    thickness = []; misses = 0
    if mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0:
        for i in indices:
            origin = mesh.triangles_center[i] - normals[i] * epsilon
            hits = distances(mesh.triangles, origin, -normals[i], epsilon)
            if len(hits): thickness.append(float(hits.min() + epsilon))
            else: misses += 1
    else:
        misses = len(indices)
    orientations = []
    for label, up in [('z+', [0,0,1]), ('z-', [0,0,-1]), ('x+', [1,0,0]), ('x-', [-1,0,0]), ('y+', [0,1,0]), ('y-', [0,-1,0])]:
        axis = np.array(up); heights = mesh.triangles @ axis
        lowest = float((mesh.vertices @ axis).min())
        suspended = heights.mean(axis=1) > lowest + epsilon
        down = (normals @ axis) < -np.cos(np.radians(overhang))
        orientations.append({'up': label, 'downward_area_over_limit_mm2': float(areas[down & suspended].sum()),
                             'height_mm': float(np.ptp(mesh.vertices @ axis))})
    limitations = ['Sampled inward-normal chords are not a complete minimum-wall measurement.',
                   'Support volume, trapped powder, shrinkage, strength and machine fit are not measured.']
    return {'file': str(Path(path).resolve()), 'units': 'mm', 'watertight': bool(mesh.is_watertight),
            'consistent_winding': bool(mesh.is_winding_consistent), 'bounds_mm': bounds.tolist(),
            'dimensions_mm': mesh.extents.tolist(), 'volume_mm3': float(mesh.volume) if mesh.is_volume else None,
            'face_count': len(mesh.faces), 'overhang_limit_degrees_from_vertical': overhang,
            'normal_chord_mm': {'samples': len(thickness), 'missed': misses, 'min': min(thickness) if thickness else None,
                                'p05': float(np.quantile(thickness, .05)) if thickness else None},
            'orientations': sorted(orientations, key=lambda x: x['downward_area_over_limit_mm2']),
            'unmeasured': ['minimum_wall_everywhere', 'support_volume', 'powder_escape', 'physical_strength'],
            'limitations': limitations}


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('mesh', type=Path)
    p.add_argument('--units', required=True, choices=['mm']); p.add_argument('--samples', type=int, default=128)
    p.add_argument('--overhang-deg', type=float, required=True)
    args = p.parse_args()
    try:
        if not 1 <= args.samples <= 512 or not 0 < args.overhang_deg < 90:
            raise ValueError('samples must be 1..512 and overhang-deg must be between 0 and 90')
        print(json.dumps(measure(args.mesh, args.samples, args.overhang_deg), indent=2, allow_nan=False))
    except (ImportError, OSError, ValueError) as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr); return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
