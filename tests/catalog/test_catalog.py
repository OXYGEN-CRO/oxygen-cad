import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from o3d.catalog.api import create_app
from o3d.catalog.assets import fetch_asset
from o3d.catalog.connectors import adafruit_parts, ingest, specifications, step_part
from o3d.catalog.db import EMBEDDING_MODEL, stable_id
from o3d.catalog.models import Asset, BOM, BOMItem, NumericFilter, Part, Provider, Search, Spec
from o3d.catalog.objects import Objects


def insert(catalog, external_id="motor-a", **kwargs):
    doc = catalog.document("test", "https://example.com/parts", b'{"captured":true}')
    values = dict(external_id=external_id, name="Small servo motor", category="actuator",
        manufacturer="Fixture Motors", mpn=external_id, procurement="off_the_shelf",
        specs=[Spec(key="torque", value=5.0, unit="N*m", source_field="torqueNm")])
    values.update(kwargs)
    return catalog.upsert("test", Part(**values), doc)


def test_identity_and_provenance_survive_reingestion(catalog):
    first = insert(catalog)
    second = insert(catalog, name="Updated servo motor")
    assert first == second
    assert catalog.status()["parts"] == 1
    row = catalog.get(first)
    assert row["name"] == "Updated servo motor"
    assert row["specs"][0]["source_field"] == "torqueNm"
    assert catalog.objects.read(row["provenance"]["object_key"]) == b'{"captured":true}'
    assert row["availability"]["state"] == "unknown"


def test_unit_constraints_and_missing_values_are_hard_filters(catalog):
    good = insert(catalog, "strong")
    insert(catalog, "weak", specs=[Spec(key="torque", value=1, unit="N*m", source_field="torqueNm")])
    insert(catalog, "wrong-units", specs=[Spec(key="torque", value=500, unit="mN*m", source_field="torque")])
    insert(catalog, "missing", specs=[])
    result = catalog.search(Search(q="servo", numeric=[NumericFilter(key="torque", unit="N*m", min=3)]))
    assert [str(p["id"]) for p in result["items"]] == [good]


def test_exact_mpn_ranks_first_and_injection_is_text(catalog):
    exact = insert(catalog, "MX-100", name="Small actuator")
    insert(catalog, "similar", name="MX-100 compatible motor")
    assert str(catalog.search(Search(q="MX-100"))["items"][0]["id"]) == exact
    assert catalog.search(Search(q="'); DROP TABLE parts;--"))["total"] == 0
    assert catalog.status()["parts"] == 2


def test_numeric_key_injection_does_not_change_filters(catalog):
    insert(catalog)
    result = catalog.search(Search(numeric=[NumericFilter(key="x' OR true--", unit="mm", min=0)]))
    assert result["total"] == 0


def test_pagination_keeps_total_beyond_last_page(catalog):
    insert(catalog)
    result = catalog.search(Search(q="servo", offset=50))
    assert result["items"] == [] and result["total"] == 1


def test_hybrid_uses_semantics_but_still_obeys_physics_constraints(catalog):
    target = insert(catalog, "quiet", name="Rotary joint drive")
    wrong = insert(catalog, "loud", name="Rotary joint drive", specs=[Spec(key="torque", value=1, unit="N*m", source_field="torque")])
    vector = [1.0] + [0.0] * 383
    for part_id in (target, wrong):
        text = catalog.conn.execute("SELECT search_text FROM parts WHERE id=%s", (part_id,)).fetchone()["search_text"]
        catalog.conn.execute("INSERT INTO embeddings(part_id,model,content_sha256,embedding) VALUES (%s,%s,%s,%s::vector)",
            (part_id, EMBEDDING_MODEL, hashlib.sha256(text.encode()).hexdigest(), json.dumps(vector)))
    result = catalog.search(Search(q="silent robot elbow", mode="hybrid",
        numeric=[NumericFilter(key="torque", unit="N*m", min=3)]), query_vector=vector)
    assert [str(p["id"]) for p in result["items"]] == [target]
    assert result["items"][0]["semantic_rank"] == 1
    insert(catalog, "quiet", name="Changed joint")
    assert catalog.conn.execute("SELECT count(*) AS n FROM embeddings WHERE part_id=%s", (target,)).fetchone()["n"] == 0


def test_licensed_cad_filter_excludes_unknown_permissions(catalog):
    licensed = Asset(url="https://example.com/a.step", format="step", license="MIT",
        license_url="https://example.com/license", download_allowed=True)
    permitted = insert(catalog, "permitted", assets=[licensed])
    unknown = insert(catalog, "unknown", assets=[Asset(url="https://example.com/b.step", format="step")])
    assert catalog.search(Search(cad_required=True))["total"] == 2
    assert [str(r["id"]) for r in catalog.search(Search(downloadable_cad=True))["items"]] == [permitted]
    # Removed upstream files are retained for audit but excluded from retrieval.
    insert(catalog, "permitted", assets=[])
    assert catalog.search(Search(downloadable_cad=True))["total"] == 0
    assert catalog.get(unknown)["assets"]


def test_live_only_provider_cannot_be_stored(catalog):
    catalog.register_provider(Provider(id="live", name="Live Only", website="https://example.com",
        access="live-only", license="restricted", terms_url="https://example.com/terms"))
    with pytest.raises(ValueError, match="not approved"):
        catalog.document("live", "https://example.com", b"{}")


def test_document_cannot_be_attributed_to_another_provider(catalog):
    doc = catalog.document("test", "https://example.com", b"{}")
    catalog.register_provider(Provider(id="other", name="Other", website="https://example.com", access="stored",
        license="MIT", terms_url="https://example.com/license"))
    with pytest.raises(ValueError, match="belong"):
        catalog.upsert("other", Part(external_id="x", name="x"), doc)


def test_bom_labels_custom_parts_and_reports_unknowns(catalog):
    part_id = insert(catalog)
    plan = catalog.plan(BOM(name="Robot", items=[BOMItem(label="Elbow", kind="off_the_shelf", part_id=part_id,
        requirements=[NumericFilter(key="torque", unit="N*m", min=10)]), BOMItem(label="Bracket", kind="custom")]))
    assert "requirement_failed:torque" in plan["items"][0]["gaps"]
    assert "mounting_and_interfaces_missing" in plan["items"][0]["gaps"]
    assert plan["items"][1]["gaps"] == ["design_and_manufacture_required"]
    assert not plan["ready_to_build"]


class BytesFetcher:
    def __init__(self, data):
        self.data = data

    def get(self, url, **kwargs):
        return b"MIT License\nPermission is hereby granted" if url.endswith("LICENSE") else self.data


def test_asset_checksum_license_sidecars_and_tamper_rejection(catalog, tmp_path):
    data = b"ISO-10303-21;\nHEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;"
    part_id = insert(catalog, assets=[Asset(url="https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/" + "a" * 40 + "/test.step",
        format="step", sha256=hashlib.sha256(data).hexdigest(), license="MIT", download_allowed=True,
        license_url="https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/" + "a" * 40 + "/LICENSE")])
    asset_id = str(catalog.get(part_id)["assets"][0]["id"])
    with pytest.raises(ValueError, match="checksum"):
        fetch_asset(catalog, asset_id, tmp_path / "bad", BytesFetcher(data + b"corrupted"))
    result = fetch_asset(catalog, asset_id, tmp_path / "good", BytesFetcher(data))
    path = Path(result["path"])
    assert path.read_bytes() == data
    assert path.with_name(path.name + ".LICENSE.txt").exists()
    assert json.loads(path.with_name(path.name + ".catalog.json").read_text())["part_id"] == part_id
    # The cached object is verified on reads too.
    asset = catalog.conn.execute("SELECT object_key FROM assets WHERE id=%s", (asset_id,)).fetchone()
    (catalog.objects.root / asset["object_key"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        fetch_asset(catalog, asset_id, tmp_path / "cached", BytesFetcher(data))


def test_api_discovery_validation_and_missing_part(catalog):
    part_id = insert(catalog)
    client = TestClient(create_app(os.environ["O3D_CATALOG_TEST_DATABASE_URL"]))
    assert client.get("/health").status_code == 200
    assert client.get("/v1/parts", params={"q": "servo"}).json()["total"] == 1
    assert client.get(f"/v1/parts/{part_id}").json()["mpn"] == "motor-a"
    assert client.get("/v1/parts/not-a-uuid").status_code == 422
    assert client.get(f"/v1/parts/{stable_id('absent')}").status_code == 404
    assert client.post("/v1/search", json={"limit": 100000}).status_code == 422
    assert client.post("/v1/search", json={"numeric": [{"key": "torque", "unit": "N*m", "min": 10}]}).json()["total"] == 0
    assert "/v1/bom/check" in client.get("/openapi.json").json()["paths"]


def test_cli_search_accepts_documented_positional_query(catalog, monkeypatch):
    from o3d.catalog.cli import app
    monkeypatch.setenv("O3D_CATALOG_DATABASE_URL", os.environ["O3D_CATALOG_TEST_DATABASE_URL"])
    insert(catalog)
    result = CliRunner().invoke(app, ["search", "servo", "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total"] == 1


def test_failed_page_resume_replays_idempotently(catalog):
    class PageFetcher:
        fail = True

        def get(self, url, **kwargs):
            from urllib.parse import parse_qs, urlsplit
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            if page == 2 and self.fail:
                raise ValueError("Temporary upstream failure")
            return json.dumps({"page": page, "items": [{"id": f"p{page}", "name": f"Part {page}",
                "stepUrl": "https://example.com/file.step"}], "hasNextPage": page == 1}).encode()
    fetcher = PageFetcher()
    with pytest.raises(ValueError, match="Temporary"):
        ingest(catalog, "step-parts", fetcher=fetcher)
    old = catalog.status()["runs"][0]
    assert old["status"] == "failed" and old["checkpoint"]["page"] == 2
    fetcher.fail = False
    ingest(catalog, "step-parts", resume=str(old["id"]), fetcher=fetcher)
    assert catalog.status()["parts"] == 2
    ingest(catalog, "step-parts", fetcher=fetcher)
    assert catalog.status()["parts"] == 2


def test_normalization_never_invents_units_or_ports():
    specs = {s.key: s for s in specifications({"lengthMm": 12, "massG": 25, "torqueNm": 2, "voltage": 12, "sealed": True})}
    assert specs["length"].unit == "mm"
    assert specs["mass"].value == .025 and specs["mass"].evidence == "derived"
    assert specs["torque"].unit == "N*m"
    assert specs["voltage"].unit is None
    part = step_part({"id": "x", "name": "Motor", "stepUrl": "https://example.com/a.step"})
    assert part.ports == [] and not part.assets[0].download_allowed


def test_adafruit_tree_pins_assets_and_preserves_model_scope():
    tree = {"sha": "a" * 40, "tree": [{"path": "4884 Feather RP2040/board.step", "type": "blob", "size": 100}]}
    part = adafruit_parts(tree)[0]
    assert part.supplier_sku == "4884"
    assert part.manufacturer is None
    assert part.assets[0].scope == "unverified"
    assert "a" * 40 in part.assets[0].url
    assert part.assets[0].license == "MIT"
    with pytest.raises(ValueError, match="truncated"):
        adafruit_parts({**tree, "truncated": True})


def test_object_store_rejects_path_traversal(tmp_path):
    store = Objects(tmp_path)
    with pytest.raises(ValueError, match="Invalid"):
        store.read("../../etc/passwd")
