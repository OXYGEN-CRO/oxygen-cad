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
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .jobs import Jobs
from .model import Command, Project
from .store import Conflict, Store, write_json


def create_app(root=None):
    store = Store(root or os.environ.get("O3D_PROJECT_DIR", "projects/demo"))
    jobs = Jobs(store)

    @asynccontextmanager
    async def lifespan(_):
        yield
        jobs.close()

    app = FastAPI(title="Oxygen CAD", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.jobs = store, jobs
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {"http://localhost:8787", "http://127.0.0.1:8787", "http://localhost:5173", "http://127.0.0.1:5173"}
        allowed.add(str(request.base_url).rstrip("/"))
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
            return FileResponse(store.artifact(relative))
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
        async def stream():
            previous = None
            while not await request.is_disconnected():
                value = {"revision": store.load().revision, "jobs": jobs.list()}
                serialized = json.dumps(value)
                if serialized != previous:
                    yield f"data: {serialized}\n\n"
                    previous = serialized
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    frontend = Path(os.environ.get("O3D_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend/dist"))
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="studio")
    else:
        @app.get("/")
        def no_frontend():
            return {"message": "Build the frontend with npm run build, or start the Vite development server", "api_docs": "/docs"}
    return app
