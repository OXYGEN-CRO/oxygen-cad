"""Killable CAD worker. Child solver processes receive cooperative cancellation."""
import json
import signal
import sys
from pathlib import Path
from threading import Event

from .model import Project
from .store import Store, write_json


def dispatch(kind, store, project, cancel=None, workspace=None):
    if kind == "domain":
        from .domains import execute
        from .job_identity import job_identity
        if not isinstance(workspace, dict):
            raise ValueError("Workspace operation input is required")
        with job_identity(store, workspace.get("principal")):
            return execute(store, project, workspace["domain"], workspace["operation"],
                           workspace.get("input", {}), cancel, actor=workspace.get("actor", "agent"))
    if kind == "build":
        from .geometry import build
        return build(store, project)
    if kind == "check":
        from .geometry import check
        return check(store, project)
    if kind == "simulate":
        from .solver import simulate
        return simulate(store, project, cancel)
    if kind == "render":
        from .artifacts import render
        return render(store, project)
    if kind == "export":
        from .artifacts import export_bundle
        return export_bundle(store, project)
    raise ValueError(f"Unknown operation {kind}")


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text())
    cancel = Event()
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())
    result = dispatch(request["kind"], Store(request["root"]), Project.model_validate(request["project"]), cancel, request.get("workspace"))
    write_json(Path(sys.argv[2]), result)
