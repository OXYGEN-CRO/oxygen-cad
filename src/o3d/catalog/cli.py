from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from .models import BOM, Search

app = typer.Typer(no_args_is_help=True, help="Discover sourced hardware components, specifications and CAD. JSON output.")


def database():
    # The base CAD install does not need Postgres dependencies.
    try:
        from .db import Catalog
    except ImportError as error:
        raise typer.BadParameter("Install oxygen-cad[catalog] first") from error
    return Catalog()


def emit(value):
    typer.echo(json.dumps(value, indent=2, default=str, allow_nan=False))


@app.command("init")
def initialize():
    """Apply the versioned Postgres schema and register provider access policies."""
    from .connectors import register
    with database() as catalog:
        result = catalog.migrate()
        register(catalog)
        emit(result)


@app.command()
def ingest(provider: str, max_parts: Annotated[int | None, typer.Option(min=1)] = None,
           category: str | None = None, resume: UUID | None = None):
    """Capture and upsert a permitted catalog. Omit max-parts to ingest all."""
    from .connectors import ingest as run
    with database() as catalog:
        emit(run(catalog, provider, max_parts=max_parts, category=category, resume=str(resume) if resume else None))


@app.command()
def search(q: str = "", category: str | None = None, provider: str | None = None,
           cad: bool = False, downloadable: bool = False, hybrid: bool = False,
           limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
           filters: Path | None = None):
    """Search identifiers/keywords. A filters JSON file exposes all numeric and facet constraints."""
    request = Search.model_validate_json(filters.read_text()) if filters else Search(q=q, category=category,
        provider=provider, cad_required=cad, downloadable_cad=downloadable, mode="hybrid" if hybrid else "lexical", limit=limit)
    with database() as catalog:
        emit(catalog.search(request))


@app.command()
def get(part_id: UUID):
    with database() as catalog:
        emit(catalog.get(str(part_id)))


@app.command()
def status():
    with database() as catalog:
        emit(catalog.status())


@app.command()
def embed(limit: Annotated[int, typer.Option(min=1, max=100000)] = 500):
    """Build/rebuild missing local semantic vectors; first run downloads the model."""
    from .semantic import embed as run
    with database() as catalog:
        emit(run(catalog, limit=limit))


@app.command("fetch-asset")
def fetch_asset(asset_id: UUID, destination: Path):
    """Fetch a licensed, checksum-checked asset plus license and source sidecars."""
    from .assets import fetch_asset as run
    with database() as catalog:
        emit(run(catalog, str(asset_id), destination))


@app.command("import-part")
def import_part(asset_id: UUID, project: Path, expected_revision: int):
    """Import a sourced STEP reference through the existing CAD revision checks."""
    from .assets import import_part as run
    with database() as catalog:
        emit(run(catalog, str(asset_id), project, expected_revision))


@app.command("check-bom")
def check_bom(manifest: Path):
    """Label purchased/custom parts and report unresolved CAD/spec/fit/stock gaps."""
    with database() as catalog:
        emit(catalog.plan(BOM.model_validate_json(manifest.read_text())))


@app.command()
def serve(port: int = 8844, host: str = "127.0.0.1"):
    """Serve the discovery API on localhost. Interactive OpenAPI: /docs."""
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(), host=host, port=port)


def main():
    app()
