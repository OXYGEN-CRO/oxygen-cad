from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response

from .db import Catalog
from .models import BOM, Search
from .manufacturing_models import ManufacturingJob, ManufacturingSearch
from .component_snapshot import ComponentSearch
from .vendor_cad import VendorCADSearch


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

    @app.get("/v1/manufacturing/status")
    def manufacturing_status():
        from .manufacturing import ManufacturingCatalog
        with Catalog(dsn) as catalog:
            return ManufacturingCatalog(catalog).status()

    @app.get("/v1/manufacturing/records")
    def manufacturing_records(q: str = Query("", max_length=500), provider: str | None = None,
                              process: str | None = None, material: str | None = None,
                              limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        from .manufacturing import ManufacturingCatalog
        with Catalog(dsn) as catalog:
            return ManufacturingCatalog(catalog).search(ManufacturingSearch(q=q, provider=provider,
                process=process, material=material, limit=limit, offset=offset))

    @app.post("/v1/manufacturing/search")
    def manufacturing_search(request: ManufacturingSearch):
        from .manufacturing import ManufacturingCatalog
        with Catalog(dsn) as catalog:
            return ManufacturingCatalog(catalog).search(request)

    @app.get("/v1/manufacturing/records/{record_id}")
    def manufacturing_detail(record_id: UUID):
        from .manufacturing import ManufacturingCatalog
        try:
            with Catalog(dsn) as catalog:
                return ManufacturingCatalog(catalog).get(str(record_id))
        except KeyError as error:
            raise HTTPException(404, "Manufacturing record not found") from error

    @app.post("/v1/manufacturing/match")
    def manufacturing_match(request: ManufacturingJob):
        from .manufacturing import ManufacturingCatalog
        with Catalog(dsn) as catalog:
            return ManufacturingCatalog(catalog).match(request)

    @app.get("/v1/integrations/jlcpcb/status")
    def jlcpcb_status():
        from .jlcpcb import JLCPCBClient, JLCPCBError
        try:
            with JLCPCBClient.from_env() as client:
                return client.configuration_status()
        except JLCPCBError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/v1/live/ark/{sku}")
    def ark_live(sku: str, response: Response):
        from .public_parts import live_ark_lookup
        response.headers["Cache-Control"] = "no-store"
        try:
            return live_ark_lookup(sku)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(502, "ARK live lookup failed; no cached offer was substituted") from error

    @app.get("/v1/components/status")
    def components_status():
        from .component_snapshot import ComponentSnapshot
        with Catalog(dsn) as catalog:
            return ComponentSnapshot(catalog).status()

    @app.get("/v1/components")
    def components(q: str = Query("", max_length=500), lcsc: str | None = None,
                   mpn: str | None = None, manufacturer: str | None = None, package: str | None = None,
                   limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
        from .component_snapshot import ComponentSnapshot
        with Catalog(dsn) as catalog:
            return ComponentSnapshot(catalog).search(ComponentSearch(q=q, lcsc=lcsc, mpn=mpn,
                manufacturer=manufacturer, package=package, limit=limit, offset=offset))

    @app.post("/v1/components/search")
    def component_search(request: ComponentSearch):
        from .component_snapshot import ComponentSnapshot
        with Catalog(dsn) as catalog:
            return ComponentSnapshot(catalog).search(request)

    @app.get("/v1/components/{identifier}")
    def component_detail(identifier: str):
        from .component_snapshot import ComponentSnapshot
        try:
            with Catalog(dsn) as catalog:
                return ComponentSnapshot(catalog).get(identifier)
        except KeyError as error:
            raise HTTPException(404, "Component not found") from error
        except ValueError as error:
            raise HTTPException(422, "Use a component UUID or LCSC C-code") from error

    @app.get("/v1/vendor-cad")
    def vendor_cad(q: str = Query("", max_length=500), provider: str | None = None,
                   sku: str | None = None, format: str | None = None, geometry_validated: bool = False,
                   limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
        from .vendor_cad import VendorCAD
        with Catalog(dsn) as catalog:
            return VendorCAD(catalog).search(VendorCADSearch(q=q, provider=provider, sku=sku,
                format=format, geometry_validated=geometry_validated, limit=limit, offset=offset))

    @app.post("/v1/vendor-cad/search")
    def vendor_cad_search(request: VendorCADSearch):
        from .vendor_cad import VendorCAD
        with Catalog(dsn) as catalog:
            return VendorCAD(catalog).search(request)

    return app
