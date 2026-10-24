"""Measured native payloads used by the contextual modeling workspace.

These are engine integration checks, not full Fusion acceptance. Browser tests
exercise the same payloads through Store's native transaction API separately.
"""
import math

import pytest

from o3d.native import NativeSession


def test_canvas_nested_profiles_dimensions_and_conflict_are_real():
    with NativeSession() as session:
        session.execute("sketch.create", {"plane": "XY", "name": "Plate sketch"})
        rectangle = session.execute("sketch.rectangle.two_point", {"p0": [-20, -15], "p1": [20, 15]})
        circle = session.execute("sketch.circle.center", {"center": [0, 0], "radius": 5})
        underconstrained = session.execute("sketch.inspect")
        assert underconstrained["dof"] > 0
        assert any(profile["holes"] for profile in underconstrained["profiles"])
        session.execute("sketch.dimension", {"entities": [rectangle["sides"]["bottom"]], "type": "length", "value": "width = 40 mm", "if_redundant": "driven"})
        session.execute("sketch.dimension", {"entities": [circle["curves"][0]], "type": "diameter", "value": "10 mm", "if_redundant": "driven"})
        session.execute("sketch.auto_constrain")
        constrained = session.execute("sketch.inspect")
        assert constrained["dof"] == 0
        assert constrained["fully_constrained"]
        with pytest.raises(ValueError, match="determined|conflict|already"):
            session.execute("sketch.dimension", {"entities": [rectangle["sides"]["bottom"]], "type": "length", "value": "41 mm"})
        assert session.execute("sketch.inspect")["curves"] == constrained["curves"]
        session.execute("sketch.finish")
        session.execute("solid.extrude", {"sketch": "Plate sketch", "distance": "5 mm", "operation": "new"})
        measure = session.execute("inspect.measure")
        assert measure["body_count"] == 1
        # Native circular mass integration currently differs by ~0.0012%; require
        # agreement within 0.01%, independently of the rendered tessellation.
        assert measure["total"]["volume_mm3"] == pytest.approx((1200 - math.pi * 25) * 5, rel=1e-4)


def test_canvas_point_drag_respects_constraints_and_reopens(tmp_path):
    saved = tmp_path / "canvas.solidcraft"
    with NativeSession() as session:
        session.execute("sketch.create", {"plane": "XY", "name": "Drag"})
        result = session.execute("sketch.line", {"points": [[0, 0], [20, 0]], "infer": True})
        line = result["curves"][0]
        session.execute("sketch.dimension", {"entities": [line], "type": "length", "value": "20 mm"})
        before = session.execute("sketch.inspect")
        endpoint = before["curves"][0]["end"]
        session.execute("sketch.drag", {"entity": endpoint, "from": [20, 0], "to": [25, 7]})
        after = session.execute("sketch.inspect")
        assert after["curves"][0]["end_at"] == pytest.approx([20, 0], abs=1e-5)
        assert after["dof"] == 0
        session.execute("file.save_as", {"path": str(saved)})
    with NativeSession() as session:
        session.execute("doc.open", {"path": str(saved)})
        reopened = session.execute("sketch.inspect", {"sketch": "Drag"})
        assert reopened["curves"] == after["curves"]
        assert reopened["constraints"] == after["constraints"]


def test_projection_is_associative():
    with NativeSession() as session:
        session.execute("parameters.add", {"name": "width", "expression": "40 mm", "unit": "mm"})
        session.execute("solid.box", {"length": "width", "width": "30 mm", "height": "5 mm", "body_name": "Plate"})
        session.execute("sketch.create", {"plane": "XY", "offset": "5 mm", "name": "Projected"})
        session.execute("sketch.project", {"refs": [{"body": "Plate"}]})
        before = session.execute("sketch.inspect")
        assert before["links"] and not any(link.get("lost") for link in before["links"])
        assert max(p["at"][0] for p in before["points"]) == pytest.approx(40)
        session.execute("parameters.change", {"name": "width", "expression": "50 mm"})
        after = session.execute("sketch.inspect")
        assert max(p["at"][0] for p in after["points"]) == pytest.approx(50)


def test_projection_break_keeps_current_geometry():
    # The Oxygen native wrapper materializes the solved sketch before detaching;
    # otherwise upstream detaches stale stored geometry after parameter edits.
    with NativeSession() as session:
        session.execute("parameters.add", {"name": "width", "expression": "40 mm", "unit": "mm"})
        session.execute("solid.box", {"length": "width", "width": 30, "height": 5, "body_name": "Plate"})
        session.execute("sketch.create", {"plane": "XY", "offset": 5})
        session.execute("sketch.project", {"refs": [{"body": "Plate"}]})
        session.execute("parameters.change", {"name": "width", "expression": "50 mm"})
        after = session.execute("sketch.inspect")
        session.execute("sketch.break_link", {"link": after["links"][0]["id"]})
        detached = session.execute("sketch.inspect")
        assert not detached["links"]
        assert [(c["start_at"], c["end_at"]) for c in detached["curves"]] == [(c["start_at"], c["end_at"]) for c in after["curves"]]


def test_timeline_edit_suppress_reorder_and_multibody_measurements():
    with NativeSession() as session:
        base = session.execute("solid.box", {"length": "40 mm", "width": "30 mm", "height": "10 mm", "body_name": "Base"})
        session.execute("solid.box", {"length": 5, "width": 5, "height": 5, "corner": [50, 0, 0], "operation": "new", "body_name": "Tool"})
        measure = session.execute("inspect.measure")
        assert measure["body_count"] == 2
        assert measure["total"]["volume_mm3"] == pytest.approx(12125)
        definition = session.execute("timeline.inspect", {"feature": base["feature"]})
        assert definition["definition"]
        session.execute("timeline.edit", {"feature": base["feature"], "set": {"height": "12 mm"}})
        assert session.execute("inspect.measure")["total"]["volume_mm3"] == pytest.approx(14525)
        session.execute("timeline.reorder", {"feature": base["feature"], "position": 1})
        session.execute("timeline.suppress", {"feature": base["feature"], "suppressed": True})
        assert session.execute("inspect.measure")["total"]["volume_mm3"] == pytest.approx(125)
        session.execute("timeline.suppress", {"feature": base["feature"], "suppressed": False})
        assert session.execute("inspect.measure")["body_count"] == 2


def test_parameter_units_equations_and_cycle_rejection(tmp_path):
    with NativeSession() as session:
        session.execute("parameters.add", {"name": "height", "expression": "1 in", "unit": "mm"})
        session.execute("parameters.add", {"name": "clearance", "expression": "2 mm", "unit": "mm"})
        session.execute("parameters.add", {"name": "width", "expression": "2 * height + clearance", "unit": "mm"})
        session.execute("solid.box", {"length": "width", "width": "10 mm", "height": "height"})
        body = session.execute("inspect.measure")["bodies"][0]
        assert body["volume_mm3"] == pytest.approx(52.8 * 10 * 25.4)
        with pytest.raises(ValueError, match="cycle|cyclic|circular"):
            session.execute("parameters.change", {"name": "height", "expression": "width"})
        assert session.execute("inspect.measure")["bodies"][0]["volume_mm3"] == pytest.approx(body["volume_mm3"])
        exported = session.execute("parameters.export", {"format": "csv"})["text"]
        assert "clearance" in exported and "2 * height + clearance" in exported
