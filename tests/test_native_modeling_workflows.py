"""Analytic modeling acceptance against the source engine and independent OCCT exports."""
import math

import build123d as bd
import pytest

from o3d.geometry import build
from o3d.model import Project
from o3d.native import NativeSession
from o3d.native_document import execute_native, query_native
from o3d.store import Store


def store_session(tmp_path, session):
    snapshot = session.execute('oxygen.document.get')
    project = Project(native_document=snapshot['document'], domains={'modeling': {'schema_version': 1, 'native_context': snapshot['context']}})
    store = Store(tmp_path); store.init(project)
    return store


def checked_volume(store, expected, *, bodies=1):
    project = Store(store.root).load()
    result = build(store, project)
    assert result['volume_mm3'] == pytest.approx(expected, rel=1e-3)
    shape = bd.import_step(store.artifact(result['artifacts']['step']))
    assert shape.is_valid and len(shape.solids()) == bodies
    assert max(b['volume_relative_error'] for b in result['engine']['independent_bodies']) < 1e-3
    return result


def test_revolve_offset_section_preserves_axis_and_angle_after_reopen(tmp_path):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'turn', 'expression': '360 deg', 'unit': 'deg'})
        session.execute('sketch.create', {'name': 'RingSection', 'plane': 'XY'})
        session.execute('sketch.rectangle.two_point', {'p0': [10, 0], 'p1': [20, 5]})
        session.execute('sketch.finish')
        session.execute('solid.revolve', {'axis': 'y', 'angle': 'turn', 'body_name': 'Ring'})
        store = store_session(tmp_path, session)
    checked_volume(store, math.pi * (20**2 - 10**2) * 5)
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'turn', 'expression': '180 deg'})
    checked_volume(store, math.pi * (20**2 - 10**2) * 5 / 2)
    assert query_native(store.load(), 'timeline.inspect', {'feature': 'Revolve1'})['definition']['axis']


def test_sweep_section_on_parameterized_alternate_plane_path(tmp_path):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'reach', 'expression': '30 mm'})
        session.execute('sketch.create', {'name': 'Section', 'plane': 'XY'})
        session.execute('sketch.circle.center', {'center': [0, 0], 'radius': 2})
        session.execute('sketch.finish')
        session.execute('sketch.create', {'name': 'Path', 'plane': 'XZ'})
        session.execute('sketch.line', {'points': [[0, 0], [0, 'reach']], 'ids': ['path_line']})
        session.execute('sketch.finish')
        session.execute('solid.sweep', {'sketch': 'Section', 'path_sketch': 'Path', 'path': ['path_line'], 'body_name': 'Swept'})
        store = store_session(tmp_path, session)
    checked_volume(store, 4 * math.pi * 30)
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'reach', 'expression': '40 mm'})
    result = checked_volume(store, 4 * math.pi * 40)
    assert max(result['dimensions_mm']) == pytest.approx(40)


def test_loft_sections_remain_parameter_driven_after_cold_reopen(tmp_path):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'top_radius', 'expression': '10 mm'})
        for name, radius, height in [('Bottom', 5, 0), ('Top', 10, 20)]:
            session.execute('sketch.create', {'name': name, 'plane': {'origin': [0, 0, height], 'x_dir': [1, 0, 0], 'y_dir': [0, 1, 0]}})
            circle = session.execute('sketch.circle.center', {'center': [0, 0], 'radius': radius})['curves'][0]
            if name == 'Top':
                session.execute('sketch.dimension', {'entities': [circle], 'type': 'radius', 'value': 'top_radius'})
            session.execute('sketch.finish')
        session.execute('solid.loft', {'sections': [{'sketch': 'Bottom'}, {'sketch': 'Top'}], 'body_name': 'Frustum'})
        store = store_session(tmp_path, session)
    checked_volume(store, 20 * math.pi / 3 * (25 + 50 + 100))
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'top_radius', 'expression': '12 mm'})
    checked_volume(store, 20 * math.pi / 3 * (25 + 60 + 144))


def test_counterbore_and_cosmetic_thread_keep_measured_geometry_and_metadata(tmp_path):
    with NativeSession() as session:
        session.execute('solid.box', {'length': 30, 'width': 20, 'height': 10, 'body_name': 'Plate'})
        session.execute('solid.hole', {'position': [10, 10, 10], 'diameter': 4, 'depth': 8, 'type': 'counterbore', 'cb_diameter': 8, 'cb_depth': 2})
        session.execute('solid.hole', {'position': [23, 10, 10], 'thread': 'M6'})
        store = store_session(tmp_path, session)
    checked_volume(store, 6000 - 56 * math.pi - math.pi * 2.5**2 * 10)
    threads = query_native(store.load(), 'model.threads')['threads']
    assert len(threads) == 1 and 'M6' in str(threads[0])


def test_body_pattern_and_mirror_recompute_distinct_native_bodies(tmp_path):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'height', 'expression': '4 mm'})
        session.execute('solid.box', {'length': 2, 'width': 3, 'height': 'height', 'corner': [1, 0, 0], 'body_name': 'Seed'})
        session.execute('solid.pattern.rectangular', {'bodies': ['Seed'], 'dir1': [1, 0, 0], 'count1': 3, 'spacing1': 10})
        bodies = [b['name'] for b in session.execute('document.inspect')['bodies']]
        session.execute('solid.mirror', {'bodies': bodies, 'plane': 'YZ'})
        store = store_session(tmp_path, session)
    first = checked_volume(store, 24 * 6, bodies=6)
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'height', 'expression': '6 mm'})
    second = checked_volume(store, 36 * 6, bodies=6)
    assert [b['id'] for b in second['scene']['bodies']] == [b['id'] for b in first['scene']['bodies']]


@pytest.mark.parametrize('command,scalar,area_factor', [('solid.fillet', 'radius', 4-math.pi), ('solid.chamfer', 'distance', 2)])
def test_exact_edge_treatments_recompute_and_refuse_impossible_sizes(tmp_path, command, scalar, area_factor):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'size', 'expression': '1 mm'})
        session.execute('solid.box', {'length': 20, 'width': 10, 'height': 5, 'body_name': 'Block'})
        session.execute(command, {'body': 'Block', 'edges': [[0, 0, 2.5], [20, 0, 2.5], [0, 10, 2.5], [20, 10, 2.5]], scalar: 'size'})
        store = store_session(tmp_path, session)
    checked_volume(store, (200 - area_factor) * 5)
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'size', 'expression': '2 mm'})
    checked_volume(store, (200 - area_factor * 4) * 5)
    with pytest.raises(ValueError):
        execute_native(store, 2, 'human', 'parameters.change', {'name': 'size', 'expression': '100 mm'})
    assert store.load().revision == 2
    checked_volume(store, (200 - area_factor * 4) * 5)


def test_curved_enclosure_shell_preserves_opening_and_wall_parameter(tmp_path):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'wall', 'expression': '1 mm'})
        session.execute('solid.cylinder', {'radius': 10, 'height': 20, 'body_name': 'Cup'})
        session.execute('solid.shell', {'body': 'Cup', 'faces': [[0, 0, 20]], 'thickness': 'wall'})
        store = store_session(tmp_path, session)
    checked_volume(store, math.pi * (10**2 * 20 - 9**2 * 19))
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'wall', 'expression': '2 mm'})
    result = checked_volume(store, math.pi * (10**2 * 20 - 8**2 * 18))
    assert any(face.get('cylinder', {}).get('radius') == pytest.approx(8) for face in result['scene']['bodies'][0]['topology']['faces'])
    with pytest.raises(ValueError):
        execute_native(store, 2, 'human', 'parameters.change', {'name': 'wall', 'expression': '12 mm'})
    assert store.load().revision == 2


def test_draft_retains_neutral_plane_and_recomputes_selected_face(tmp_path):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'draft_angle', 'expression': '5 deg', 'unit': 'deg'})
        session.execute('solid.box', {'length': 20, 'width': 10, 'height': 5, 'body_name': 'Block'})
        session.execute('solid.draft', {'body': 'Block', 'faces': [[20, 5, 2.5]], 'angle': 'draft_angle', 'neutral': 'XY'})
        store = store_session(tmp_path, session)
    checked_volume(store, 1000 - 125 * math.tan(math.radians(5)))
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'draft_angle', 'expression': '10 deg'})
    result = checked_volume(store, 1000 - 125 * math.tan(math.radians(10)))
    assert result['bounds'][0][2] == pytest.approx(0, abs=1e-6)
    assert result['bounds'][1][0] == pytest.approx(20)


@pytest.mark.parametrize('command', ['solid.rib', 'solid.web'])
def test_support_web_reaches_existing_faces_and_keeps_thickness_binding(tmp_path, command):
    with NativeSession() as session:
        session.execute('parameters.add', {'name': 'rib_thickness', 'expression': '3 mm'})
        session.execute('solid.box', {'length': 24, 'width': 16, 'height': 2, 'body_name': 'Bracket'})
        session.execute('solid.box', {'length': 2, 'width': 16, 'height': 24, 'operation': 'join'})
        session.execute('sketch.create', {'name': 'Brace', 'plane': {'origin': [0, 8, 0], 'x_dir': [1, 0, 0], 'y_dir': [0, 0, 1]}})
        session.execute('sketch.line', {'points': [[2, 18], [18, 2]], 'ids': ['diagonal']})
        session.execute('sketch.finish')
        session.execute(command, {'sketch': 'Brace', 'curves': ['diagonal'], 'thickness': 'rib_thickness'})
        store = store_session(tmp_path, session)
    checked_volume(store, 1472 + 128 * 3)
    execute_native(store, 1, 'human', 'parameters.change', {'name': 'rib_thickness', 'expression': '4 mm'})
    checked_volume(store, 1472 + 128 * 4)
