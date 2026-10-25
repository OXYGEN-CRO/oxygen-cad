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


def compact_native_scene(scene):
    """Drop only redundant mesh-face descriptors when indexed picking is complete.

    Positions, triangle order and native tri_face identities are retained. The
    current viewport recomputes its display normals/edges; their original values
    stay available through the default full-scene response.
    Exact B-rep faces and a mesh without an aligned face map stay untouched.
    """
    result = dict(scene)
    bodies = []
    for body in scene.get("bodies", []):
        mesh = body.get("mesh") or {}
        if (body.get("geometry_kind") == "mesh"
                and isinstance(mesh.get("positions"), list)
                and isinstance(mesh.get("triangles"), list)
                and isinstance(mesh.get("tri_face"), list)
                and len(mesh["tri_face"]) == len(mesh["triangles"])):
            body = dict(body)
            body["mesh"] = {key: value for key, value in mesh.items()
                            if key not in {"faces", "normals", "edges", "edge_faces", "seams"}}
        bodies.append(body)
    result["bodies"] = bodies
    return result


def mount_native_routes(app, store, jobs):
    @app.get("/api/native/scene")
    def native_scene(compact: bool = False):
        try:
            project = store.load()
            # Engine replies already contain JSON primitives. Do not walk every
            # triangle a second time in FastAPI's generic object encoder.
            scene = query_native(project, "oxygen.scene", {"mesh": True}, store)
            return JSONResponse({"revision": project.revision, "scene": compact_native_scene(scene) if compact else scene})
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
