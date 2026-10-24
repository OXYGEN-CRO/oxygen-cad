"""Versioned local platform operations and optional authenticated HTTP routes."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from ..collaboration import Collaboration
from ..identity import Identity, Principal, action_for_request, principal_context
from ..projects import ProjectCatalog, contained, portable_archive, project_metadata, restore_archive
from ..recovery import write_json


class PlatformData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    description: str = ""


def validate(data) -> dict:
    return PlatformData.model_validate(data).model_dump(mode="json")


def status(store):
    with store.lock():
        project = store._load()
        metadata = project_metadata(store)
    recovery = store.cache / "recovery/last-report.json"
    identity = Identity(store)
    return {"schema_version": 1, "project": metadata, "directory": store.root.name,
            "revision": project.revision, "history_count": len(store.history()),
            "mode": "authenticated-project" if identity.enabled else "trusted-local",
            "offline": {"document": True, "assets": True, "history": True, "cloud_sync": False,
                        "runtime": "Installed CAD/solver runtime required; first setup may require network"},
            "recovery": json.loads(recovery.read_text()) if recovery.exists() else None,
            "review": Collaboration(store).inspect(),
            "limits": ["Credentials protect HTTP access; filesystem owners and local CLI remain trusted administrators",
                       "No cloud sync, public share-link service, plugin sandbox or MCP endpoint is provided",
                       "Archive includes saved history and dependencies; unsaved drafts and credentials are excluded"]}


def _draft_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Invalid draft ID")
    principal = principal_context.get()
    return f"{principal.id if principal else 'local'}-{value}"


def execute(store, project, operation, payload, cancel=None):
    payload = payload or {}
    if cancel and cancel():
        raise ValueError("Platform operation cancelled")
    if operation == "status":
        return status(store)
    if operation == "checkpoint":
        return Collaboration(store).record("checkpoints", payload.get("revision", project.revision), payload.get("text", ""))
    if operation == "compare":
        return Collaboration(store).compare(int(payload["before"]), int(payload["after"]))
    if operation == "reconcile":
        return Collaboration(store).reconcile(int(payload["base_revision"]), payload["project"], payload.get("resolutions"))
    if operation == "portable":
        result = portable_archive(store, include_results=bool(payload.get("include_results", True)))
        return {"artifact": str(Path(result["path"]).relative_to(store.cache)), "manifest": result["manifest"]}
    if operation == "recover":
        return store.recover()
    if operation == "draft_save":
        from ..model import Project
        candidate = Project.model_validate(payload["project"])
        value = {"schema_version": 1, "base_revision": int(payload["base_revision"]), "project": candidate.model_dump(mode="json")}
        with store.lock():
            path = store.cache / "drafts" / f"{_draft_id(payload['id'])}.json"
            write_json(path, value)
        return {"saved": True, "base_revision": value["base_revision"], "id": payload["id"]}
    if operation in {"draft_load", "draft_discard"}:
        with store.lock():
            path = store.cache / "drafts" / f"{_draft_id(payload['id'])}.json"
            if operation == "draft_discard":
                path.unlink(missing_ok=True)
                return {"discarded": True}
            return json.loads(path.read_text()) if path.exists() else {"project": None}
    raise ValueError(f"Unsupported platform operation: {operation}")


def mount(app, store, jobs):
    """Mount before the static frontend route. Does not change the active project."""
    from fastapi import Request, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from ..model import Command, Project
    identity = Identity(store)
    catalog = ProjectCatalog(store.root.parent)
    cookie_name = "o3d_session_" + hashlib.sha256(str(store.root).encode()).hexdigest()[:12]

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        path = request.scope.get("path", request.url.path)
        root_path = request.scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path):]
        if not path.startswith("/api/") or path in {"/api/platform/session", "/api/platform/identity/enable"} or not identity.enabled:
            return await call_next(request)
        token = request.headers.get("authorization", "")
        token = token[7:] if token.lower().startswith("bearer ") else request.cookies.get(cookie_name, "")
        try:
            principal = identity.authenticate(token)
            principal.require(action_for_request(request.method, path))
        except PermissionError as error:
            return JSONResponse({"detail": str(error)}, status_code=403 if token else 401)
        request.state.principal = principal
        context = principal_context.set(principal)
        try:
            return await call_next(request)
        finally:
            principal_context.reset(context)

    def current_principal():
        return principal_context.get() or Principal("local", "Local filesystem user", "administrator", "human")

    def session_response(result):
        response = JSONResponse(result)
        response.set_cookie(cookie_name, result["token"], httponly=True, samesite="strict", max_age=12 * 3600)
        return response

    @app.get("/api/platform/status")
    def inspect_platform():
        return status(store)

    @app.post("/api/platform/identity/enable")
    async def enable(request: Request):
        if identity.enabled:
            raise HTTPException(409, "Project authentication is already enabled")
        result = identity.enable((await request.json()).get("name", "Project owner"))
        return session_response(result)

    @app.post("/api/platform/session")
    async def login(request: Request):
        if not identity.enabled:
            return {"mode": "trusted-local"}
        try:
            token = (await request.json()).get("token", "")
            principal = identity.authenticate(token)
        except PermissionError as error:
            raise HTTPException(401, str(error))
        response = JSONResponse({"principal": principal.public()})
        response.set_cookie(cookie_name, token, httponly=True, samesite="strict", max_age=12 * 3600)
        return response

    @app.delete("/api/platform/session")
    def logout():
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(cookie_name)
        return response

    @app.get("/api/platform/identity")
    def accounts():
        return {"accounts": identity.accounts(current_principal()) if identity.enabled else [], "enabled": identity.enabled}

    @app.post("/api/platform/identity/issue")
    async def issue(request: Request):
        value = await request.json()
        return identity.issue(current_principal(), value["name"], value["role"], value.get("kind", "human"))

    @app.post("/api/platform/identity/revoke")
    async def revoke(request: Request):
        return identity.revoke(current_principal(), (await request.json())["id"])

    @app.post("/api/platform/commands")
    async def command(request: Request):
        value = await request.json()
        updated = store.apply(Command.model_validate(value["command"]), request_id=value.get("request_id"))
        return {"project": updated.model_dump(mode="json")}

    @app.get("/api/platform/portable")
    def backup():
        result = portable_archive(store)
        return FileResponse(result["path"], filename=f"{store.root.name}-r{result['revision']}.oxygen.zip")

    @app.post("/api/platform/restore")
    async def restore(request: Request, name: str):
        if identity.enabled:
            raise HTTPException(403, "Restore into the local catalog requires trusted local mode")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 512 * 1024 * 1024:
                raise HTTPException(413, "Archive exceeds 512 MiB")
        return restore_archive(bytes(content), catalog.store(name).root)

    @app.post("/api/platform/operation/{operation}")
    async def operation(operation: str, request: Request):
        value = await request.json()
        # Review authorization must be checked by operation as well as URL.
        principal = current_principal()
        principal.require("export" if operation == "portable" else "read" if operation in {"status", "compare", "draft_load"} else "edit")
        return execute(store, store.load(), operation, value)

    @app.get("/api/platform/projects")
    def projects(query: str = "", archived: bool = False):
        if identity.enabled:
            raise HTTPException(403, "Cross-project catalog requires trusted local mode; credentials are project-scoped")
        return {"projects": catalog.list(query=query, archived=archived), "current": store.root.name}

    @app.post("/api/platform/projects/{name}/{operation}")
    async def project_operation(name: str, operation: str, request: Request):
        if identity.enabled:
            raise HTTPException(403, "Cross-project catalog requires trusted local mode; credentials are project-scoped")
        value = await request.json()
        target = catalog.store(name)
        if Identity(target).enabled:
            raise HTTPException(403, "Authenticate in the target project to manage it")
        if operation == "create":
            target = catalog.create(name, Project.model_validate(value.get("project", {"name": value.get("name", name)})))
        elif operation == "duplicate":
            if Identity(catalog.store(value["source"])).enabled:
                raise HTTPException(403, "Authenticated source cannot be duplicated from another project")
            target = catalog.duplicate(value["source"], name)
        elif operation == "archive":
            return catalog.archive(name, value.get("archived", True))
        elif operation == "rename":
            project = target.load().model_copy(update={"name": value["name"]})
            target.apply(Command(expected_revision=int(value["expected_revision"]), actor="human", project=project, summary="Rename project"))
        elif operation != "inspect":
            raise HTTPException(404, "Unsupported project operation")
        return {"project": target.load().model_dump(mode="json"), "directory": name,
                "open": {"requires_project_scope": True, "directory": name, "url": f"/p/{name}/"}}

    @app.get("/api/platform/reviews")
    def reviews():
        return Collaboration(store).inspect()

    @app.post("/api/platform/reviews")
    async def review(request: Request):
        value = await request.json()
        current_principal().require("review")
        return Collaboration(store).record("reviews", value["revision"], value["text"], value.get("anchor"), value.get("decision", "approved"))

    @app.post("/api/platform/comments")
    async def comment(request: Request):
        value = await request.json()
        current_principal().require("comment")
        return Collaboration(store).record("comments", value["revision"], value["text"], value.get("anchor"))
