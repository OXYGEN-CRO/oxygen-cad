from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import typer

from .model import Command, Project
from .store import Store

app = typer.Typer(no_args_is_help=True, help="Oxygen CAD: inspect, edit, build, verify and export a local CAD project.")

from .catalog.cli import app as parts_app
app.add_typer(parts_app, name="parts")


def emit(value, machine=True):
    typer.echo(json.dumps(value, indent=2, default=str, allow_nan=False))


@app.callback()
def main(ctx: typer.Context, project: Path = typer.Option(Path(os.environ.get("O3D_PROJECT_DIR", "projects/demo")), "--project", "-p")):
    ctx.obj = Store(project)


engine_app = typer.Typer(help="Discover and use the vendored Rust modelling engine.")
app.add_typer(engine_app, name="engine")


@engine_app.command("commands")
def engine_commands(json_output: bool = typer.Option(False, "--json")):
    from .native import NativeSession
    from .native_commands import PROJECT_COMMANDS
    with NativeSession() as engine:
        emit({"version": engine.execute("oxygen.version"), "commands": engine.execute("engine.commands"),
              "project_commands": sorted(PROJECT_COMMANDS)})


@engine_app.command("describe")
def engine_describe(command: str):
    from .native import NativeSession
    with NativeSession() as engine:
        emit(engine.execute("engine.describe", {"command": command}))


@engine_app.command("stdio")
def engine_stdio():
    """Raw local JSON-lines engine session; saved projects use the restricted command surface."""
    import subprocess
    from .native import engine_binary
    raise typer.Exit(subprocess.call([engine_binary()]))


@app.command()
def init(ctx: typer.Context, template: Path | None = None, json_output: bool = typer.Option(False, "--json")):
    if template:
        if str(template) in ("bracket", "cantilever"):
            template = Path(__file__).resolve().parents[2] / "examples" / f"{template}.json"
        project = Project.model_validate_json(template.read_text())
    else:
        example = Path(__file__).resolve().parents[2] / "examples/bracket.json"
        project = Project.model_validate_json(example.read_text()) if example.exists() else Project()
    emit(ctx.obj.init(project).model_dump(mode="json"))


@app.command()
def inspect(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    state = ctx.obj.state()
    for name in ("build", "last_build"):
        if state.get(name):
            value = dict(state[name])
            value["faces"] = [{k: v for k, v in face.items() if k not in ("vertices", "triangles")} for face in value["faces"]]
            value["references"] = [{"id": r["id"], "name": r["name"], "face_count": len(r["faces"])} for r in value["references"]]
            state[name] = value
    emit(state)


@app.command()
def context(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    emit(ctx.obj.state()["context"])


@app.command()
def apply(ctx: typer.Context, proposal: Path, expected_revision: int = typer.Option(...), actor: str = "agent", summary: str = "Agent revision", json_output: bool = typer.Option(False, "--json")):
    project = Project.model_validate_json(proposal.read_text())
    emit(ctx.obj.apply(Command(expected_revision=expected_revision, actor=actor, project=project, summary=summary)).model_dump(mode="json"))


@app.command("import-step")
def import_step(ctx: typer.Context, file: Path, expected_revision: int = typer.Option(...), json_output: bool = typer.Option(False, "--json")):
    emit(ctx.obj.import_step(file.read_bytes(), file.name, expected_revision).model_dump(mode="json"))


def operation(ctx, kind):
    from .worker import dispatch
    emit(dispatch(kind, ctx.obj, ctx.obj.load()))


@app.command()
def build(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    operation(ctx, "build")


@app.command()
def check(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    from .worker import dispatch
    result = dispatch("check", ctx.obj, ctx.obj.load())
    emit(result)
    if result["state"] != "passed":
        raise typer.Exit(2)


@app.command()
def studies(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    """List study capabilities and validate the current study without solving."""
    from .analysis import capabilities, validate_study
    project = ctx.obj.load() if (ctx.obj.root / "o3d.json").exists() else None
    emit({**capabilities(), "current_study": project.study.model_dump() if project else None,
          "setup_errors": validate_study(project) if project else []})


@app.command()
def simulate(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    from .worker import dispatch
    result = dispatch("simulate", ctx.obj, ctx.obj.load())
    emit(result)
    if result["state"] != "passed":
        raise typer.Exit(2)


@app.command()
def render(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    operation(ctx, "render")


@app.command("export")
def export(ctx: typer.Context, output: Path | None = None, json_output: bool = typer.Option(False, "--json")):
    from .artifacts import export_bundle
    result = export_bundle(ctx.obj, ctx.obj.load())
    if output:
        output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ctx.obj.artifact(result["artifact"]), output / "release.zip")
        result["output"] = str(output / "release.zip")
    emit(result)


@app.command()
def history(ctx: typer.Context, action: str = "list", revision: int | None = None, expected_revision: int | None = None, json_output: bool = typer.Option(False, "--json")):
    if action == "list":
        emit(ctx.obj.history())
    else:
        if expected_revision is None:
            raise typer.BadParameter("History changes require --expected-revision")
        emit(ctx.obj.apply(Command(expected_revision=expected_revision, actor="human", action=action, restore_revision=revision, summary=f"History {action}")).model_dump(mode="json"))


@app.command()
def doctor(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    import importlib.metadata
    from .solver import mesher_python, solver_binary
    result = {"python": os.sys.version.split()[0], "project_exists": (ctx.obj.root / "o3d.json").exists(),
              "build123d": importlib.metadata.version("build123d"), "mesher_python": mesher_python()}
    try:
        from .native import NativeSession
        with NativeSession() as engine:
            result["geometry_engine"] = engine.execute("oxygen.version")
        result["solver"] = solver_binary()
        result["ok"] = Path(mesher_python()).is_file()
    except RuntimeError as error:
        result["ok"], result["error"] = False, str(error)
    emit(result)
    if not result["ok"]:
        raise typer.Exit(2)


@app.command()
def studio(ctx: typer.Context, host: str = "127.0.0.1", port: int = 8787):
    if not (ctx.obj.root / "o3d.json").exists():
        example = Path(__file__).resolve().parents[2] / "examples/bracket.json"
        ctx.obj.init(Project.model_validate_json(example.read_text()))
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(ctx.obj.root), host=host, port=port)


if __name__ == "__main__":
    try:
        app()
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        typer.echo(json.dumps({"error": str(error)}), err=True)
        raise SystemExit(1)
