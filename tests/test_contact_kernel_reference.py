"""Actual unmodified upstream Fortran force/stiffness, independently compiled."""
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import pytest
from o3d.simulation_contact_stability import closed_response
from o3d.simulation_solver_profile import contract


@pytest.fixture(scope='module')
def native():
    path=os.environ.get('O3D_CONTACT_KERNEL_EVIDENCE')
    if not path:pytest.skip('Configure independently compiled upstream contact-kernel evidence')
    value=json.loads(Path(path).read_text())
    assert value['schema']==1 and value['source_sha256']==contract()['source_sha256']
    driver=Path(__file__).resolve().parents[1]/'scripts/contact_kernel_probe.f90'
    assert value['driver_sha256']==hashlib.sha256(driver.read_bytes()).hexdigest()
    assert len(value['cases'])==8
    return {row['name']:row for row in value['cases']}


@pytest.mark.parametrize('name',[kind+rotation for rotation in ['', '-rotated'] for kind in ['frictionless','stick','sliding','reverse']])
def test_independent_return_map_and_nonsymmetric_derivative_match_native(native,name):
    row=native[name];normal=np.array(row['normal']);relative=np.array(row['relative_displacement_mm'])
    result=closed_response(normal,float(normal@relative),relative,row['initial_state'],row['normal_penalty_n_mm3'],
        row['tangential_penalty_n_mm3'],row['friction_coefficient'],row['area_mm2'])
    scale=np.linalg.norm(row['force_n'])
    assert np.linalg.norm(result['force_n']-row['force_n'])/scale<1e-9
    scale=np.linalg.norm(row['stiffness_n_mm'])
    assert np.linalg.norm(result['tangent_n_mm']-row['stiffness_n_mm'])/scale<1e-10
    assert np.linalg.norm(result['tangent_n_mm']-row['force_finite_difference_n_mm'])/scale<1e-6
    assert row['finite_difference_relative_error']<1e-6
    if row['friction_coefficient']:
        assert result['state']==pytest.approx(row['state'],abs=1e-12)
    if name.startswith(('sliding','reverse')):
        assert np.linalg.norm(result['tangent_n_mm']-result['tangent_n_mm'].T)/scale>.1
    else:assert np.linalg.norm(result['tangent_n_mm']-result['tangent_n_mm'].T)/scale<1e-12
