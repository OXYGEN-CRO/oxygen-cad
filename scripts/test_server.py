"""Dedicated disposable project for browser acceptance tests."""
import tempfile
from pathlib import Path
import uvicorn
from o3d.api import create_app
from o3d.geometry import build
from o3d.model import Project
from o3d.store import Store

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="o3d-browser-") as folder:
        store = Store(folder)
        store.init(Project.model_validate_json((root / "examples/bracket.json").read_text()))
        build(store, store.load())
        uvicorn.run(create_app(folder), host="127.0.0.1", port=8877)
