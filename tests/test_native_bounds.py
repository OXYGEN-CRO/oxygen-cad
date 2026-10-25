"""Build dimensions must match actual STEP extrema, not sampled render bounds."""
import math

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
import pytest

from o3d.geometry import build
from o3d.model import Project
from o3d.native import NativeSession
from o3d.store import Store


RADIAL = 4 * math.sqrt(2 / 3)
AXIAL = 18 / math.sqrt(3)


def independent_step_bounds(path):
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() > 0
    shape = reader.OneShape()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return np.array([[p.X(), p.Y(), p.Z()] for p in (box.CornerMin(), box.CornerMax())])


@pytest.mark.parametrize('commands, expected', [
    ([('solid.sphere', {'radius': 6.25, 'center': [163.2, -7.25, 2.5], 'body_name': 'Sphere'})],
     [[156.95, -13.5, -3.75], [169.45, -1, 8.75]]),
    ([('solid.cylinder', {'radius': 4, 'height': 18, 'base': [13, -7, 4], 'axis': [1, 1, 1], 'body_name': 'Tilted cylinder'})],
     [[13-RADIAL, -7-RADIAL, 4-RADIAL], [13+AXIAL+RADIAL, -7+AXIAL+RADIAL, 4+AXIAL+RADIAL]]),
    ([('solid.box', {'length': 20, 'width': 8, 'height': 4, 'corner': [-24, -10, -3], 'body_name': 'Block'}),
      ('solid.cylinder', {'radius': 5, 'height': 9, 'base': [25, 5, -2], 'body_name': 'Cylinder'}),
      ('solid.sphere', {'radius': 7, 'center': [44, 6, 2], 'body_name': 'Sphere'})],
     [[-24, -10, -5], [51, 13, 9]]),
], ids=['off-centre-sphere', 'tilted-cylinder', 'off-centre-multibody'])
def test_closed_native_build_bounds_match_independent_step_extrema(tmp_path, commands, expected):
    with NativeSession() as session:
        for command, params in commands:
            session.execute(command, params)
        snapshot = session.execute('oxygen.document.get')
    project = Project(native_document=snapshot['document'],
        domains={'modeling': {'schema_version': 1, 'native_context': snapshot['context']}})
    store = Store(tmp_path); store.init(project)
    result = build(store, project)

    exact = independent_step_bounds(store.artifact(result['artifacts']['step']))
    np.testing.assert_allclose(exact, expected, atol=2e-7, rtol=0)
    np.testing.assert_allclose(result['bounds'], exact, atol=1e-8, rtol=0)
    np.testing.assert_allclose(result['dimensions_mm'], exact[1] - exact[0], atol=1e-8, rtol=0)
    # Persistence and the compact model-card metadata use the same exact bounds.
    state = store.state()
    np.testing.assert_allclose(state['build']['bounds'], exact, atol=1e-8, rtol=0)
    np.testing.assert_allclose(state['build']['dimensions_mm'], exact[1] - exact[0], atol=1e-8, rtol=0)
    assert len(result['scene']['bodies']) == len(commands)
    assert all(body['geometry_kind'] == 'solid' for body in result['scene']['bodies'])


def test_curved_solid_dimension_requirement_uses_actual_diameter(tmp_path):
    from o3d.geometry import check
    from o3d.model import NativeFeature, Requirement

    project = Project(features=[NativeFeature(id='sphere', command='solid.sphere',
        params={'radius': 6, 'center': [160, 0, 0], 'body_name': 'Sphere'})],
        requirements=[Requirement(id='fit', kind='max_dimension', axis='X', value=10)])
    store = Store(tmp_path); store.init(project)
    built = build(store, project)
    assert built['dimensions_mm'] == pytest.approx([12, 12, 12], abs=1e-8)
    checked = check(store, project)
    finding = next(item for item in checked['findings'] if item['feature'] == 'fit')
    assert finding['severity'] == 'error' and checked['state'] == 'failed'
    assert finding['value'] == pytest.approx(12)
    assert finding['limit'] == 10
