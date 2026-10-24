from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, HTTPException, Query

from .db import Catalog
from .models import BOM, Search


def create_app(dsn: str | None = None):
    app = FastAPI(title="Oxygen Parts Catalog", version="0.1.0",
        description="Agent component discovery. Numeric constraints are hard filters; fit and live stock remain explicit unknowns.")

    @app.get("/health")
    def health():
        with Catalog(dsn) as catalog:
            catalog.conn.execute("SELECT 1 FROM parts LIMIT 1")
        return {"ok": True}

    @app.get("/v1/status")
    def status():
        with Catalog(dsn) as catalog:
            return catalog.status()

    @app.get("/v1/providers")
    def providers():
        with Catalog(dsn) as catalog:
            return catalog.conn.execute("SELECT * FROM providers ORDER BY id").fetchall()

    @app.get("/v1/parts")
    def parts(q: str = Query("", max_length=500), category: str | None = None,
              provider: str | None = None, cad_required: bool = False,
              limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        with Catalog(dsn) as catalog:
            return catalog.search(Search(q=q, category=category, provider=provider,
                cad_required=cad_required, limit=limit, offset=offset))

    @app.post("/v1/search")
    def search(request: Search):
        try:
            with Catalog(dsn) as catalog:
                return catalog.search(request)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/v1/parts/{part_id}")
    def detail(part_id: UUID):
        try:
            with Catalog(dsn) as catalog:
                return catalog.get(str(part_id))
        except KeyError as error:
            raise HTTPException(404, "Part not found") from error

    @app.post("/v1/bom/check")
    def bom_check(bom: BOM):
        try:
            with Catalog(dsn) as catalog:
                return catalog.plan(bom)
        except (KeyError, ValueError) as error:
            raise HTTPException(400, "BOM contains an unknown or malformed part ID") from error

    return app
