"""Localized mesh-based molding advice with user-sourced material thresholds.

Normal-ray thickness and two-sided pull accessibility are geometric advice only;
this does not simulate mold filling, cooling, shrinkage or an actual parting mold.
"""
from __future__ import annotations

import math
from .util import cancelled


def analyze(mesh_path, rules, cancel=None):
    import numpy as np
    import trimesh
    mesh = trimesh.load(mesh_path, force="mesh", process=True)
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError("Plastics advice requires a closed, consistently oriented mesh")
    if len(mesh.faces) > 100000:
        raise ValueError("Plastics advice mesh exceeds 100,000 triangles")
    triangles = mesh.triangles
    edge1, edge2 = triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]
    pull = np.asarray(rules.pull_direction, dtype=float)
    pull /= np.linalg.norm(pull)
    findings = []
    samples = []
    count = min(len(triangles), 512)
    indices = np.unique(np.linspace(0, len(triangles)-1, count, dtype=int))

    def nearest(origin, direction, ignored):
        h = np.cross(np.broadcast_to(direction, edge2.shape), edge2)
        det = np.einsum("ij,ij->i", edge1, h)
        valid = np.abs(det) > 1e-10
        inverse = np.divide(1, det, out=np.zeros_like(det), where=valid)
        delta = origin-triangles[:, 0]
        u = inverse*np.einsum("ij,ij->i", delta, h)
        q = np.cross(delta, edge1)
        v = inverse*(q@direction)
        distance = inverse*np.einsum("ij,ij->i", edge2, q)
        valid &= (u >= -1e-8) & (v >= -1e-8) & (u+v <= 1+1e-8) & (distance > 1e-6)
        valid[ignored] = False
        return float(distance[valid].min()) if valid.any() else None

    for index in indices:
        if cancelled(cancel):
            raise RuntimeError("Plastics analysis cancelled")
        center = mesh.triangles_center[index]
        normal = mesh.face_normals[index]
        thickness = nearest(center-normal*1e-5, -normal, index)
        plus = nearest(center+normal*1e-5, pull, index)
        minus = nearest(center+normal*1e-5, -pull, index)
        draft = math.degrees(math.asin(float(np.clip(abs(normal@pull), 0, 1))))
        sample = {"face_index": int(index), "center_mm": center.tolist(), "normal": normal.tolist(),
                  "normal_ray_thickness_mm": thickness, "draft_deg": draft,
                  "blocked_positive_pull": plus is not None, "blocked_negative_pull": minus is not None}
        samples.append(sample)
        def finding(code, value, threshold):
            findings.append({"code": code, "face_index": int(index), "location_mm": center.tolist(),
                             "value": value, "threshold": threshold, "rule_source": rules.source, "severity": "warning"})
        if thickness is not None:
            if thickness < rules.min_wall_mm:
                finding("thin_normal_section", thickness, rules.min_wall_mm)
            if thickness > rules.max_wall_mm:
                finding("thick_normal_section", thickness, rules.max_wall_mm)
        if draft < rules.min_draft_deg:
            finding("insufficient_draft", draft, rules.min_draft_deg)
        if plus is not None and minus is not None:
            finding("blocked_both_pull_directions", min(plus, minus), 0)
    return {"engine": "trimesh + vectorized Moller-Trumbore normal/pull rays", "material": rules.material,
            "rule_source": rules.source, "pull_direction": pull.tolist(), "sampled_faces": len(samples),
            "total_faces": len(mesh.faces), "complete_face_sampling": len(samples) == len(mesh.faces),
            "findings": findings, "samples": samples,
            "limits": ["Normal-ray section lengths do not establish medial-axis wall thickness", "Draft uses absolute face normal versus two-sided pull", "No parting surface, slider design, knife-edge test or mold-fill simulation"]}
