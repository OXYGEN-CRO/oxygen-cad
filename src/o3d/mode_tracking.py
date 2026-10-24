"""Physical-volume eigenvector correlation across independent C3D10 meshes.

No node IDs are shared across meshes. Positive volume quadrature and full
quadratic inversion transfer fields; outside points are never extrapolated.
MAC handles sign/scale/permutation; principal angles handle repeated eigenspaces.
"""
from __future__ import annotations

import hashlib
import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix

from .quadratic import EDGES, GRADIENTS

MIN_CORRELATION = .98
CLUSTER_GAP = .01
QUADRATURE_TOLERANCE = .005
MAX_SAMPLE_POINTS = 1_000_000


def computed_mode_count(requested):
    # Guard modes detect a repeated family cut by the requested spectral window.
    return requested + 4


def basis(reference):
    bary = np.column_stack((1-reference.sum(axis=1), reference))
    values = np.column_stack((bary*(2*bary-1),
                              *[4*bary[:, a]*bary[:, b] for a, b in EDGES]))
    gradients = np.concatenate(((4*bary[:, :, None]-1)*GRADIENTS,
        np.stack([4*(bary[:, a, None]*GRADIENTS[b]+bary[:, b, None]*GRADIENTS[a])
                  for a, b in EDGES], axis=1)), axis=1)
    return values, gradients


def geometry(mesh):
    ids = list(map(int, mesh["nodes"]))
    lookup = {n: i for i, n in enumerate(ids)}
    nodes = np.asarray(list(mesh["nodes"].values()), dtype=float)
    connectivity = np.asarray([[lookup[int(n)] for n in e] for e in mesh["elements"]])
    xyz = nodes[connectivity]
    return ids, nodes, connectivity, xyz


def quadrature(order):
    roots, weights = np.polynomial.legendre.leggauss(order)
    roots, weights = (roots+1)/2, weights/2
    points, volumes = [], []
    for i, u in enumerate(roots):
        for j, v in enumerate(roots):
            for k, w in enumerate(roots):
                points.append((u, (1-u)*v, (1-u)*(1-v)*w))
                volumes.append(weights[i]*weights[j]*weights[k]*(1-u)**2*(1-v))
    return np.asarray(points), np.asarray(volumes)


class Sampler:
    """Reusable bounding-volume hierarchy and batched quadratic inversion."""
    def __init__(self, mesh):
        self.ids, self.nodes, self.connectivity, self.xyz = geometry(mesh)
        self.origins = self.xyz[:, 0]
        self.local = self.xyz-self.origins[:, None]
        controls = np.concatenate((self.xyz[:, :4], np.stack([
            2*self.xyz[:, i+4]-.5*(self.xyz[:, a]+self.xyz[:, b])
            for i, (a, b) in enumerate(EDGES)], axis=1)), axis=1)
        extent = np.linalg.norm(np.ptp(controls, axis=1), axis=1)
        self.tolerance = np.maximum.reduce((extent*1e-9,
            np.max(np.abs(self.xyz), axis=(1, 2))*np.finfo(float).eps*32,
            np.full(len(extent), 1e-12)))
        self.lower = controls.min(axis=1)-self.tolerance[:, None]
        self.upper = controls.max(axis=1)+self.tolerance[:, None]
        centers = (self.lower+self.upper)/2

        def tree(indices):
            bounds = (self.lower[indices].min(axis=0), self.upper[indices].max(axis=0))
            if len(indices) <= 12:
                return bounds, indices, None
            axis = int(np.argmax(np.ptp(centers[indices], axis=0)))
            indices = indices[np.argsort(centers[indices, axis], kind="stable")]
            half = len(indices)//2
            return bounds, None, (tree(indices[:half]), tree(indices[half:]))
        self.tree = tree(np.arange(len(self.xyz)))

    def interpolation(self, points, cancel=None):
        count = len(points)
        found = np.zeros(count, dtype=bool)
        columns = np.zeros((count, 10), dtype=int)
        interpolation = np.zeros((count, 10))

        def visit(node, point_ids):
            if cancel and cancel.is_set():
                from .solver import Cancelled
                raise Cancelled("Job cancelled")
            (lower, upper), elements, children = node
            point_ids = point_ids[~found[point_ids]]
            inside = np.all((points[point_ids] >= lower) & (points[point_ids] <= upper), axis=1)
            point_ids = point_ids[inside]
            if not len(point_ids):
                return
            if children:
                for child in children:
                    visit(child, point_ids)
                return
            # Bound temporary candidate arrays even for very large meshes.
            for start in range(0, len(point_ids), 2048):
                selected = point_ids[start:start+2048]
                p, e = np.where(np.all((points[selected, None] >= self.lower[elements]) &
                                      (points[selected, None] <= self.upper[elements]), axis=2))
                p, e = selected[p], elements[e]
                if not len(p):
                    continue
                local = self.local[e]
                target = points[p]-self.origins[e]
                reference = np.linalg.solve(np.swapaxes(local[:, 1:4], 1, 2), target[..., None])[..., 0]
                for _ in range(20):
                    values, gradients = basis(reference)
                    residual = np.einsum("pn,pni->pi", values, local)-target
                    norm = np.linalg.norm(residual, axis=1)
                    if np.all(norm <= self.tolerance[e]):
                        break
                    jacobian = np.einsum("pni,pnj->pij", local, gradients)
                    delta = np.linalg.solve(jacobian, residual[..., None])[..., 0]
                    # Damped Newton, per candidate; no change to physical points.
                    moving = norm > self.tolerance[e]
                    for fraction in (1., .5, .25, .125, .0625, .03125):
                        trial = reference-fraction*delta
                        trial_values, _ = basis(trial)
                        better = moving & (np.linalg.norm(np.einsum("pn,pni->pi", trial_values, local)-target, axis=1) < norm)
                        reference[better] = trial[better]
                        moving[better] = False
                        if not moving.any():
                            break
                values, _ = basis(reference)
                residual = np.linalg.norm(np.einsum("pn,pni->pi", values, local)-target, axis=1)
                bary = np.column_stack((1-reference.sum(axis=1), reference))
                accepted = np.flatnonzero((residual <= self.tolerance[e]) &
                    (bary.min(axis=1) >= -1e-7) & (bary.max(axis=1) <= 1+1e-7))
                # Shared-face candidates are equivalent for conforming fields.
                _, unique = np.unique(p[accepted], return_index=True)
                accepted = accepted[unique]
                columns[p[accepted]] = self.connectivity[e[accepted]]
                interpolation[p[accepted]] = values[accepted]
                found[p[accepted]] = True

        visit(self.tree, np.arange(count))
        if not found.all():
            raise ValueError(f"Mode correlation cannot locate {np.count_nonzero(~found)}/{count} physical quadrature points in the new mesh; extrapolation is prohibited")
        return csr_matrix((interpolation.ravel(), (np.repeat(np.arange(count), 10), columns.ravel())),
                          shape=(count, len(self.nodes)))


def nodal_modes(mesh, modes):
    ids = list(map(int, mesh["nodes"]))
    fields = [{int(n): v for n, v in mode.items()} for mode in modes]
    if any(set(f) != set(ids) for f in fields):
        raise ValueError("Incomplete eigenvector field for mode correlation")
    result = np.asarray([[field[n] for field in fields] for n in ids], dtype=float)
    if result.shape != (len(ids), len(modes), 3) or not np.isfinite(result).all():
        raise ValueError("Eigenvectors must have three finite world-frame components")
    # Avoid overflow and make rank tests invariant to arbitrary solver scaling.
    scale = np.max(np.abs(result), axis=(0, 2))
    if np.any(scale <= 0):
        raise ValueError("Zero eigenvector cannot establish mode correlation")
    return result/scale[None, :, None]


def correlate_samples(a, b, weights, old_values, new_values, requested):
    """Positive-weight L2 MAC and repeated-family principal-angle diagnostics."""
    if a.shape != b.shape or a.shape[1] < requested+1:
        raise ValueError("Mode correlation needs complete fields and guard modes")
    weights = np.asarray(weights, dtype=float)
    if weights.shape != (len(a),) or not np.isfinite(weights).all() or np.any(weights <= 0):
        raise ValueError("Mode inner product requires positive finite volume weights")
    if (len(old_values) != a.shape[1] or len(new_values) != b.shape[1] or
            not np.isfinite(old_values).all() or not np.isfinite(new_values).all()):
        raise ValueError("Mode correlation requires one finite eigenvalue per field")
    weighted = np.sqrt(weights[:, None, None])
    a = (a*weighted).transpose(0, 2, 1).reshape(-1, a.shape[1])
    b = (b*weighted).transpose(0, 2, 1).reshape(-1, b.shape[1])
    a /= np.linalg.norm(a, axis=0)
    b /= np.linalg.norm(b, axis=0)
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Zero or nonfinite sampled eigenvector")
    mac = np.clip((a.T @ b)**2, 0., 1.)
    _, matched = linear_sum_assignment(mac, maximize=True)
    old_values, new_values = np.asarray(old_values), np.asarray(new_values)
    parent = list(range(len(matched)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i in range(len(matched)):
        for j in range(i):
            close = lambda x, y: abs(x-y)/max(abs(x), abs(y), 1e-30) <= CLUSTER_GAP
            if close(old_values[i], old_values[j]) or close(new_values[matched[i]], new_values[matched[j]]):
                parent[root(i)] = root(j)
    families = {}
    for i in range(len(matched)):
        families.setdefault(root(i), []).append(i)
    groups, changes, passed = [], np.zeros(requested), True
    for indices in families.values():
        current = matched[indices]
        if not any(i < requested for i in indices) and not np.any(current < requested):
            continue
        previous_matrix, current_matrix = a[:, indices], b[:, current]
        # Rank deficiency must not be hidden by orth() dropping duplicate modes.
        u, s, _ = np.linalg.svd(previous_matrix, full_matrices=False)
        v, t, _ = np.linalg.svd(current_matrix, full_matrices=False)
        full_rank = bool(min(s[-1]/s[0], t[-1]/t[0]) > 1e-8)
        principal = np.linalg.svd(u.T @ v, compute_uv=False)
        score = float(np.clip(principal[-1]**2, 0, 1)) if full_rank else 0.
        boundary = (any(i >= requested for i in indices) or np.any(current >= requested))
        # Sorted spectra within a close family are independent of arbitrary basis.
        old, new = np.sort(old_values[indices]), np.sort(new_values[current])
        delta = np.abs(old-new)/np.maximum(np.maximum(np.abs(old), np.abs(new)), 1e-30)
        for i in indices:
            if i < requested:
                changes[i] = float(max(delta))
        valid = full_rank and not boundary and score >= MIN_CORRELATION
        passed &= valid
        groups.append({"previous_modes": [int(i+1) for i in indices],
                       "current_modes": [int(i+1) for i in current],
                       "minimum_squared_cosine": score, "full_rank": full_rank,
                       "requested_window_cut": bool(boundary),
                       "quantity_changes": delta.tolist(), "passed": bool(valid)})
    return {"mac_matrix": mac.tolist(), "matching": (matched+1).tolist(),
            "groups": groups, "quantity_changes": changes.tolist(), "passed": bool(passed)}


def track_modes(previous_mesh, previous_modes, previous_values, mesh, modes, values, requested, cancel=None):
    """Correlate over every coarse cell; two positive quadrature orders must agree.

    Uniform material density cancels from the mass inner product in this adapter.
    This is numerical sampled-volume correlation, not physical modal validation.
    """
    from .mesh_quality_worker import mesh_fingerprint
    _, _, connectivity, xyz = geometry(previous_mesh)
    if len(xyz)*(4**3+5**3) > MAX_SAMPLE_POINTS:
        raise ValueError(f"Mode correlation exceeds the {MAX_SAMPLE_POINTS} point resource limit; use a coarser initial mesh")
    old = nodal_modes(previous_mesh, previous_modes)
    new = nodal_modes(mesh, modes)
    sampler = Sampler(mesh)
    audits = []
    for order in (4, 5):
        reference, weights = quadrature(order)
        shape, gradients = basis(reference)
        local = xyz-xyz[:, :1]
        points = (xyz[:, :1]+np.einsum("qn,eni->eqi", shape, local)).reshape(-1, 3)
        jacobians = np.einsum("eni,qnj->eqij", local, gradients)
        volumes = (np.linalg.det(jacobians)*weights).ravel()
        if not np.isfinite(volumes).all() or np.any(volumes <= 0):
            raise ValueError("Nonpositive quadrature measure prevents a valid mode inner product")
        old_samples = np.einsum("qn,enmc->eqmc", shape, old[connectivity]).reshape(-1, len(previous_modes), 3)
        new_samples = (sampler.interpolation(points, cancel) @ new.reshape(len(new), -1)).reshape(-1, len(modes), 3)
        audit = correlate_samples(old_samples, new_samples, volumes, previous_values, values, requested)
        audit.update({"quadrature_order": order, "sample_count": len(points), "volume_mm3": float(volumes.sum()),
                      "points_weights_sha256": hashlib.sha256(points.tobytes()+volumes.tobytes()).hexdigest()})
        audits.append(audit)
    low, high = audits
    # Check all MAC entries touching the requested spectral window, so a matching
    # unstable under integration cannot masquerade as agreement of one scalar.
    difference = np.abs(np.asarray(low["mac_matrix"])-np.asarray(high["mac_matrix"]))
    sensitivity = float(max(difference[:requested].max(), difference[:, :requested].max()))
    return {"schema_version": 1, "method": "physical-volume C3D10 MAC and principal-angle subspaces",
            "previous_mesh_sha256": mesh_fingerprint(previous_mesh), "mesh_sha256": mesh_fingerprint(mesh),
            "inner_product": "positive volume quadrature; uniform density cancels",
            "minimum_correlation": MIN_CORRELATION, "relative_cluster_gap": CLUSTER_GAP,
            "quadrature_tolerance": QUADRATURE_TOLERANCE, "quadrature_sensitivity": sensitivity,
            "quadrature_checks": audits, "matching": high["matching"], "groups": high["groups"],
            "quantity_changes": high["quantity_changes"],
            "passed": low["passed"] and high["passed"] and sensitivity <= QUADRATURE_TOLERANCE}
