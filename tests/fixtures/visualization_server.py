"""Disposable API fixture for the integrated visualization browser acceptance."""
import os
import tempfile

import uvicorn

from o3d.api import create_app
from o3d.geometry import build
from o3d.model import Project
from o3d.store import Store


if __name__ == "__main__":
    port=int(os.environ.get("O3D_TEST_PORT","8954"))
    if port==8787:
        raise ValueError("The user preview port is not a test fixture")
    with tempfile.TemporaryDirectory(prefix="o3d-visualization-integration-") as folder:
        store=Store(folder)
        store.init(Project(name="Visualization browser fixture",features=[
            {"id":"p","type":"profile","width":20,"height":16},
            {"id":"b","type":"extrude","profile":"p","distance":12}]))
        build(store,store.load())
        uvicorn.run(create_app(folder,project_windows=False),host=os.environ.get("O3D_TEST_HOST","127.0.0.1"),port=port)
