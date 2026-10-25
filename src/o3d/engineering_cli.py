"""Machine-readable engineering workflow commands, sharing the HTTP service path."""
from __future__ import annotations
import json
from pathlib import Path
import typer
from .engineering import execute

brief_app = typer.Typer(help="Edit an engineering brief and inspect or bind requirement evidence.")
compatibility_app = typer.Typer(help="Compare explicit component interfaces and generate editable adapters.")
procurement_app = typer.Typer(help="Evaluate a purchased/custom BOM with supplied transient offers.")
supplier_app = typer.Typer(help="Preflight sourced manufacturing rules and prepare a manual supplier handoff.")


def _read(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Engineering input exceeds 1 MiB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Engineering input must be an object")
    return value


def _emit(value):
    typer.echo(json.dumps(value, indent=2, allow_nan=False))


@brief_app.command("inspect")
def brief_inspect(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    _emit(execute(ctx.obj, "brief", "inspect", {}))


@brief_app.command("save")
def brief_save(ctx: typer.Context, input: Path, expected_revision: int = typer.Option(...), actor: str = "agent", json_output: bool = typer.Option(False, "--json")):
    _emit(execute(ctx.obj, "brief", "save", {"brief": _read(input), "expected_revision": expected_revision, "actor": actor}))


@brief_app.command("bind")
def brief_bind(ctx: typer.Context, expected_revision: int = typer.Option(...), actor: str = "agent", json_output: bool = typer.Option(False, "--json")):
    _emit(execute(ctx.obj, "brief", "bind", {"expected_revision": expected_revision, "actor": actor}))


@compatibility_app.command("evaluate")
def compatibility_evaluate(ctx: typer.Context, input: Path, json_output: bool = typer.Option(False, "--json")):
    _emit(execute(ctx.obj, "compatibility", "evaluate", _read(input)))


@compatibility_app.command("adapter")
def compatibility_adapter(ctx: typer.Context, input: Path, output: Path | None = None, json_output: bool = typer.Option(False, "--json")):
    result = execute(ctx.obj, "compatibility", "adapter", _read(input))
    if output:
        # An explicit new proposal file, never a replacement of the live source.
        with output.open("x") as file:
            json.dump(result["project"], file, indent=2, allow_nan=False)
            file.write("\n")
    _emit(result)


@procurement_app.command("inspect")
def procurement_inspect(ctx: typer.Context, input: Path, json_output: bool = typer.Option(False, "--json")):
    _emit(execute(ctx.obj, "procurement", "inspect", _read(input)))


@procurement_app.command("evaluate")
def procurement_evaluate(ctx: typer.Context, input: Path, json_output: bool = typer.Option(False, "--json")):
    _emit(execute(ctx.obj, "procurement", "evaluate", _read(input)))


def _supplier(ctx, operation, input):
    _emit(execute(ctx.obj, "supplier", operation, _read(input)))


@supplier_app.command("source")
def supplier_source(ctx: typer.Context, input: Path, json_output: bool = typer.Option(False, "--json")):
    if input.stat().st_size > 1024 * 1024:
        raise ValueError("Supplier source exceeds 1 MiB")
    _emit(execute(ctx.obj, "supplier", "source", {"content": input.read_text()}))


@supplier_app.command("evaluate")
def supplier_evaluate(ctx: typer.Context, input: Path, json_output: bool = typer.Option(False, "--json")):
    _supplier(ctx, "evaluate", input)


@supplier_app.command("package")
def supplier_package(ctx: typer.Context, input: Path, expected_revision: int = typer.Option(...), actor: str = "agent", json_output: bool = typer.Option(False, "--json")):
    data = _read(input)
    data.update(expected_revision=expected_revision, actor=actor)
    _emit(execute(ctx.obj, "supplier", "package", data))


@supplier_app.command("status")
def supplier_status(ctx: typer.Context, input: Path, json_output: bool = typer.Option(False, "--json")):
    _supplier(ctx, "status", input)


@supplier_app.command("response")
def supplier_response(ctx: typer.Context, input: Path, expected_revision: int = typer.Option(...), actor: str = "agent", json_output: bool = typer.Option(False, "--json")):
    data = _read(input)
    data.update(expected_revision=expected_revision, actor=actor)
    _emit(execute(ctx.obj, "supplier", "response", data))
