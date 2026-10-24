"""Optional project-scoped credentials for HTTP access.

Local filesystem/CLI owners remain trusted administrators. Actor labels alone
never authenticate an HTTP client. Tokens are random, hashed at rest, revocable,
and scoped to the project that issued them. No hosted identity provider exists.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass

from .recovery import write_json

principal_context = contextvars.ContextVar("o3d_principal", default=None)
ROLE_ACTIONS = {
    "viewer": {"read"},
    "reader": {"read", "export", "comment"},
    "editor": {"read", "export", "comment", "edit"},
    "manager": {"read", "export", "comment", "edit", "review", "invite", "revoke"},
    "administrator": {"read", "export", "comment", "edit", "review", "invite", "revoke", "admin"},
}


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    role: str
    kind: str

    def require(self, action):
        if action not in ROLE_ACTIONS.get(self.role, set()):
            raise PermissionError(f"Role {self.role} cannot {action}")

    def public(self):
        return {"id": self.id, "name": self.name, "role": self.role, "kind": self.kind}


class Identity:
    def __init__(self, store):
        self.store = store
        self.path = store.cache / "identity.json"

    @property
    def enabled(self):
        return self.path.is_file()

    def _read(self):
        value = json.loads(self.path.read_text())
        if value.get("schema_version") != 1:
            raise ValueError("Unsupported identity store; original file retained")
        return value

    def _write(self, value):
        write_json(self.path, value)
        os.chmod(self.path, 0o600)

    def enable(self, name):
        with self.store.lock():
            if self.enabled:
                raise ValueError("Project authentication is already enabled")
            token, account = self._account(name, "administrator", "human")
            self._write({"schema_version": 1, "accounts": [account], "audit": [{"action": "enable", "principal": account["id"]}]})
            return {"token": token, "principal": self._public(account)}

    @staticmethod
    def _account(name, role, kind):
        if role not in ROLE_ACTIONS or kind not in {"human", "agent"}:
            raise ValueError("Unknown identity role or kind")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("Identity name must be 1–100 characters")
        token = secrets.token_urlsafe(40)
        account = {"id": str(uuid.uuid4()), "name": name.strip(), "role": role, "kind": kind,
                   "token_hash": hashlib.sha256(token.encode()).hexdigest(), "revoked": False}
        return token, account

    @staticmethod
    def _public(account):
        return {key: account[key] for key in ("id", "name", "role", "kind")}

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) > 200:
            raise PermissionError("A project credential is required")
        hashed = hashlib.sha256(token.encode()).hexdigest()
        for account in self._read()["accounts"]:
            if not account["revoked"] and hmac.compare_digest(hashed, account["token_hash"]):
                return Principal(**self._public(account))
        raise PermissionError("Project credential is invalid or revoked")

    def resolve(self, identity_id):
        for account in self._read()["accounts"]:
            if account["id"] == identity_id and not account["revoked"]:
                return Principal(**self._public(account))
        raise PermissionError("Project identity is revoked or missing")

    def accounts(self, principal):
        principal.require("invite")
        with self.store.lock():
            return [{**self._public(a), "revoked": a["revoked"]} for a in self._read()["accounts"]]

    def issue(self, principal, name, role, kind="human"):
        principal.require("invite")
        if role == "administrator":
            principal.require("admin")
        with self.store.lock():
            value = self._read()
            token, account = self._account(name, role, kind)
            value["accounts"].append(account)
            value["audit"].append({"action": "issue", "principal": principal.id, "target": account["id"], "role": role})
            self._write(value)
        return {"token": token, "principal": self._public(account)}

    def revoke(self, principal, target):
        principal.require("revoke")
        with self.store.lock():
            value = self._read()
            account = next((a for a in value["accounts"] if a["id"] == target), None)
            if account is None:
                raise ValueError("Identity does not exist")
            if account["role"] == "administrator":
                principal.require("admin")
                if len([a for a in value["accounts"] if a["role"] == "administrator" and not a["revoked"]]) <= 1:
                    raise ValueError("Cannot revoke the final administrator")
            account["revoked"] = True
            value["audit"].append({"action": "revoke", "principal": principal.id, "target": target})
            self._write(value)
        return {"revoked": target}


def action_for_request(method, path):
    if path.startswith("/api/artifacts/") or path.startswith("/api/platform/portable") or path == "/api/jobs/export":
        return "export"
    if path.startswith("/api/platform/identity"):
        return "revoke" if path.endswith("/revoke") else "invite"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if path == "/api/context":
        return "read"
    if path.startswith("/api/platform/reviews"):
        return "review"
    if path.startswith("/api/platform/comments"):
        return "comment"
    return "edit"


def effective_actor(requested="human"):
    principal = principal_context.get()
    return "agent" if principal and principal.kind == "agent" else requested


@contextmanager
def authorized(store, action):
    """Revalidate at the mutation boundary; caller holds Store.lock."""
    principal = principal_context.get()
    identity = Identity(store)
    if principal and identity.enabled:
        principal = identity.resolve(principal.id)
    if principal:
        principal.require(action)
    context = principal_context.set(principal)
    try:
        yield principal
    finally:
        principal_context.reset(context)
