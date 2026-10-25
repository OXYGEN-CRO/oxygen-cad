"""HTTP examples are installed package resources, independent of checkout paths."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from o3d import engineering_routes


@pytest.mark.parametrize(('feature', 'filename'), [
    ('brief', 'engineering-brief.json'),
    ('interfaces', 'component-interfaces.json'),
    ('procurement', 'procurement-bom.json'),
    ('supplier', 'supplier-request.json'),
])
def test_engineering_examples_use_packaged_resources(feature, filename, tmp_path, monkeypatch):
    # The package resource retains the checked-in example exactly. Deliberately
    # move the apparent route file away from any checkout examples directory;
    # a source-root-relative loader would fail this request.
    expected = json.loads((Path(__file__).resolve().parents[1] / 'examples' / filename).read_text())
    monkeypatch.setattr(engineering_routes, '__file__', str(tmp_path / 'installed/o3d/engineering_routes.py'))
    app = FastAPI()
    engineering_routes.mount_engineering(app, None)
    with TestClient(app) as client:
        response = client.get('/api/engineering/examples/' + feature)
    assert response.status_code == 200, response.text
    assert response.json() == (expected['engineering_brief'] if feature == 'brief' else expected)
