import json
import numpy as np
import pytest

from o3d.mode_tracking import Sampler, correlate_samples, nodal_modes, track_modes
from o3d.quadratic import EDGES


def independent_samples():
    return np.eye(12)[:, :6].reshape(4, 3, 6).transpose(0, 2, 1)


def test_sign_scale_and_crossing_are_matched_by_shape_not_frequency_order():
    a = independent_samples()
    b = a[:, [1, 0, 2, 3, 4, 5]]*np.array([-4, 12, .1, 2, 5, 1])[None, :, None]
    result = correlate_samples(a, b, np.arange(1, 5), [10, 10.4, 30, 40, 50, 60],
                               [10.02, 10.38, 30, 40, 50, 60], 2)
    assert result["passed"]
    assert result["matching"][:2] == [2, 1]
    # Sorted scalar comparison would incorrectly claim only 0.2% change.
    assert min(result["quantity_changes"]) > .03


def test_rotated_repeated_eigenspace_passes_even_when_individual_mac_is_half():
    a = independent_samples()
    b = a.copy()
    b[:, 0] = (a[:, 0]+a[:, 1])/np.sqrt(2)
    b[:, 1] = (a[:, 0]-a[:, 1])/np.sqrt(2)
    result = correlate_samples(a, b, np.ones(4), [10, 10, 30, 40, 50, 60],
                               [10.001, 10.002, 30, 40, 50, 60], 2)
    assert result["passed"]
    assert result["mac_matrix"][0][:2] == pytest.approx([.5, .5])
    assert result["groups"][0]["minimum_squared_cosine"] == pytest.approx(1)
    assert len(result["groups"][0]["previous_modes"]) == 2


def test_partial_repeated_family_cannot_claim_convergence():
    a = independent_samples()
    result = correlate_samples(a, a, np.ones(4), [10, 10, 30, 40, 50, 60],
                               [10, 10, 30, 40, 50, 60], 1)
    assert not result["passed"]
    assert result["groups"][0]["requested_window_cut"]


def test_duplicate_basis_vectors_do_not_pass_a_repeated_family():
    a = independent_samples()
    b = a.copy()
    b[:, 1] = b[:, 0]
    result = correlate_samples(a, b, np.ones(4), [10, 10, 30, 40, 50, 60],
                               [10, 10, 30, 40, 50, 60], 2)
    assert not result["passed"]
    assert not result["groups"][0]["full_rank"]


def tetra_mesh(refined=False, curved=False):
    vertices = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [.25, .25, .25]])
    tets = [(0, 1, 2, 3)] if not refined else [(0, 1, 2, 4), (0, 1, 4, 3), (0, 4, 2, 3), (4, 1, 2, 3)]
    nodes, lookup = {}, {}

    def node(p):
        key = tuple(p)
        if key not in lookup:
            # Deliberately different IDs across the two meshes.
            n = (len(nodes)+1)*(13 if refined else 7)
            lookup[key] = n
            r, s, t = p
            nodes[str(n)] = [r+.15*s*t, s+.1*r*t, t+.05*r*s] if curved else list(p)
        return lookup[key]

    elements = []
    for tet in tets:
        corners = vertices[list(tet)]
        elements.append([node(p) for p in corners]+[node((corners[a]+corners[b])/2) for a, b in EDGES])
    return {"nodes": nodes, "elements": elements}


def fields(mesh):
    result = [dict() for _ in range(6)]
    for n, p in mesh["nodes"].items():
        x, y, z = p
        values = ([1, 0, 0], [0, 1, 0], [0, 0, 1], [x, 0, 0], [0, y, 0], [0, 0, z])
        for mode, value in zip(result, values):
            mode[int(n)] = value
    return result


@pytest.mark.parametrize("curved", [False, True])
def test_different_topology_and_ids_interpolate_the_same_physical_field(curved):
    coarse, fine = tetra_mesh(curved=curved), tetra_mesh(True, curved)
    old_modes, new_modes = fields(coarse), fields(fine)
    # Permute and change normalization. Frequencies stay in solver order.
    new_modes[0], new_modes[1] = new_modes[1], new_modes[0]
    new_modes[0] = {n: (-3*np.asarray(v)).tolist() for n, v in new_modes[0].items()}
    result = track_modes(coarse, old_modes, [10, 20, 30, 40, 50, 60],
                         fine, new_modes, [20, 10, 30, 40, 50, 60], 2)
    assert result["passed"]
    assert result["matching"][:2] == [2, 1]
    assert result["quantity_changes"] == pytest.approx([0, 0])
    assert result["quadrature_sensitivity"] < 1e-7
    for q in result["quadrature_checks"]:
        assert q["sample_count"] in (64, 125)
        assert q["volume_mm3"] == pytest.approx(1/6, rel=.03)
    assert result["previous_mesh_sha256"] != result["mesh_sha256"]


def test_outside_points_and_incomplete_or_zero_fields_refuse():
    mesh = tetra_mesh(curved=True)
    with pytest.raises(ValueError, match="extrapolation"):
        Sampler(mesh).interpolation(np.array([[2., 0, 0]]))
    modes = fields(mesh)
    modes[0].pop(next(iter(modes[0])))
    with pytest.raises(ValueError, match="Incomplete"):
        nodal_modes(mesh, modes)
    with pytest.raises(ValueError, match="Zero"):
        nodal_modes(mesh, [{int(n): [0, 0, 0] for n in mesh["nodes"]}])


def test_buckling_field_pairing_skips_only_the_verified_reference_load_frame():
    from o3d.analysis import eigenmode_fields
    frames = [{"name": "DISP", "time": v, "values": {1: [v, 0, 0]}} for v in [0, 12, 30]]
    assert eigenmode_fields(frames, [12, 30], "buckling") == [frames[1]["values"], frames[2]["values"]]
    with pytest.raises(RuntimeError, match="reference-load"):
        eigenmode_fields(frames[1:], [12, 30], "buckling")
    with pytest.raises(RuntimeError, match="does not match"):
        eigenmode_fields(frames, [12, 32], "buckling")
    with pytest.raises(RuntimeError, match="correspond"):
        eigenmode_fields(frames, [12, 30], "modal")


def test_tracking_failure_makes_a_real_eigenstudy_inconclusive(store, monkeypatch):
    from o3d.solver import simulate
    import o3d.mode_tracking as tracking
    p = store.load()
    p.study.type = "modal"
    p.study.forces = []
    p.study.stress_probe_mm = None
    p.study.mode_count = 2
    p.requirements = []
    monkeypatch.setattr(tracking, "track_modes", lambda *a: {"passed": False, "reason": "injected shape mismatch"})
    result = simulate(store, p)
    assert result["state"] == "inconclusive"
    assert not result["converged"]
    assert any("injected shape mismatch" in warning for warning in result["warnings"])
    assert any(path.endswith("mode-tracking.json") for path in result["artifacts"].values())
    saved = json.loads(store.artifact(result["field_artifact"]).read_text())
    assert len(saved["guard_modes"]) == 4


@pytest.mark.parametrize("kind", ["modal", "buckling"])
def test_square_column_repeated_bending_family_tracks_real_solver_modes(store, kind):
    from o3d.solver import simulate
    p = store.load()
    p.features[0].width = 4.0
    p.study.type = kind
    p.study.stress_probe_mm = None
    p.study.mode_count = 2
    if kind == "modal":
        p.study.forces = []
    else:
        p.study.forces[0].vector_n = (-1, 0, 0)
    p.requirements = []
    result = simulate(store, p)
    assert result["state"] == "passed"
    assert result["mode_tracking"]["passed"]
    group = result["mode_tracking"]["groups"][0]
    assert group["previous_modes"] == [1, 2]
    assert group["minimum_squared_cosine"] > .995
    assert all(it["mode_tracking"]["passed"] for it in result["iterations"][1:])
    if kind == "buckling":
        field = json.loads(store.artifact(result["field_artifact"]).read_text())
        for mode in field["modes"]:
            vectors = np.asarray(list(mode["vectors"].values()))
            # A bending eigenmode is transverse; the skipped static response is axial.
            assert np.linalg.norm(vectors[:, 0])/np.linalg.norm(vectors[:, 1:]) < .05


def test_request_cutting_a_real_repeated_family_is_inconclusive(store):
    from o3d.solver import simulate
    p = store.load()
    p.features[0].width = 4.0
    p.study.type = "modal"
    p.study.stress_probe_mm = None
    p.study.mode_count = 1
    p.study.forces = []
    p.requirements = []
    result = simulate(store, p)
    assert result["state"] == "inconclusive"
    assert not result["converged"]
    assert result["mode_tracking"]["groups"][0]["requested_window_cut"]


def test_curved_hollow_cad_modal_family_and_exported_correlation(tmp_path, beam, mesh_families):
    import zipfile
    from o3d.artifacts import export_bundle
    from o3d.solver import simulate
    from o3d.store import Store
    p = mesh_families['tube']
    p.material = beam.material.model_copy(deep=True)
    p.study.type = 'modal'; p.study.forces = []; p.study.fixed_faces = ['part:xmin']
    p.study.stress_probe_mm = None; p.study.mode_count = 2
    p.study.mesher = 'gmsh'; p.study.assumptions_confirmed = True; p.requirements = []
    store = Store(tmp_path/'curved-modal'); store.init(p)
    result = simulate(store, p)
    assert result['state'] == 'passed'
    assert result['mode_tracking']['groups'][0]['minimum_squared_cosine'] > .995
    assert result['mode_tracking']['groups'][0]['previous_modes'] == [1, 2]
    # A coarse CAD approximation can place a few points outside the next mesh.
    # That comparison must remain recorded as inconclusive, while a subsequent
    # fully covered comparison can establish convergence without extrapolation.
    assert result['iterations'][-1]['mode_tracking']['passed']
    assert result['mode_tracking']['quadrature_sensitivity'] <= .005
    release = export_bundle(store, p)
    with zipfile.ZipFile(store.artifact(release['artifact'])) as archive:
        audits = [name for name in archive.namelist() if name.endswith('/mode-tracking.json')]
        assert len(audits) == len(result['iterations'])-1
        last = next(name for name in audits if f"mesh_{len(result['iterations'])-1}/" in name)
        assert json.loads(archive.read(last))['passed']
        for i, iteration in enumerate(result['iterations'][1:], 1):
            name = next(name for name in audits if f'mesh_{i}/' in name)
            assert json.loads(archive.read(name)) == iteration['mode_tracking']
