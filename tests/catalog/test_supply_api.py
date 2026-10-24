import hashlib
import json
import os

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from o3d.catalog.api import create_app
from o3d.catalog.cli import app
from o3d.catalog.manufacturing import ManufacturingCatalog


def seed(catalog):
    store = ManufacturingCatalog(catalog)
    store.migrate()
    catalog.conn.execute("TRUNCATE manufacturing_providers CASCADE")
    content = "Source: CNC, aluminum, maximum X 100 mm; actual order is reviewed."
    report = store.ingest_bundle({"task": "api-fixture", "provider": "fixture-cnc",
        "sources": [{"url": "https://example.com/cnc", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "status": 200, "captured_at": "2026-10-24T12:00:00Z"}],
        "records": [{"external_id": "cnc-aluminum", "record_type": "capability", "name": "Aluminum CNC",
            "process": "CNC", "material": "aluminum", "source_url": "https://example.com/cnc",
            "order_path": "https://example.com/quote", "facts": {},
            "constraints": [{"key": "max_x", "value": 100, "unit": "mm", "source_field": "maximum X"}]}]})
    assert report["records"] == 1
    return store


def test_manufacturing_api_and_hard_filter(catalog):
    seed(catalog)
    client = TestClient(create_app(os.environ["O3D_CATALOG_TEST_DATABASE_URL"]))
    result = client.get("/v1/manufacturing/records", params={"q": "Aluminum"})
    assert result.status_code == 200 and result.json()["total"] == 1
    record = result.json()["items"][0]
    assert client.get(f"/v1/manufacturing/records/{record['id']}").json()["order_path"] == "https://example.com/quote"
    assert client.get("/v1/manufacturing/status").json()["manufacturing_records"] == 1
    assert client.post("/v1/manufacturing/search", json={"numeric": [{"key": "max_x", "unit": "mm", "min": 200}]}).json()["total"] == 0
    match = client.post("/v1/manufacturing/match", json={"process": "CNC", "dimensions": {"x": 200}}).json()
    assert match["items"][0]["state"] == "rejected"
    assert record["manufacturability"]["state"] == "unverified"
    assert client.get("/v1/manufacturing/records/not-a-uuid").status_code == 422


def test_supply_cli_and_credential_status(catalog, monkeypatch):
    seed(catalog)
    monkeypatch.setenv("O3D_CATALOG_DATABASE_URL", os.environ["O3D_CATALOG_TEST_DATABASE_URL"])
    for key in ("JLCPCB_APP_ID", "JLCPCB_ACCESS_KEY", "JLCPCB_SECRET_KEY", "JLCPCB_API_KEY", "JLCPCB_API_SECRET"):
        monkeypatch.delenv(key, raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["supply", "search", "Aluminum", "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total"] == 1
    status = runner.invoke(app, ["supply", "jlc-status"])
    assert status.exit_code == 0
    assert not json.loads(status.output)["configured"]
    assert not json.loads(status.output)["real_authenticated_verified"]
    assert runner.invoke(app, ["supply", "jlc-component", "C25804"]).exit_code == 2


def test_live_lookup_is_ephemeral_and_never_substitutes_cached_data(monkeypatch):
    import o3d.catalog.public_parts as public
    monkeypatch.setattr(public, "live_ark_lookup", lambda sku: {"sku": sku, "ephemeral": True, "observed_at": "2026-10-24T12:00:00Z"})
    client = TestClient(create_app())
    response = client.get("/v1/live/ark/ARK-FLOW")
    assert response.status_code == 200 and response.json()["ephemeral"]
    assert response.headers["Cache-Control"] == "no-store"
    def fail(sku):
        raise ValueError("Unavailable")
    monkeypatch.setattr(public, "live_ark_lookup", fail)
    assert client.get("/v1/live/ark/ARK-FLOW").status_code == 502
