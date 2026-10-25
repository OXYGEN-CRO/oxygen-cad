"""Independent full frozen-map FE/contact operator, importing no Oxygen code.

scikit-fem supplies bulk geometry, strains and quadratic traces. An independently
compiled unmodified CalculiX contact kernel supplies force/tangent differences.
This verifies a frozen linear-bulk operator, not changing-map or physical stability.
"""
import sys, json, hashlib, subprocess
from pathlib import Path
import numpy as np
import scipy, skfem
from scipy import sparse
from skfem import Basis, ElementTetP2, ElementTriP2, ElementVector, asm
from skfem.models.elasticity import linear_elasticity, lame_parameters
from reference_fem import geometry


def run(request, output):
    data = request['mesh']; nodes = {int(n): p for n, p in data['nodes'].items()}
    ids = sorted(nodes); lookup = {n: i for i, n in enumerate(ids)}; size = 3 * len(ids)
    rows, columns, values = [], [], []
    alpha = (5 + 3 * np.sqrt(5)) / 20
    beta = (5 - np.sqrt(5)) / 20
    quadrature = np.array([[alpha if j == i else beta for j in range(4)] for i in range(4)])[:, 1:].T
    for body, material in request['materials'].items():
        cells = [e for e, b in zip(data['elements'], request['element_bodies']) if b == body]
        used = {n for e in cells for n in e}
        local = {'nodes': {n: nodes[n] for n in used}, 'elements': cells, 'surfaces': {}}
        mesh, _, _, index, _, mid, _ = geometry(local)
        basis = Basis(mesh, ElementVector(ElementTetP2()), quadrature=(quadrature, np.ones(4) / 24))
        mapping = np.empty(basis.N, dtype=int)
        for n in used:
            dofs = basis.nodal_dofs[:, index[n]] if n in index else basis.edge_dofs[:, mid[n]]
            mapping[dofs] = 3 * lookup[n] + np.arange(3)
        matrix = asm(linear_elasticity(*lame_parameters(material['youngs_modulus_mpa'], material['poisson_ratio'])), basis).tocoo()
        rows.extend(mapping[matrix.row]); columns.extend(mapping[matrix.col]); values.extend(matrix.data)
    bulk = sparse.coo_matrix((values, (rows, columns)), shape=(size, size)).tocsr()
    receipt = json.loads(Path(request['native_receipt']).read_text())
    binary = Path(request['native_receipt']).parent / 'contact-kernel-probe'
    if hashlib.sha256(binary.read_bytes()).hexdigest() != receipt['binary_sha256']:
        raise ValueError('Independent native kernel differs from its receipt')
    driver = Path(request['native_receipt']).parent / 'source/contact_kernel_probe.f90'
    if hashlib.sha256(driver.read_bytes()).hexdigest() != receipt['driver_sha256'] or 'area,h' not in driver.read_text():
        raise ValueError('Use the recorded variable-step verification driver')
    rows, columns, tangents, differences = [], [], [], []
    maximum_fd = maximum_trace = 0.; points = 0
    for trace in request['points']:
        if trace['boundary']:
            raise ValueError('One-sided boundary is not a closed-branch derivative reference')
        weights = []
        for role in ['secondary', 'primary']:
            uv = np.array(trace[role + '_uv'])[:, None]
            shape = np.array([ElementTriP2().lbasis(uv, i)[0][0] for i in range(6)])
            maximum_trace = max(maximum_trace, float(np.max(abs(shape - trace[role + '_weights']))))
            weights.extend(shape if role == 'secondary' else -shape)
        normal = np.array(trace['normal']); relative = np.array(trace['relative_mm']); gap = trace['gap_mm']
        # The standalone native probe uses coincident reference planes. Preserve
        # the actual scalar gap, including any reference-plane offset, explicitly.
        jump = relative + (gap - normal @ relative) * normal
        old = np.array(trace['initial_state'] or [0.] * 9)
        kn = trace['normal_penalty_n_mm3']; kt = trace['tangential_penalty_n_mm3']; mu = trace['friction_coefficient']
        step = min(1e-8, abs(gap) * 1e-3)
        if mu:
            projector = np.eye(3) - np.outer(normal, normal)
            trial = kt * (old[6:9] + projector @ (relative - old[3:6]) - old[:3])
            margin = abs(np.linalg.norm(trial) - mu * (-kn * gap))
            step = min(step, margin / (kt + mu * kn) * 1e-3)
        if step <= 0 or not np.isfinite(step):
            raise ValueError('No two-sided interior finite-difference step')
        inputs = [*normal, *jump, *old, kn, kt, mu, trace['area_mm2'], step]
        native = subprocess.run([str(binary)], input=' '.join(format(v, '.17g') for v in inputs) + '\n',
                                text=True, capture_output=True, check=True)
        numbers = np.array([float(s.replace('D', 'E')) for s in native.stdout.split()])
        if numbers.shape != (36,) or not np.isfinite(numbers).all():
            raise ValueError('Incomplete native contact kernel output')
        tangent = numbers[18:27].reshape(3, 3); difference = numbers[27:36].reshape(3, 3)
        maximum_fd = max(maximum_fd, float(np.linalg.norm(tangent - difference) / np.linalg.norm(tangent)))
        jump_operator = np.kron(np.array(weights)[None, :], np.eye(3))
        dofs = (np.array([lookup[n] for n in trace['secondary_nodes'] + trace['primary_nodes']])[:, None] * 3 + np.arange(3)).ravel()
        rows.extend(np.repeat(dofs, 36)); columns.extend(np.tile(dofs, 36))
        tangents.extend((jump_operator.T @ tangent @ jump_operator).ravel())
        differences.extend((jump_operator.T @ difference @ jump_operator).ravel()); points += 1
    contact = sparse.coo_matrix((tangents, (rows, columns)), shape=(size, size)).tocsr()
    contact_fd = sparse.coo_matrix((differences, (rows, columns)), shape=(size, size)).tocsr()
    sparse.save_npz(output.parent / 'reference-bulk.npz', bulk)
    sparse.save_npz(output.parent / 'reference-contact.npz', contact)
    free = request['free_dofs']
    sparse.save_npz(output.parent / 'reference-reduced.npz', (bulk + contact)[free][:, free])
    sparse.save_npz(output.parent / 'reference-reduced-fd.npz', (bulk + contact_fd)[free][:, free])
    return {'points': points, 'maximum_trace_weight_difference': maximum_trace,
            'maximum_native_tangent_force_fd_relative_difference': maximum_fd,
            'native_binary_sha256': receipt['binary_sha256'], 'native_driver_sha256': receipt['driver_sha256'],
            'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'geometry_adapter_sha256': hashlib.sha256(Path(__file__).with_name('reference_fem.py').read_bytes()).hexdigest(),
            'software': {'scikit_fem': skfem.__version__, 'numpy': np.__version__, 'scipy': scipy.__version__},
            'scope': 'Independent exact-quadrature bulk/P2 traces and native closed-branch contact tangent/force differences; no physical or changing-map qualification'}


if __name__ == '__main__':
    output = Path(sys.argv[2]); output.write_text(json.dumps(run(json.loads(Path(sys.argv[1]).read_text()), output), indent=2, allow_nan=False) + '\n')
