from pathlib import Path
import pytest
from o3d.model import Project
from o3d.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def beam():
    return Project.model_validate_json((ROOT / "examples/cantilever.json").read_text())


@pytest.fixture
def store(tmp_path, beam):
    result = Store(tmp_path / "project")
    result.init(beam)
    return result
