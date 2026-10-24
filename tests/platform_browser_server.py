"""Disposable catalog fixture for the integrated platform browser acceptance test.

Run from an integrated checkout with PYTHONPATH=<checkout>/src and an installed
native engine. Unlike the general browser fixture, child projects also remain
inside the temporary catalog and are removed on shutdown.
"""
import os
import tempfile
from pathlib import Path

import uvicorn

from o3d.api import create_app
from o3d.geometry import build
from o3d.model import Project
from o3d.store import Store


if __name__ == "__main__":
    port = int(os.environ.get("O3D_PLATFORM_TEST_PORT", "9371"))
    if port == 8787:
        raise ValueError("Platform browser acceptance must use an isolated port, never the user preview")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="oxygen-platform-browser-") as folder:
        store = Store(Path(folder) / "main")
        project = Project.model_validate_json((root / "examples/bracket.json").read_text())
        project.name = "Platform browser fixture"
        store.init(project)
        result = build(store, store.load())
        store.import_step(store.artifact(result["artifacts"]["step"]).read_bytes(), "immutable-reference.step", 1)
        build(store, store.load())
        uvicorn.run(create_app(store.root), host=os.environ.get("O3D_PLATFORM_TEST_HOST", "127.0.0.1"), port=port)
