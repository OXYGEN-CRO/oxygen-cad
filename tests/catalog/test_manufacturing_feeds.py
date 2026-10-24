"""Pure feed tests: no catalog fixture, network, or shared database."""
from copy import deepcopy
import gzip
import hashlib
import json

import httpx
import pytest

from o3d.catalog.manufacturing_feeds import (
    LASERHUB_MATERIALS_URL, SENDCUTSEND_CATALOG_URL, SENDCUTSEND_SPECS_URL,
    _PublicFetcher, load_laserhub, load_sendcutsend,
)
from o3d.catalog.offer_policy import is_offer_field, strip_offer_fields


def sendcutsend_payloads():
    # Representative provider schema. Stock/price must never enter storage.
    catalog = {"_meta": {"generated_at": "2026-05-27T16:45:46.616728+00:00"},
        "materials": [
            {"sku": "ALU6061-100", "name": "6061 T6 Aluminum", "thickness": "0.100",
             "cutting_process": "Fiber Laser", "available_services": ["tapping", "anodizing"], "out_of_stock": False},
            {"sku": "ALU6061-125", "name": "6061 T6 Aluminum", "thickness": "0.125",
             "cutting_process": "Fiber Laser", "available_services": ["bending"]}],
        "hardware": [{"sku": "SP-M5-0", "title": "Nut, M5x0.08", "unit_price": "2.13",
             "manufacturing_specs": {"min_sheet_thickness": 0.031, "max_sheet_thickness": "N/A"}}],
        "finish_options": [{"value": "clear", "finish_type": "anodizing", "color": "Clear"}]}
    specs = {"_meta": {"units_note": "All dimensions are in inches unless otherwise noted. Angles are in degrees."},
        "materials": [{"sku": "ALU6061-100", "general_specs": {"thickness": "0.100", "gauge": 12.0},
            "cutting_specs": {"min_hole_size": "0.040", "min_bridge_size": None,
                "min_hole_to_edge": "N/A", "min_part_length": 0.375, "max_part_size": "44 x 30"},
            "bending_specs": {"max_bend_angle": "130", "k_factor": 0.4},
            "material_properties": {"density": "169.344 lb/ft^3"}},
            {"sku": "DOES-NOT-MATCH", "general_specs": {"thickness": "0.125"},
             "cutting_specs": {"min_hole_size": "0.999"}}]}
    return {SENDCUTSEND_CATALOG_URL: catalog, SENDCUTSEND_SPECS_URL: specs}


def encoded_fetcher(payloads):
    # Explicit recorded-response metadata for fixture verification, not a claim
    # that these tests execute a real provider HTTP request.
    return lambda url: httpx.Response(200, content=json.dumps(payloads[url], ensure_ascii=False).encode("utf-8"),
        extensions={"captured_at": "2026-10-24T21:17:03+00:00"})


def test_sendcutsend_preserves_exact_skus_without_material_service_cross_join():
    payloads = sendcutsend_payloads()
    bundle = load_sendcutsend(encoded_fetcher(payloads))
    assert bundle["provider"] == "sendcutsend"
    assert len(bundle["records"]) == 4
    assert [r["external_id"] for r in bundle["records"]] == ["ALU6061-100", "ALU6061-125", "SP-M5-0", "clear"]
    first, second, hardware, finish = bundle["records"]
    assert first["facts"]["available_services"] == ["tapping", "anodizing"]
    assert second["facts"]["available_services"] == ["bending"]
    assert "engineering_specs" not in second["facts"]
    assert second["constraints"] == []
    assert "unit_price" not in hardware["facts"]
    assert "out_of_stock" not in first["facts"]
    assert hardware["constraints"] == []  # Catalog never declares dimensional units.
    assert finish["process"] == "anodizing"
    assert bundle["summary"]["catalog_generated_at"].startswith("2026-05-27")
    assert all("No current price" in r["notes"][0] for r in bundle["records"])


@pytest.mark.parametrize("key", ["unitPrice", "unit_price", "currentStock", "out_of_stock", "quantityAvailable",
    "availableQuantity", "pricing", "availability", "inventory", "stockLevels", "specialOffers"])
def test_offer_keys_cover_camel_case_and_snapshot_containers(key):
    assert is_offer_field(key, "snapshot-value")


def test_offer_redaction_retains_engineering_quantities_and_nested_variants():
    original = {"quantity": 2, "minimum_order_quantity": 5, "shippingWeight": 12.5,
        "available_services": ["hardware"], "dimensions": {"quantity": 3, "width": 4},
        "variant": [{"sku": "SO-M3-12", "unitPrice": "1.23", "currentStock": 8,
                     "quantityAvailable": 20, "pricing": {"currency": "USD", "amount": 1.2},
                     "availability": {"status": "in-stock"}, "hole_diameter": "0.171"}]}
    clean, redactions = strip_offer_fields(original)
    assert clean == {"quantity": 2, "minimum_order_quantity": 5, "shippingWeight": 12.5,
        "available_services": ["hardware"], "dimensions": {"quantity": 3, "width": 4},
        "variant": [{"sku": "SO-M3-12", "hole_diameter": "0.171"}]}
    assert set(redactions) == {f"variant[0].{key}" for key in
        ("unitPrice", "currentStock", "quantityAvailable", "pricing", "availability")}
    assert "unitPrice" in original["variant"][0]  # Source capture remains immutable.


def test_sendcutsend_derived_sources_match_filtered_facts_and_original_checksums():
    from o3d.catalog.manufacturing import _offer_fields

    payloads = sendcutsend_payloads()
    payloads[SENDCUTSEND_SPECS_URL]["materials"][0]["general_specs"]["out_of_stock"] = False
    bundle = load_sendcutsend(encoded_fetcher(payloads))
    assert not any(_offer_fields(record["facts"]) for record in bundle["records"])
    for source in bundle["sources"]:
        original = encoded_fetcher(payloads)(source["url"]).content
        assert source["original_sha256"] == hashlib.sha256(original).hexdigest()
        assert source["sha256"] == hashlib.sha256(source["content"].encode()).hexdigest()
        assert source["source_kind"] == "derived_metadata"
        assert source["original_http_status"] == source["status"] == 200
        assert source["derived_from_original_verified"] is True
        assert source["verification"] == "verified_derived_metadata"
        assert source["captured_at"] and source["redactions"]
        derived_payload = json.loads(source["content"])
        assert not _offer_fields(derived_payload)
        assert derived_payload == strip_offer_fields(payloads[source["url"]])[0]
    assert bundle["summary"]["redacted_offer_fields"] == 3


def test_sendcutsend_safe_numbers_explicit_units_and_field_provenance():
    bundle = load_sendcutsend(encoded_fetcher(sendcutsend_payloads()))
    constraints = {r["key"]: r for r in bundle["records"][0]["constraints"]}
    assert constraints["general_specs.thickness"]["value"] == 0.1
    hole = constraints["cutting_specs.min_hole_size"]
    assert hole == {"key": "cutting_specs.min_hole_size", "value": .04, "unit": "in",
        "source_field": "materials[0].cutting_specs.min_hole_size", "source_url": SENDCUTSEND_SPECS_URL}
    assert constraints["bending_specs.max_bend_angle"]["unit"] == "deg"
    assert set(constraints) == {"general_specs.thickness", "cutting_specs.min_hole_size",
        "cutting_specs.min_part_length", "bending_specs.max_bend_angle"}
    assert bundle["records"][0]["facts"]["engineering_specs"]["cutting_specs"]["min_hole_to_edge"] == "N/A"


@pytest.mark.parametrize("meta", [{}, None, {"units_note": None}, {"units_note": "Units unknown"}])
def test_sendcutsend_never_infers_missing_units(meta):
    payloads = sendcutsend_payloads()
    payloads[SENDCUTSEND_SPECS_URL]["_meta"] = meta
    bundle = load_sendcutsend(encoded_fetcher(payloads))
    assert bundle["records"][0]["constraints"] == []


def test_sendcutsend_duplicate_engineering_sku_does_not_guess():
    payloads = sendcutsend_payloads()
    payloads[SENDCUTSEND_SPECS_URL]["materials"].append(
        deepcopy(payloads[SENDCUTSEND_SPECS_URL]["materials"][0]))
    bundle = load_sendcutsend(encoded_fetcher(payloads))
    assert bundle["records"][0]["constraints"] == []
    assert "engineering_specs" not in bundle["records"][0]["facts"]
    assert "Ambiguous engineering SKU" in bundle["warnings"][0]


def test_laserhub_preserves_individual_tuples_ranges_and_type_identity():
    materials = [
        {"id": 21, "group": "aluminium", "manufacturingType": "SHEET",
            "locale": {"de": "EN AW-5754 (AlMg3)"}, "variations": [
                {"thickness": 2, "maxLength": 3980, "maxWidth": 1980},
                {"thickness": 2, "maxLength": 2980, "maxWidth": 1480},
                {"thickness": 3, "maxLength": 2980, "maxWidth": 1480}]},
        {"id": 21, "group": "aluminium", "manufacturingType": "MILLING",
            "locale": None, "ranges": {"square": {"min": 5, "max": 1000}}},
        {"id": 1025, "manufacturingType": "TUBE", "locale": {}, "variations": [
            {"profile": "CIRCULAR", "width": 20, "height": 20, "thickness": 1}]}]
    bundle = load_laserhub(encoded_fetcher({LASERHUB_MATERIALS_URL: materials}))
    assert [r["external_id"] for r in bundle["records"]] == ["SHEET:21", "MILLING:21", "TUBE:1025"]
    assert bundle["records"][0]["facts"] == materials[0]
    assert bundle["records"][1]["facts"]["ranges"] == {"square": {"min": 5, "max": 1000}}
    assert all(r["constraints"] == [] for r in bundle["records"])
    assert bundle["summary"]["source_variations"] == 4
    assert all("not an ordering API" in r["notes"][1] for r in bundle["records"])


def test_laserhub_repeated_source_ids_keep_evidence_without_overwriting():
    first = {"id": 6040, "manufacturingType": "MILLING", "ranges": {"square": {"min": 5, "max": 1000}}}
    other = {**first, "ranges": {"square": {"min": 10, "max": 800}}}
    bundle = load_laserhub(encoded_fetcher({LASERHUB_MATERIALS_URL: [first, deepcopy(first), other]}))
    assert len(bundle["records"]) == 1
    record = bundle["records"][0]
    assert record["external_id"] == "MILLING:6040"
    assert record["source_row_indices"] == [0, 1, 2]
    assert record["facts"] == first
    assert record["source_record_alternatives"] == [other]
    assert bundle["summary"]["source_rows"] == 3
    assert bundle["summary"]["duplicate_source_rows"] == 1


def test_sources_return_original_utf8_body_and_exact_checksum():
    raw = '[{"id":67,"manufacturingType":"SHEET","locale":{"de":"Aluminium | blank — ö"}}]\n'.encode()

    class FakeFetcher:
        def get(self, url, *, allowed_hosts):
            assert url == LASERHUB_MATERIALS_URL
            assert allowed_hosts == {"app.laserhub.com"}
            return httpx.Response(200, content=raw, extensions={"captured_at": "2026-10-24T21:17:37+00:00"})

    source = load_laserhub(FakeFetcher())["sources"][0]
    assert source["content"].encode() == raw
    assert source["sha256"] == hashlib.sha256(raw).hexdigest()
    assert source["media_type"] == "application/json"
    assert source["status"] == 200
    assert source["captured_at"] == "2026-10-24T21:17:37+00:00"
    assert source["http_verified"] is True


@pytest.mark.parametrize("as_text", [False, True])
def test_bare_payload_never_claims_http_capture_or_derived_proof(as_text):
    payloads = sendcutsend_payloads()

    def fetch(url):
        content = json.dumps(payloads[url])
        return content if as_text else content.encode()

    bundle = load_sendcutsend(fetch)
    for source in bundle["sources"]:
        assert source["status"] is None
        assert source["original_http_status"] is None
        assert source["captured_at"] is None
        assert source["http_verified"] is False
        assert source["verification"] == "observational_unverified"
        assert source["derived_from_original_verified"] is False


@pytest.mark.parametrize("captured_at", [None, "bad-date", "2026-10-24T21:17:37"])
def test_response_without_valid_capture_timestamp_remains_unverified(captured_at):
    raw = b'[{"id":67,"manufacturingType":"SHEET"}]'
    source = load_laserhub(lambda url: httpx.Response(200, content=raw,
        extensions={"captured_at": captured_at}))["sources"][0]
    assert source["status"] == 200
    assert source["http_verified"] is False
    assert source["verification"] == "observational_unverified"


def test_recorded_response_can_explicitly_withhold_http_verification():
    raw = b'[{"id":67,"manufacturingType":"SHEET"}]'
    source = load_laserhub(lambda url: httpx.Response(200, content=raw, extensions={
        "captured_at": "2026-10-24T21:17:37+00:00", "http_verified": False}))["sources"][0]
    assert source["http_verified"] is False
    assert source["verification"] == "observational_unverified"


@pytest.mark.parametrize("raw", [b'{"login":"required"}', b'[{"id":1,"manufacturingType":"SHEET","x":NaN}]', b"<html>blocked</html>"])
def test_laserhub_rejects_nonfeed_payloads(raw):
    with pytest.raises(ValueError):
        load_laserhub(lambda url: raw)


def test_http_error_is_not_retried_or_treated_as_a_feed():
    calls = []

    def fetch(url):
        calls.append(url)
        return httpx.Response(403, content=b"blocked")

    with pytest.raises(ValueError, match="HTTP 403"):
        load_sendcutsend(fetch)
    assert calls == [SENDCUTSEND_CATALOG_URL]


def test_default_fetcher_spaces_gets_and_never_retries_redirect(monkeypatch):
    requests, sleeps = [], []
    fetcher = _PublicFetcher()
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: requests.append(str(request.url)) or httpx.Response(
            200 if len(requests) == 1 else 302, content=b"{}", headers={"location": LASERHUB_MATERIALS_URL})))
    monkeypatch.setattr("o3d.catalog.manufacturing_feeds.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("o3d.catalog.manufacturing_feeds.time.sleep", sleeps.append)
    try:
        fetcher.get(SENDCUTSEND_CATALOG_URL, allowed_hosts={"app.sendcutsend.com"})
        with pytest.raises(ValueError, match="HTTP 302"):
            fetcher.get(SENDCUTSEND_SPECS_URL, allowed_hosts={"app.sendcutsend.com"})
    finally:
        fetcher.close()
    assert requests == [SENDCUTSEND_CATALOG_URL, SENDCUTSEND_SPECS_URL]
    assert sleeps == [1.0]


def test_default_fetcher_decodes_http_compression_once():
    raw = b'[{"id":21,"manufacturingType":"SHEET"}]'
    fetcher = _PublicFetcher()
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, content=gzip.compress(raw), headers={"Content-Encoding": "gzip"})))
    try:
        bundle = load_laserhub(fetcher)
    finally:
        fetcher.close()
    assert bundle["sources"][0]["content"].encode() == raw
    assert bundle["sources"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert bundle["sources"][0]["status"] == 200
    assert bundle["sources"][0]["http_verified"] is True
    assert bundle["sources"][0]["captured_at"]
