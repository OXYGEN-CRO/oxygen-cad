"""HTTP adapters for the shared native project command path."""
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .native_document import execute_native, inspect_native, query_native
from .store import Conflict


class NativeCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    actor: Literal["human", "agent"] = "human"
    command: str = Field(min_length=1, max_length=128)
    params: dict = Field(default_factory=dict)


def mount_native_routes(app, store, jobs):
    @app.get("/api/native/scene")
    def native_scene():
        try:
            project = store.load()
            # Engine replies already contain JSON primitives. Do not walk every
            # triangle a second time in FastAPI's generic object encoder.
            return JSONResponse({"revision": project.revision, "scene": query_native(project, "oxygen.scene", {"mesh": True}, store)})
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/native/inspect")
    def native_inspect():
        try:
            return inspect_native(store.load(), store)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/native/execute")
    def native_execute(request: NativeCommandRequest):
        try:
            response = execute_native(store, request.expected_revision, request.actor, request.command, request.params)
            return {"project": response["project"].model_dump(mode="json"), "result": response["result"]}
        except Conflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
