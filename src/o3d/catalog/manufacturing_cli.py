"""Agent JSON commands for sourced manufacturing and procurement discovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from .manufacturing_models import ManufacturingJob, ManufacturingSearch

app = typer.Typer(no_args_is_help=True, help="Find real providers, manufacturing materials and source-backed purchased parts.")


def database():
    from .cli import database as connect
    return connect()


def emit(value):
    typer.echo(json.dumps(value, indent=2, default=str, allow_nan=False))


@app.command("init")
def initialize():
    from .manufacturing import ManufacturingCatalog
    with database() as catalog:
        emit(ManufacturingCatalog(catalog).migrate())


@app.command("ingest")
def ingest(provider: str):
    """Refresh a public provider feed or the unofficial CDFER JLC component CSV."""
    from .manufacturing import ManufacturingCatalog
    if provider in {"sendcutsend", "laserhub"}:
        from .manufacturing_feeds import load_laserhub, load_sendcutsend
        bundle = (load_sendcutsend if provider == "sendcutsend" else load_laserhub)()
    elif provider == "ark":
        from .public_parts import load_ark
        bundle = load_ark()
    elif provider == "cdfer":
        import tempfile
        from .community_jlc import fetch_csv, load_cdfer_csv
        raw, metadata = fetch_csv()
        with tempfile.TemporaryDirectory(prefix="oxygen-jlc-community-") as folder:
            path = Path(folder) / "components.csv"
            path.write_bytes(raw)
            bundle = load_cdfer_csv(path, metadata)
    else:
        raise typer.BadParameter("Supported public feeds: sendcutsend, laserhub, ark, cdfer")
    with database() as catalog:
        emit(ManufacturingCatalog(catalog).ingest_bundle(bundle))


@app.command("import-evidence")
def import_evidence(root: Path, report: Path | None = None, dry_run: bool = False):
    """Validate primary captures and import eligible provider research bundles."""
    from .supply_evidence import normalize_evidence_tree
    plan = normalize_evidence_tree(root)
    result = {"normalization": plan["summary"], "quarantined": plan["quarantined"], "imports": [], "dry_run": dry_run}
    if not dry_run:
        from .manufacturing import ManufacturingCatalog
        with database() as catalog:
            store = ManufacturingCatalog(catalog)
            store.migrate()
            for bundle in plan["bundles"]:
                result["imports"].append(store.ingest_bundle(bundle, base_dir=root))
            result["database"] = store.status()
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, default=str, allow_nan=False) + "\n")
    emit(result)


@app.command("search")
def search(q: Annotated[str, typer.Argument()] = "", provider: str | None = None,
           process: str | None = None, material: str | None = None, record_type: str | None = None,
           limit: Annotated[int, typer.Option(min=1, max=100)] = 20, filters: Path | None = None):
    from .manufacturing import ManufacturingCatalog
    request = ManufacturingSearch.model_validate_json(filters.read_text()) if filters else ManufacturingSearch(
        q=q, provider=provider, process=process, material=material, record_type=record_type, limit=limit)
    with database() as catalog:
        emit(ManufacturingCatalog(catalog).search(request))


@app.command("get")
def get(record_id: UUID):
    from .manufacturing import ManufacturingCatalog
    with database() as catalog:
        emit(ManufacturingCatalog(catalog).get(str(record_id)))


@app.command("match")
def match(requirements: Path):
    """Compare a custom-part job to explicit sourced constraints; missing data stays unknown."""
    from .manufacturing import ManufacturingCatalog
    with database() as catalog:
        emit(ManufacturingCatalog(catalog).match(ManufacturingJob.model_validate_json(requirements.read_text())))


@app.command("status")
def status():
    from .manufacturing import ManufacturingCatalog
    with database() as catalog:
        emit(ManufacturingCatalog(catalog).status())


@app.command("jlc-status")
def jlc_status():
    from .jlcpcb import JLCPCBClient
    with JLCPCBClient.from_env() as client:
        emit(client.configuration_status())


@app.command("jlc-component")
def jlc_component(codes: Annotated[list[str], typer.Argument()]):
    """Ephemeral real official detail lookup, requiring approved JLCAPI credentials."""
    from .jlcpcb import JLCPCBClient, JLCPCBError
    try:
        with JLCPCBClient.from_env() as client:
            emit({"items": client.get_component_details(codes), "integration": client.configuration_status(), "persisted": False})
    except JLCPCBError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error


@app.command("jlc-library")
def jlc_library(page: Annotated[int, typer.Option(min=1)] = 1,
                page_size: Annotated[int, typer.Option(min=1, max=100)] = 10):
    """Read one official library page; approved Components API permission required."""
    from .jlcpcb import JLCPCBClient, JLCPCBError
    try:
        with JLCPCBClient.from_env() as client:
            emit({"response": client.get_component_library_page(page, page_size), "integration": client.configuration_status(), "persisted": False})
    except JLCPCBError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error


@app.command("jlc-feed")
def jlc_feed(last_key: str | None = None):
    """Read one official catalog batch; no automatic retry or order operation."""
    from .jlcpcb import JLCPCBClient, JLCPCBError
    try:
        with JLCPCBClient.from_env() as client:
            emit({"response": client.get_component_feed(last_key), "integration": client.configuration_status(), "persisted": False})
    except JLCPCBError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error


@app.command("ark-live")
def ark_live(sku: Annotated[str, typer.Argument()]):
    """Read a current ARK offer response without persisting it."""
    from .public_parts import live_ark_lookup
    emit(live_ark_lookup(sku))


components_app = typer.Typer(no_args_is_help=True, help="Query or refresh the full unofficial JLC component mirror.")
app.add_typer(components_app, name="components")


@components_app.command("import-snapshot")
def import_snapshot(path: Path, metadata: Path | None = None,
                    max_rows: Annotated[int | None, typer.Option(min=1)] = None,
                    batch_size: Annotated[int, typer.Option(min=1, max=10000)] = 2000):
    """Copy a validated local public SQLite snapshot into indexed Postgres metadata."""
    from .component_snapshot import ComponentSnapshot
    details = json.loads(metadata.read_text()) if metadata else None
    with database() as catalog:
        emit(ComponentSnapshot(catalog).import_sqlite(path, details, batch_size=batch_size, max_rows=max_rows))


@components_app.command("search")
def component_search(q: Annotated[str, typer.Argument()] = "", mpn: str | None = None,
                     manufacturer: str | None = None, package: str | None = None,
                     limit: Annotated[int, typer.Option(min=1, max=100)] = 20, filters: Path | None = None):
    from .component_snapshot import ComponentSearch, ComponentSnapshot
    request = ComponentSearch.model_validate_json(filters.read_text()) if filters else ComponentSearch(
        q=q, mpn=mpn, manufacturer=manufacturer, package=package, limit=limit)
    with database() as catalog:
        emit(ComponentSnapshot(catalog).search(request))


@components_app.command("get")
def component_get(identifier: str):
    from .component_snapshot import ComponentSnapshot
    with database() as catalog:
        emit(ComponentSnapshot(catalog).get(identifier))


@components_app.command("status")
def component_status():
    from .component_snapshot import ComponentSnapshot
    with database() as catalog:
        emit(ComponentSnapshot(catalog).status())


cad_app = typer.Typer(no_args_is_help=True, help="Find exact supplier SKU → CAD → purchase source chains and inspect selected files.")
app.add_typer(cad_app, name="cad")


@cad_app.command("search")
def cad_search(q: Annotated[str, typer.Argument()] = "", provider: str | None = None,
               sku: str | None = None, format: str | None = None, validated: bool = False,
               limit: Annotated[int, typer.Option(min=1, max=100)] = 20):
    from .vendor_cad import VendorCAD, VendorCADSearch
    values = {"q": q, "provider": provider, "sku": sku, "format": format, "limit": limit}
    if validated:
        values["geometry_validated"] = True
    with database() as catalog:
        emit(VendorCAD(catalog).search(VendorCADSearch(**values)))


@cad_app.command("inspect")
def cad_inspect(record_id: UUID, cad_url: str | None = None, expected_mm: str | None = None,
                tolerance_mm: Annotated[float, typer.Option(gt=0)] = 0.05, cad_python: Path | None = None):
    """Inspect one selected vendor CAD file locally; record geometry facts without an accuracy guarantee."""
    from .vendor_cad import VendorCAD
    expected = None
    if expected_mm:
        try:
            expected = [float(value) for value in expected_mm.split(",")]
            if len(expected) != 3 or any(value <= 0 for value in expected):
                raise ValueError()
        except ValueError as error:
            raise typer.BadParameter("expected-mm must contain three positive millimeter values, such as 15,15,420") from error
    with database() as catalog:
        emit(VendorCAD(catalog).inspect(str(record_id), cad_url=cad_url, expected_dimensions_mm=expected,
            tolerance_mm=tolerance_mm, cad_python=str(cad_python) if cad_python else None))
