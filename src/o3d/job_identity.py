"""Restore a job's server-captured identity without persisting bearer secrets."""
from contextlib import contextmanager

from .identity import Identity, Principal, principal_context


@contextmanager
def job_identity(store, captured):
    identity = Identity(store)
    principal = None
    if identity.enabled:
        if not isinstance(captured, dict):
            raise PermissionError("Project authentication changed; resubmit this operation")
        account = next((a for a in identity._read()["accounts"]
                        if a["id"] == captured.get("id") and not a["revoked"]), None)
        if account is None:
            raise PermissionError("The operation's project credential was revoked")
        principal = Principal(**identity._public(account))
        principal.require("edit")
    elif captured is not None:
        raise PermissionError("Project authentication changed; resubmit this operation")
    token = principal_context.set(principal)
    try:
        yield principal
    finally:
        principal_context.reset(token)
