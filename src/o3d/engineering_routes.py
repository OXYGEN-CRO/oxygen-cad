"""Local, bounded engineering HTTP routes; all edits retain Store revision checks."""
from __future__ import annotations

import json
from importlib.resources import files
from fastapi import HTTPException, Request
from .engineering import execute

MAX_INPUT_BYTES = 1024 * 1024


async def _input(request):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_INPUT_BYTES:
            raise HTTPException(413, "Engineering input exceeds 1 MiB")
    value = json.loads(content or b"{}")
    if not isinstance(value, dict):
        raise ValueError("Engineering input must be an object")
    return value


def mount_engineering(app, store):
    @app.get("/api/engineering/brief")
    def inspect_brief():
        return execute(store, "brief", "inspect", {})

    @app.get("/api/engineering/examples/{feature}")
    def example(feature: str):
        names = {"brief": "engineering-brief.json", "interfaces": "component-interfaces.json",
                 "procurement": "procurement-bom.json", "supplier": "supplier-request.json"}
        if feature not in names:
            raise HTTPException(404, "Unknown engineering example")
        value = json.loads(files("o3d").joinpath("data", names[feature]).read_text(encoding="utf-8"))
        return value["engineering_brief"] if feature == "brief" else value

    @app.post("/api/engineering/{feature}/{operation}")
    async def engineering_operation(feature: str, operation: str, request: Request):
        # Keep CPU/file-heavy adapters off the event loop.
        from starlette.concurrency import run_in_threadpool
        payload = await _input(request)
        try:
            return await run_in_threadpool(execute, store, feature, operation, payload)
        except KeyError as error:
            raise ValueError(f"Missing engineering field: {error.args[0]}") from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
