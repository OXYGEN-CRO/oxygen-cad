"""Deformable frozen operators, actual native states and independent assembly."""
import json, os
from pathlib import Path
import numpy as np
import pytest
from scipy import sparse
from o3d.simulation_contact_global import GlobalContactAudit, native_bulk
from o3d.simulation_contact_stability import closed_response
from o3d.simulation_quadratic import QuadraticSystem
from o3d.simulation_models import Region, Support, Load
from o3d.solver import run_process
from o3d.store import write_json
from .test_canonical_contact import reference


def problem():
    _, domain, study, case = reference(refined=False)
    return QuadraticSystem(domain.models[0].mesh, study, {m.id: m for m in domain.materials}), case


def frozen_point(system, *, sliding=False):
    from o3d.simulation_mortar import mortar_map
    sn, mn, shapes, masters, areas = mortar_map(system, 'first:xmax', 'second:xmin', 1e-7)[2][0]
    a, b = shapes[0], masters[0]
    uv = lambda shape: [float(shape[1] + (shape[3] + shape[4]) / 2), float(shape[2] + (shape[4] + shape[5]) / 2)]
    normal = np.array([1., 0., 0.]); gap = -2.5e-6
    relative = np.array([gap, .001 if sliding else 1e-5, 0.]); old = np.zeros(9)
    response = closed_response(normal, gap, relative, old, 1e7, 1e5, .3, float(areas[0]))
    trace = {'secondary_nodes': sn, 'primary_nodes': mn, 'secondary_weights': a.tolist(), 'primary_weights': b.tolist(),
             'secondary_uv': uv(a), 'primary_uv': uv(b), 'normal': normal.tolist(), 'area_mm2': float(areas[0]),
             'gap_mm': gap, 'relative_mm': relative.tolist(), 'initial_state': old.tolist(),
             'normal_penalty_n_mm3': 1e7, 'tangential_penalty_n_mm3': 1e5, 'friction_coefficient': .3}
    return response, trace


def test_native_bulk_conserves_actual_affine_strain_energy_and_all_rigid_motions():
    system, _ = problem(); matrix = native_bulk(system)
    strain = np.array([1e-3, 0., 0.]); u = (system.xyz * strain).ravel()
    # Two separately connected 10 x 2 x 2 mm bars, declared E=200000 MPa, nu=0.
    assert .5 * u @ (matrix @ u) == pytest.approx(.5 * 200000 * 1e-6 * 80, rel=1e-12)
    for nodes in system.solid_components:
        for translation, rotation in [(np.array([.3, -.1, .7]), np.zeros(3)), (np.zeros(3), np.array([.2, .5, -.4]))]:
            rigid = np.zeros_like(system.xyz)
            for n in nodes: rigid[system.index[n]] = translation + np.cross(rotation, system.mesh.nodes[n])
            assert np.linalg.norm(matrix @ rigid.ravel()) / (np.linalg.norm(matrix.data) * np.linalg.norm(rigid)) < 1e-12


@pytest.mark.parametrize('sliding', [False, True])
def test_full_quadratic_trace_preserves_signed_weights_and_global_force_derivative(tmp_path, sliding):
    system, case = problem(); audit = GlobalContactAudit(system, case)
    response, trace = frozen_point(system, sliding=sliding)
    assert min(trace['secondary_weights']) < 0 and min(trace['primary_weights']) < 0
    audit.start(); audit.point(response['tangent_n_mm'], trace); row = audit.finish(1.)
    assert row['complete_frozen_operator'] and row['solve_backward_error'] < 1e-12
    assert (row['asymmetry_frobenius_ratio'] > 1e-5) == sliding
    snapshot = audit.snapshot
    assert snapshot['reduced'].shape == (643, 643)
    # Perturb all free world DOFs, including midside DOFs. This is a controlled
    # frozen-map force derivative, not an accepted equilibrium or physical trial.
    direction = np.zeros(3 * len(system.ids)); direction[audit.free] = np.random.default_rng(17).standard_normal(len(audit.free))
    direction /= np.linalg.norm(direction)
    jump = np.zeros(3)
    for role, sign in [('secondary', 1), ('primary', -1)]:
        for n, w in zip(trace[role + '_nodes'], trace[role + '_weights']):
            jump += sign * w * direction[3 * system.index[n]:3 * system.index[n] + 3]
    def residual(t):
        change = t * jump
        r = closed_response(np.array(trace['normal']), trace['gap_mm'] + change[0],
                            np.array(trace['relative_mm']) + change, trace['initial_state'], 1e7, 1e5, .3, trace['area_mm2'])
        out = audit.bulk @ (t * direction)
        for role, sign in [('secondary', 1), ('primary', -1)]:
            for n, w in zip(trace[role + '_nodes'], trace[role + '_weights']):
                out[3 * system.index[n]:3 * system.index[n] + 3] += sign * w * r['force_n']
        return out[audit.free]
    step = 1e-8; difference = (residual(step) - residual(-step)) / (2 * step)
    derivative = snapshot['reduced'] @ direction[audit.free]
    assert np.linalg.norm(difference - derivative) / np.linalg.norm(derivative) < 1e-7
    summary = audit.write(tmp_path)
    assert not summary['full_contact_stability_qualified']
    assert summary['worst_frame_symmetric_part']['eigenpair_relative_residual'] < 1e-12
    assert (tmp_path / 'contact-global-symmetric-witness.npz').exists()


def test_soft_deformable_body_is_visible_despite_unchanged_rigid_supports():
    system, case = problem(); response, trace = frozen_point(system)
    values = []
    for factor in [1., 1e-6]:
        system.assignments['second'] = system.assignments['second'].model_copy(update={'youngs_modulus_mpa': 200000 * factor})
        audit = GlobalContactAudit(system, case); audit.start(); audit.point(response['tangent_n_mm'], trace)
        values.append(audit.finish(1.)['condition_number_1_estimate'])
    assert values[1] > values[0] * 1000


def test_unknown_traces_and_one_sided_boundaries_do_not_become_complete_operators():
    system, case = problem(); response, trace = frozen_point(system)
    audit = GlobalContactAudit(system, case); audit.start()
    audit.point(response['tangent_n_mm'], None); audit.point(np.zeros((3, 3)), trace, boundary=True)
    row = audit.finish(1.)
    assert not row['complete_frozen_operator'] and row['missing_traces'] == row['boundary_points'] == 1


def test_nonlinear_bulk_is_not_replaced_by_a_linear_operator(tmp_path, monkeypatch):
    system, case = problem(); system.study.settings.large_deformation = True
    from o3d import simulation_contact_global as module
    monkeypatch.setattr(module, 'native_bulk', lambda _: pytest.fail('Linear substitute was assembled'))
    audit = GlobalContactAudit(system, case)
    summary = audit.write(tmp_path)
    assert not summary['performed'] and not summary['full_contact_stability_qualified']
    assert 'nonlinear' in summary['reason']


def test_fully_constrained_operator_has_no_invented_free_dof_or_condition_number(tmp_path):
    system, case = problem()
    region = Region(id='all-first', body_id='first', node_ids=sorted(system.body_nodes['first']))
    system.mesh.regions.append(region); system.regions[region.id] = region
    case.supports = [Support(id='first', region_id='all-first'), Support(id='second', region_id='second:guide')]
    audit = GlobalContactAudit(system, case); audit.start(); row = audit.finish(1.)
    assert row['free_dofs'] == 0 and row['condition_number_1_estimate'] is None
    assert row['factorization'] == 'fully constrained'
    assert not audit.write(tmp_path)['worst_frame_symmetric_part']['performed']


def test_contact_entry_budget_records_unperformed_audit_without_certification(tmp_path, monkeypatch):
    system, case = problem(); response, trace = frozen_point(system)
    audit = GlobalContactAudit(system, case); audit.start()
    from o3d import simulation_contact_global as module
    monkeypatch.setattr(module, 'MAX_ENTRIES', 1)
    audit.point(response['tangent_n_mm'], trace)
    assert audit.finish(1.) is None
    summary = audit.write(tmp_path)
    assert not summary['performed'] and 'entry audit budget' in summary['reason']
    assert not summary['full_contact_stability_qualified']


@pytest.mark.skipif(not os.environ.get('O3D_REFERENCE_PYTHON') or not os.environ.get('O3D_CONTACT_KERNEL_EVIDENCE'), reason='Configure pinned independent FE environment and compiled contact-kernel receipt')
@pytest.mark.parametrize('shear,curved', [(None, False), (10., False), (100., False), (None, True)])
def test_actual_native_contact_full_operator_matches_independent_fe_and_native_force_derivatives(tmp_path, shear, curved):
    _, domain, study, case = reference(refined=False)
    if curved:
        mesh = domain.models[0].mesh
        mesh.nodes = {n: (x + .01 * y * (2 - y), y, z) for n, (x, y, z) in mesh.nodes.items()}
    if shear is not None:
        study.interfaces[0].friction_coefficient = .3; study.interfaces[0].tangential_penalty_n_mm3 = 1e5
        study.interfaces[0].source = 'Declared numerical Coulomb reference, not measured hardware'
        mesh = domain.models[0].mesh
        mesh.regions.append(Region(id='first:guide', body_id='first', node_ids=sorted({n for e in mesh.elements if e.body_id == 'first' for n in e.nodes})))
        case.supports.append(Support(id='shear-guide', region_id='first:guide', kind='partial', components=(True, False, True)))
        case.loads.append(Load(id='shear', region_id='first:xmax', vector=(0, shear, 0), source='Declared numerical shear force'))
    system = QuadraticSystem(domain.models[0].mesh, study, {m.id: m for m in domain.materials})
    from o3d.extended_solver import calculix_solve
    result = calculix_solve(system, case, tmp_path / 'native')
    audit = result['checks']['contact_current_state_audit']['full_frozen_operator']
    assert audit['performed'] and audit['frames'] > 0 and audit['complete_frames'] == audit['frames']
    assert not audit['full_contact_stability_qualified']
    dofs = json.loads((tmp_path / 'native/contact-global-dofs.json').read_text())
    traces = json.loads((tmp_path / 'native/contact-global-traces.json').read_text())
    payload = {'mesh': system.raw_mesh, 'element_bodies': [e.body_id for e in system.mesh.elements],
               'materials': {b: m.model_dump(mode='json') for b, m in system.assignments.items()},
               'points': traces['points'], 'free_dofs': dofs['free_dofs'], 'native_receipt': os.environ['O3D_CONTACT_KERNEL_EVIDENCE']}
    source = tmp_path / 'reference-request.json'; target = tmp_path / 'reference-result.json'; write_json(source, payload)
    run_process([os.environ['O3D_REFERENCE_PYTHON'], Path(__file__).resolve().parents[1] / 'scripts/reference_contact_global.py', source, target],
                tmp_path, tmp_path / 'reference.log', timeout=180)
    checked = json.loads(target.read_text()); errors = {}
    for label in ['bulk', 'contact', 'reduced']:
        own = sparse.load_npz(tmp_path / f'native/contact-global-{label}.npz')
        other = sparse.load_npz(tmp_path / f'reference-{label}.npz')
        errors[label] = float(sparse.linalg.norm(own - other) / sparse.linalg.norm(other))
        assert errors[label] < 1e-10
    own = sparse.load_npz(tmp_path / 'native/contact-global-reduced.npz'); other = sparse.load_npz(tmp_path / 'reference-reduced-fd.npz')
    errors['native_force_fd'] = float(sparse.linalg.norm(own - other) / sparse.linalg.norm(other))
    assert errors['native_force_fd'] < 1e-6
    assert checked['maximum_native_tangent_force_fd_relative_difference'] < 1e-6
    assert checked['maximum_trace_weight_difference'] < 1e-12
    write_json(tmp_path / 'comparison.json', {'relative_errors': errors, 'independent': checked, 'native_audit': audit})


@pytest.mark.parametrize('fraction,boundary', [(1., True), (1.-1e-14, True), (1.+1e-14, True), (1.-1e-3, False), (1.+1e-3, False)])
def test_current_coulomb_threshold_is_not_an_unambiguous_full_operator(tmp_path, fraction, boundary):
    # Controlled constitutive boundary, not an accepted equilibrium or hardware test.
    from o3d.simulation_contact import SurfaceContact
    from o3d.simulation_contact_stability import ContactStateAudit
    system, case = problem(); _, trace = frozen_point(system)
    record = SurfaceContact(system).records[0] | {'friction_coefficient': .3, 'tangential_penalty_n_mm3': 1e5}
    normal = np.array([1., 0., 0.]); gap = -2.5e-6; pressure = 25.
    relative = np.array([gap, .3 * pressure / 1e5 * fraction, 0.]); old = np.zeros(9)
    response = closed_response(normal, gap, relative, old, 1e7, 1e5, .3, trace['area_mm2'])
    tau = response['force_n'] / trace['area_mm2'] + pressure * normal
    locations = [sum(w * np.array(system.mesh.nodes[n]) for n, w in zip(trace[role + '_nodes'], trace[role + '_weights'])) for role in ['secondary', 'primary']]
    audit = ContactStateAudit(system, case); audit.start({'step': 2, 'increment': 1})
    audit.point(record, normal, trace['area_mm2'], gap, relative, pressure, tau, old, response['state'], *locations, trace=trace)
    audit.finish(1.); row = audit.history[-1]
    assert row['point_states']['boundary'] == int(boundary)
    assert row['full_frozen_operator']['boundary_points'] == int(boundary)
    assert row['full_frozen_operator']['complete_frozen_operator'] == (not boundary)
    assert audit.global_audit.snapshot['traces'][0]['boundary'] == boundary
    summary = audit.write(tmp_path)
    assert summary['full_frozen_operator']['complete_frames'] == int(not boundary)
    assert not summary['full_contact_stability_qualified']
