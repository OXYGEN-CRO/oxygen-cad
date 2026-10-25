"""Full deformable, frozen-map contact operators with declared bulk tangents.

The bulk uses CalculiX's four C3D10 points, not the higher-order eigen adapter.
An optional native provider supplies state-bound material/geometric tangents.
Contact retains every signed quadratic trace coefficient and nonsymmetric term.
Condition estimates and a symmetric-part witness are diagnostics, not a claim
about changing maps, nonlinear bulk, dynamic stability or measured hardware.
"""
from __future__ import annotations
import hashlib
import numpy as np
from scipy import sparse, linalg
from scipy.sparse import linalg as sla
from .quadratic import weights_and_gradients
from .store import write_json

MAX_DOFS = 12_000
MAX_ENTRIES = 8_000_000
MAX_DENSE_SPECTRUM = 2048


def native_bulk(system):
    """Linear isotropic C3D10 stiffness at the exact native four-point rule."""
    alpha = .5854101966249685
    beta = .1381966011250105
    points = np.array([[alpha if j == i else beta for j in range(4)] for i in range(4)])[:, 1:]
    gradients = np.array([weights_and_gradients(p)[1] for p in points])
    rows, columns, entries = [], [], []
    for start in range(0, len(system.mesh.elements), 64):
        cells = system.mesh.elements[start:start + 64]
        indices = np.array([[system.index[n] for n in e.nodes] for e in cells])
        xyz = system.xyz[indices]
        jac = np.einsum('eni,qnj->eqij', xyz - xyz[:, :1], gradients)
        weights = np.linalg.det(jac) / 24
        if not np.isfinite(weights).all() or np.any(weights <= 0):
            raise ValueError('Global contact bulk has invalid native quadrature Jacobians')
        physical = np.einsum('qni,eqij->eqnj', gradients, np.linalg.inv(jac))
        b = np.zeros((len(cells), 4, 6, 10, 3))
        for axis in range(3):
            b[:, :, axis, :, axis] = physical[:, :, :, axis]
        for row, (a, d) in enumerate([(0, 1), (1, 2), (2, 0)], 3):
            b[:, :, row, :, a] = physical[:, :, :, d] / np.sqrt(2)
            b[:, :, row, :, d] = physical[:, :, :, a] / np.sqrt(2)
        b = b.reshape(len(cells), 4, 6, 30)
        materials = [system.assignments[e.body_id] for e in cells]
        young = np.array([m.youngs_modulus_mpa for m in materials])
        nu = np.array([m.poisson_ratio for m in materials])
        mu = young / (2 * (1 + nu))
        lam = young * nu / ((1 + nu) * (1 - 2 * nu))
        constitutive = np.zeros((len(cells), 6, 6))
        constitutive[:, :3, :3] = lam[:, None, None]
        constitutive[:, np.arange(6), np.arange(6)] += 2 * mu[:, None]
        k = np.einsum('eqai,eab,eqbj,eq->eij', b, constitutive, b, weights)
        dofs = (indices[:, :, None] * 3 + np.arange(3)).reshape(len(cells), 30)
        rows.append(np.repeat(dofs, 30, axis=1).ravel())
        columns.append(np.tile(dofs, (1, 30)).ravel())
        entries.append(k.ravel())
    size = 3 * len(system.ids)
    matrix = sparse.coo_matrix((np.concatenate(entries), (np.concatenate(rows), np.concatenate(columns))), shape=(size, size)).tocsr()
    matrix.eliminate_zeros()
    if not np.isfinite(matrix.data).all() or np.any(matrix.diagonal() <= 0):
        raise ValueError('Invalid native global contact bulk stiffness')
    if sla.norm(matrix - matrix.T) > sla.norm(matrix) * 1e-12:
        raise ValueError('Asymmetric native linear bulk stiffness')
    return matrix


def matrix_hash(matrix):
    matrix = matrix.copy().tocsr(); matrix.sort_indices()
    digest = hashlib.sha256()
    for values, dtype in [(matrix.shape, '<i8'), (matrix.indptr, '<i8'), (matrix.indices, '<i8'), (matrix.data, '<f8')]:
        digest.update(np.asarray(values, dtype=dtype).tobytes())
    return digest.hexdigest()


class GlobalContactAudit:
    def __init__(self, system, case):
        self.system = system; self.history = []; self.snapshot = None; self.worst = None
        self.reason = None
        self.provider = getattr(system, '_accepted_bulk', None)
        if self.provider is not None and self.provider.reason:
            self.reason = self.provider.reason
        elif self.provider is None and (system.study.settings.large_deformation or any(m.plastic_curve for m in system.assignments.values())):
            self.reason = 'Accepted nonlinear bulk state is unavailable; a linear substitute is refused'
        elif len(system.ids) * 3 > MAX_DOFS or len(system.mesh.elements) * 900 > MAX_ENTRIES:
            self.reason = 'Full frozen operator exceeds the declared 12000-DOF/8000000 bulk-entry audit budget'
        if self.reason:
            return
        self.bulk = native_bulk(system) if self.provider is None else None
        size = 3*len(system.ids)
        fixed = set()
        for support in case.supports:
            if support.kind not in {'fixed', 'partial'}:
                raise ValueError('Full contact operator requires exact world-axis support components')
            for i in system.region_nodes(support.region_id):
                for axis, active in enumerate((True, True, True) if support.kind == 'fixed' else support.components):
                    if active: fixed.add(3 * i + axis)
        self.fixed = sorted(fixed)
        self.free = np.array([d for d in range(size) if d not in fixed], dtype=int)

    def start(self, meta=None):
        if self.reason: return
        self.meta=meta
        if self.provider is not None:
            value=self.provider.get(meta)
            if value is None:
                self.reason='Native accepted bulk frame is unavailable';return
            self.bulk,self.bulk_force,self.bulk_data=value
            self.contact_force=np.zeros_like(self.bulk_force)
        self.rows = []; self.columns = []; self.values = []; self.traces = []
        self.missing = 0; self.boundaries = 0; self.entries = 0

    def point(self, derivative, trace, *, boundary=False, force=None):
        if self.reason: return
        boundary = bool(boundary)
        self.boundaries += int(boundary)
        if trace is None:
            self.missing += 1
            return
        nodes = trace['secondary_nodes'] + trace['primary_nodes']
        weights = np.r_[trace['secondary_weights'], -np.array(trace['primary_weights'])]
        if len(nodes) != 12 or len(set(nodes)) != 12 or not np.isfinite(weights).all():
            raise ValueError('Incomplete/ambiguous full quadratic contact trace')
        if any(n not in self.system.index for n in nodes):
            raise ValueError('Contact trace references a missing global node')
        if not np.allclose([sum(weights[:6]), sum(weights[6:])], [1, -1], rtol=0, atol=1e-10):
            raise ValueError('Contact trace does not conserve translations')
        jump = np.kron(weights[None, :], np.eye(3))
        dofs = (np.array([self.system.index[n] for n in nodes])[:, None] * 3 + np.arange(3)).ravel()
        if self.provider is not None:
            if force is None:raise ValueError('Accepted bulk/contact force reconstruction requires point forces')
            for node,weight in zip(nodes,weights):self.contact_force[self.system.index[node]]+=weight*force
        local = jump.T @ derivative @ jump
        row, column = np.nonzero(local)
        self.entries += len(row)
        if self.entries > MAX_ENTRIES:
            self.reason = 'Full frozen contact assembly exceeds the declared 8000000-entry audit budget'
            self.rows = []; self.columns = []; self.values = []; self.traces = []
            return
        self.rows.append(dofs[row]); self.columns.append(dofs[column]); self.values.append(local[row, column])
        self.traces.append(trace | {'boundary': boundary})

    def finish(self, time):
        if self.reason: return None
        size = self.bulk.shape[0]
        contact = (sparse.coo_matrix((np.concatenate(self.values), (np.concatenate(self.rows), np.concatenate(self.columns))), shape=(size, size)).tocsr()
                   if self.values else sparse.csr_matrix((size, size)))
        contact.eliminate_zeros()
        full = self.bulk + contact
        reduced = full[self.free][:, self.free].tocsc()
        norm = float(sla.norm(reduced, 1)) if len(self.free) else 0.
        row = {'time_s': time, 'total_dofs': size, 'free_dofs': len(self.free), 'contact_points': len(self.traces),
               'missing_traces': self.missing, 'boundary_points': self.boundaries,
               'complete_frozen_operator': not self.missing and not self.boundaries,
               'operator_sha256': matrix_hash(reduced), 'one_norm_n_mm': norm,
               'asymmetry_frobenius_ratio': float(sla.norm(reduced - reduced.T) / max(float(sla.norm(reduced)), 1e-300)),
               'condition_number_1_estimate': None, 'solve_backward_error': None, 'factorization': 'fully constrained'}
        if self.provider is not None:
            row.update(accepted_bulk_file_sha256=self.bulk_data['file_sha256'],
                native_internal_force_relative_error=self.provider.check_force(self.meta,self.contact_force))
            bound=self.provider.rows[(self.meta['step'],self.meta['increment'])]
            row['bulk_force_jacobian_complete']=bound['bulk_force_jacobian_complete']
            row['complete_frozen_operator'] &= bound['bulk_force_jacobian_complete']
        witness = np.zeros(len(self.free))
        if len(self.free):
            try:
                factor = sla.splu(reduced)
                inverse = sla.LinearOperator(reduced.shape, matvec=factor.solve,
                    rmatvec=lambda x: factor.solve(x, trans='T'), dtype=float)
                # t=1 has a deterministic initial vector; this remains an
                # estimate/lower bound, never an exact condition number.
                estimate, force, action = sla.onenormest(inverse, t=1, compute_v=True, compute_w=True)
                witness = action / max(float(np.linalg.norm(action)), 1e-300)
                error = float(np.linalg.norm(reduced @ action - force, 1) /
                              max(norm * np.linalg.norm(action, 1) + np.linalg.norm(force, 1), 1e-300))
                row.update(condition_number_1_estimate=float(norm * estimate), solve_backward_error=error,
                           factorization='LU completed; not a rank or stability certificate')
            except RuntimeError as error:
                row['factorization'] = 'LU failed: ' + str(error)
        if not np.isfinite(reduced.data).all() or any(v is not None and not np.isfinite(v) for v in [row['condition_number_1_estimate'], row['solve_backward_error']]):
            raise ValueError('Nonfinite full frozen contact operator diagnostic')
        score = float('inf') if len(self.free) and row['condition_number_1_estimate'] is None else row['condition_number_1_estimate'] or 0.
        if self.worst is None or score > self.worst:
            self.worst = score
            self.snapshot = {'bulk': self.bulk.copy(), 'contact': contact, 'reduced': reduced, 'witness': witness, 'row': row.copy(), 'traces': self.traces.copy()}
        self.history.append(row)
        return row

    def write(self, directory):
        summary = {'performed': not bool(self.reason), 'reason': self.reason, 'frames': len(self.history),
                   'complete_frames': sum(r['complete_frozen_operator'] for r in self.history),
                   'full_contact_stability_qualified': False,
                   'scope': 'Full linear-bulk frozen-map operator, exact supported DOFs and nonsymmetric contact coupling. Condition estimates are lower bounds, not stability or physical certificates.'}
        if self.provider is not None:
            summary['scope']='Native accepted bulk algorithmic/geometric tangent and frozen-map contact operator with exact supports; unchecked plastic material derivatives remain incomplete. Conditioning and symmetric witnesses do not establish changing-map, dynamic or physical stability.'
        if self.reason:
            write_json(directory / 'contact-global.json', {'summary': summary, 'history': self.history})
            return summary
        estimates = [r['condition_number_1_estimate'] for r in self.history if r['condition_number_1_estimate'] is not None]
        summary.update(total_dofs=3*len(self.system.ids), free_dofs=len(self.free),
                       maximum_condition_number_1_estimate=max(estimates, default=None),
                       failed_factorizations=sum(r['factorization'].startswith('LU failed') for r in self.history))
        spectral = {'performed': False, 'reason': 'No accepted operator'}
        if self.snapshot:
            snapshot = self.snapshot; reduced = snapshot['reduced']
            sparse.save_npz(directory / 'contact-global-bulk.npz', snapshot['bulk'])
            sparse.save_npz(directory / 'contact-global-contact.npz', snapshot['contact'])
            sparse.save_npz(directory / 'contact-global-reduced.npz', reduced)
            np.savez_compressed(directory / 'contact-global-witness.npz', inverse_action_displacement=snapshot['witness'])
            write_json(directory / 'contact-global-traces.json', {'time_s': snapshot['row']['time_s'], 'points': snapshot['traces']})
            if 0 < len(self.free) <= MAX_DENSE_SPECTRUM:
                symmetric = ((reduced + reduced.T) / 2).toarray()
                values, vectors = linalg.eigh(symmetric, subset_by_index=[0, 0], check_finite=False)
                minimum = float(values[0]); vector = vectors[:, 0]
                scale = max(float(sla.norm(reduced)), 1e-300)
                spectral = {'performed': True, 'minimum_symmetric_eigenvalue_n_mm': minimum,
                            'eigenpair_relative_residual': float(np.linalg.norm(symmetric @ vector - minimum * vector) / scale),
                            'negative_symmetric_direction_found': minimum < -scale * 1e-10,
                            'scope': 'Symmetric part at the worst estimated-condition frame only; not the spectrum of a frictional dynamic system or all increments'}
                np.savez_compressed(directory / 'contact-global-symmetric-witness.npz', displacement=vector)
            else:
                spectral['reason'] = 'Symmetric spectrum is limited to 2048 free DOFs or there are no free DOFs'
            write_json(directory / 'contact-global-dofs.json', {'node_ids': self.system.ids, 'components': ['X', 'Y', 'Z'],
                'ordering': 'node then world component', 'fixed_dofs': self.fixed, 'free_dofs': self.free.tolist(),
                'snapshot': snapshot['row'], 'bulk_quadrature': 'four native C3D10 points; accepted material/geometric tensor' if self.provider is not None else 'four native C3D10 points; full ten-node isotropic Kelvin strain',
                'symmetric_part': spectral})
        summary['worst_frame_symmetric_part'] = spectral
        write_json(directory / 'contact-global.json', {'summary': summary, 'history': self.history})
        return summary
