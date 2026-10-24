"""Independent project windows: each URL owns its Store, jobs and authentication."""
from fastapi.responses import JSONResponse
from starlette.routing import Mount, Router

from .projects import ProjectCatalog


class ProjectWindows:
    def __init__(self, catalog_root):
        self.catalog = ProjectCatalog(catalog_root)
        self.apps = {}
        self.router = Router(routes=[Mount("/{directory}", app=self.open_window)])

    async def __call__(self, scope, receive, send):
        await self.router(scope, receive, send)

    async def open_window(self, scope, receive, send):
        directory = scope["path_params"]["directory"]
        try:
            store = self.catalog.store(directory)
            if not (store.root / "o3d.json").is_file():
                raise ValueError("Project does not exist")
            if directory not in self.apps:
                from .api import create_app
                self.apps[directory] = create_app(store.root, project_windows=False)
        except ValueError as error:
            await JSONResponse({"detail": str(error)}, status_code=404)(scope, receive, send)
            return
        await self.apps[directory](scope, receive, send)

    def close(self):
        for app in self.apps.values():
            app.state.jobs.close()
