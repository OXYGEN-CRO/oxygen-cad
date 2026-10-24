from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from pydantic import BaseModel, Field
from typing import Literal
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .jobs import Jobs
from .model import Command, Project
from .store import Conflict, Store, write_json


class DomainSave(BaseModel):
    expected_revision: int = Field(ge=1)
    actor: Literal["human", "agent"] = "agent"
    data: dict


class DomainRun(BaseModel):
    expected_revision: int = Field(ge=1)
    actor: Literal["human", "agent"] = "agent"
    operation: str = Field(min_length=1, max_length=100)
    input: dict = Field(default_factory=dict)


def create_app(root=None, *, project_windows=True):
    store = Store(root or os.environ.get("O3D_PROJECT_DIR", "projects/demo"))
    jobs = Jobs(store)

    @asynccontextmanager
    async def lifespan(_):
        yield
        jobs.close()
        if windows:
            windows.close()

    app = FastAPI(title="Oxygen CAD", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.jobs = store, jobs
    windows = None
    if project_windows:
        from .project_windows import ProjectWindows
        windows = ProjectWindows(store.root.parent)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {"http://localhost:8787", "http://127.0.0.1:8787", "http://localhost:5173", "http://127.0.0.1:5173"}
        allowed.add(f"{request.url.scheme}://{request.url.netloc}")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(Conflict)
    async def conflict(_, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(_, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/api/state")
    def state():
        return store.state() | {"jobs": jobs.list()}

    @app.get("/api/project")
    def project():
        return store.load()

    @app.get("/api/schema")
    def schema():
        return Project.model_json_schema()

    @app.get("/api/domains")
    def domains():
        from .domains import catalog
        return {"workspaces": catalog()}

    @app.put("/api/domains/{domain}")
    def domain_save(domain: str, request: DomainSave):
        from .domains import save_domain
        updated = save_domain(store, domain, request.data, request.expected_revision, request.actor)
        return {"project": updated.model_dump(mode="json")}

    @app.post("/api/domains/{domain}/run")
    def domain_run(domain: str, request: DomainRun):
        return jobs.submit_domain(domain, request.operation, request.input, request.expected_revision, request.actor)

    @app.post("/api/commands")
    def commands(command: Command):
        updated = store.apply(command)
        return {"project": updated.model_dump(mode="json"), "geometry_state": store.state()["geometry_state"]}

    @app.post("/api/import")
    async def upload(request: Request, expected_revision: int):
        # Stream a raw STEP body with a hard cap; never read an unbounded upload into memory.
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 50 * 1024 * 1024:
                raise HTTPException(413, "STEP imports are limited to 50 MiB")
        return store.import_step(bytes(content), request.headers.get("x-filename", "component.step"), expected_revision)

    @app.post("/api/jobs/{kind}")
    def submit(kind: str):
        if kind not in ("build", "check", "simulate", "render", "export"):
            raise HTTPException(404, "Unknown job kind")
        return jobs.submit(kind)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        try:
            return jobs.get(job_id)
        except FileNotFoundError:
            raise HTTPException(404, "Job not found")

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return jobs.cancel(job_id)

    @app.get("/api/artifacts/{relative:path}")
    def artifact(relative: str):
        try:
            path = store.artifact(relative)
            if relative.startswith("manufacturing/"):
                from .domains.manufacturing import validate_artifact
                try:
                    validate_artifact(store, relative)
                except ValueError as error:
                    raise HTTPException(409, str(error)) from error
            return FileResponse(path)
        except ValueError:
            raise HTTPException(404, "Artifact not found")

    @app.post("/api/context")
    async def context(request: Request):
        value = await request.json()
        if len(json.dumps(value)) > 10000:
            raise ValueError("Selection context is too large")
        write_json(store.cache / "context.json", value)
        return {"ok": True}

    @app.get("/api/events")
    async def events(request: Request):
        from .identity import Identity, principal_context
        captured = principal_context.get()
        async def stream():
            previous = None
            while not await request.is_disconnected():
                identity = Identity(store)
                if identity.enabled:
                    if not captured:
                        return
                    try:
                        identity.resolve(captured.id).require("read")
                    except PermissionError:
                        return
                elif captured:
                    return
                value = {"revision": store.load().revision, "jobs": jobs.list()}
                serialized = json.dumps(value)
                if serialized != previous:
                    yield f"data: {serialized}\n\n"
                    previous = serialized
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    from .native_routes import mount_native_routes
    mount_native_routes(app, store, jobs)
    # Workspace-owned routes are registered before the static studio mount.
    from .domains import catalog, module_for
    for item in catalog():
        if item["available"]:
            mount = getattr(module_for(item["id"]), "mount", None)
            if mount:
                mount(app, store, jobs)

    if windows:
        app.mount("/p", windows)
    from .joint_routes import mount as mount_joints
    mount_joints(app, store)

    frontend = Path(os.environ.get("O3D_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend/dist"))
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="studio")
    else:
        @app.get("/")
        def no_frontend():
            return {"message": "Build the frontend with npm run build, or start the Vite development server", "api_docs": "/docs"}
    return app
