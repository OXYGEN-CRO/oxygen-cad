"""Disposable Store, production native routes, and production command service."""
import os
import tempfile

import uvicorn

from o3d.api import create_app
from o3d.model import Project
from o3d.native_routes import mount_native_routes
from o3d.store import Store
from starlette.routing import Mount

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="o3d-modeling-ui-") as folder:
        Store(folder).init(Project(name="Modeling acceptance"))
        app = create_app(folder)
        if not any(route.path == "/api/native/execute" for route in app.routes):
            mount_native_routes(app, app.state.store, app.state.jobs)
        # The shell mounts StaticFiles at '/', so appended production adapters
        # must precede that catch-all in this isolated fixture too.
        app.router.routes.sort(key=lambda route: isinstance(route, Mount))
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("O3D_TEST_PORT", "8964")))
