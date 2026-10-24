import hashlib
import json
import gzip

import httpx
import pytest

from o3d.catalog.public_parts import ARK_CATALOG_URL, live_ark_lookup, load_ark
from o3d.catalog import public_parts


def product(identity=10, sku="ARK-TEST", **changes):
    return {"id": identity, "product_id": identity, "parent_id": None,
        "sku": sku, "name": f"Test {identity}", "type": "simple",
        "permalink": f"https://arkelectron.com/product/test-{identity}/",
        "price": "123.45", "currency": "USD", "stock_quantity": 123,
        "stock_status": "instock", "purchasable": True, "backorders": "notify",
        "shipping_weight_g": 6, "weight_g": 6, "lead_time": "7 days",
        "dimensions_cm": {"length": "3", "width": "2", "height": "1"},
        "specs": {"voltage_range": None, "protocol": ["DroneCAN"], "ndaa": None},
        "docs_markdown": "https://docs.arkelectron.com/products/test.md",
        "spec_gaps": ["voltage_range", "ndaa", "lead_time"], **changes}


def page(rows, current=1, per_page=50, total=None):
    return {"store": "https://arkelectron.com/", "weight_unit": "g",
        "dimension_unit": "cm", "currency": "USD", "checkout": {"payment": "secret"},
        "page": current, "per_page": per_page, "total": len(rows) if total is None else total,
        "products": rows}


class MockFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, *, allowed_hosts):
        assert allowed_hosts == {"arkelectron.com"}
        self.urls.append(url)
        assert self.responses, "unexpected request"
        value = self.responses.pop(0)
        return value if isinstance(value, httpx.Response) else json.dumps(value).encode()


def test_catalog_redacts_offers_and_labels_derived_evidence():
    original = page([product()])
    fetcher = MockFetcher([original])
    bundle = load_ark(fetcher)
    record = bundle["records"][0]
    assert record["external_id"] == "10"
    assert record["facts"]["specs"]["voltage_range"] is None
    assert record["facts"]["specs"]["ndaa"] is None
    assert record["facts"]["availability_unknown"] is True
    assert record["license_status"] == "unknown"
    assert record["constraints"] == []  # No invented units/engineering interpretation.
    assert record["facts"]["dimensions_cm"]["length"] == "3"
    assert record["facts"]["docs_markdown"] == original["products"][0]["docs_markdown"]
    source = bundle["sources"][0]
    retained = json.loads(source["content"])
    assert source["source_kind"] == "derived_metadata"
    assert source["redaction"]["original_response_retained"] is False
    assert source["sha256"] == hashlib.sha256(source["content"].encode()).hexdigest()
    assert source["original_sha256"] == hashlib.sha256(json.dumps(original).encode()).hexdigest()
    for value in [record["facts"], retained["products"][0]]:
        for key in ("price", "currency", "stock_quantity", "stock_status", "purchasable",
                    "backorders", "shipping_weight_g", "weight_g", "lead_time"):
            assert key not in value
    assert "checkout" not in retained
    assert "currency" not in retained
    assert "lead_time" not in record["facts"]["spec_gaps"]


def test_actual_redirect_and_pagination_preserve_query_and_exact_variant_ids():
    parent = product(20, "SHARED", type="variable", variations=[
        {"id": 21, "product_id": 21, "parent_id": 20, "sku": "SHARED",
         "name": "Variant A", "attributes": {"antenna": "include"}, "price": "99"},
        {"id": 22, "product_id": 22, "parent_id": 20, "sku": "SHARED",
         "name": "Variant B", "attributes": {"antenna": "exclude"}, "stock_status": "instock"}])
    canonical = ARK_CATALOG_URL + "?ark_nocache=1"
    fetcher = MockFetcher([
        httpx.Response(307, headers={"location": canonical}),
        page([parent], per_page=1, total=2),
        page([product(21, "SHARED", type="variation", parent_id=20,
                      attributes={"antenna": "include"})], current=2, per_page=1, total=2)])
    bundle = load_ark(fetcher)
    assert fetcher.urls == [ARK_CATALOG_URL, canonical, canonical + "&page=2&per_page=1"]
    records = {r["external_id"]: r for r in bundle["records"]}
    assert set(records) == {"21", "22"}
    assert records["21"]["facts"]["sku"] == records["22"]["facts"]["sku"] == "SHARED"
    assert records["21"]["source_url"] == canonical + "&page=2&per_page=1"
    assert "specs" not in records["22"]["facts"]  # Parent specs never inferred.
    assert records["22"]["product_url"] == parent["permalink"]
    assert bundle["summary"]["variable_parents_excluded"] == 1
    assert len(bundle["sources"]) == 2


@pytest.mark.parametrize("location", [
    "https://evil.example/wp-json/ark/v1/catalog", "http://arkelectron.com/wp-json/ark/v1/catalog",
    "https://arkelectron.com/wp-json/ark/v1/checkout", "https://user:pass@arkelectron.com/wp-json/ark/v1/catalog",
    "https://arkelectron.com:444/wp-json/ark/v1/catalog"])
def test_unsafe_redirects_never_requested(location):
    fetcher = MockFetcher([httpx.Response(307, headers={"location": location})])
    with pytest.raises(ValueError, match="allowlist"):
        load_ark(fetcher)
    assert fetcher.urls == [ARK_CATALOG_URL]


@pytest.mark.parametrize("status", [403, 429, 503])
def test_failures_stop_without_retry_or_bypass(status):
    fetcher = MockFetcher([httpx.Response(status)])
    with pytest.raises(ValueError, match=f"HTTP {status}"):
        load_ark(fetcher)
    assert len(fetcher.urls) == 1


def test_request_budget_rejects_unbounded_catalog():
    fetcher = MockFetcher([page([product()], per_page=1, total=11)])
    with pytest.raises(ValueError, match="budget"):
        load_ark(fetcher)
    assert len(fetcher.urls) == 1


@pytest.mark.parametrize("second", [
    page([product(11)], current=1, per_page=1, total=2),
    page([product(11)], current=2, per_page=1, total=3),
    page([], current=2, per_page=1, total=2),
    page([product(10)], current=2, per_page=1, total=2)])
def test_bad_pagination_and_repeated_ids_reject_partial_results(second):
    fetcher = MockFetcher([page([product()], per_page=1, total=2), second])
    with pytest.raises(ValueError):
        load_ark(fetcher)


def test_live_lookup_returns_actual_ephemeral_response_without_snapshot():
    response = product()
    fetcher = MockFetcher([response])
    result = live_ark_lookup("ARK TEST", fetcher)
    assert result["ephemeral"] is True
    assert result["response"] == response
    assert result["response"]["stock_quantity"] == 123
    assert result["observed_at"].endswith("+00:00")
    assert result["source_url"] == ARK_CATALOG_URL + "/ARK%20TEST?ark_nocache=1"
    assert "sources" not in result
    assert len(fetcher.urls) == 1


def test_unknown_fields_are_not_retained_and_missing_sku_is_not_invented():
    bundle = load_ark(MockFetcher([page([product(sku="", new_offer={"price": 999})])]))
    facts = bundle["records"][0]["facts"]
    assert "new_offer" not in facts
    assert facts["sku"] == ""
    assert bundle["records"][0]["external_id"] == "10"


def test_original_http_proof_requires_status_and_retains_original_capture_time():
    response = httpx.Response(200, json=page([product()]))
    response.captured_at = "2026-10-24T12:00:00Z"
    source = load_ark(MockFetcher([response]))["sources"][0]
    assert source["derived_from_original_verified"] is True
    assert source["original_http_status"] == 200
    assert source["captured_at"] == response.captured_at
    source = load_ark(MockFetcher([page([product()])]))["sources"][0]
    assert source["derived_from_original_verified"] is False


@pytest.mark.parametrize("field,value", [
    ("dimensions_cm", {"price": "10"}),
    ("specs", {"voltage_range": {"price": "10"}}),
    ("attributes", {"option": {"price": "10"}}),
    ("docs_markdown", {"price": "10"})])
def test_unexpected_nested_metadata_cannot_hide_offers(field, value):
    with pytest.raises(ValueError):
        load_ark(MockFetcher([page([product(**{field: value})])]))


def test_inconsistent_parent_relationship_is_not_joined():
    parent = product(20, type="variable", variations=[product(21, parent_id=99)])
    with pytest.raises(ValueError, match="parent_id"):
        load_ark(MockFetcher([page([parent])]))


@pytest.mark.parametrize("numeric", ["NaN", "Infinity", "1e999"])
def test_nonfinite_json_numbers_are_rejected(numeric):
    raw = json.dumps(page([product()])).replace('"123.45"', numeric).encode()
    with pytest.raises(ValueError):
        load_ark(lambda url: raw)


def test_default_transport_identifies_agent_spaces_requests_and_disables_redirects(monkeypatch):
    requests, sleeps, configuration = [], [], {}
    canonical = ARK_CATALOG_URL + "?ark_nocache=1"
    def handler(request):
        requests.append(request)
        return (httpx.Response(307, headers={"location": canonical}) if len(requests) == 1
                else httpx.Response(200, json=page([product()])))
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    def make_client(**kwargs):
        configuration.update(kwargs)
        client.headers.update(kwargs["headers"])
        return client
    ticks = iter([10.0, 10.25, 11.0])
    monkeypatch.setattr(public_parts.httpx, "Client", make_client)
    monkeypatch.setattr(public_parts.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(public_parts.time, "sleep", sleeps.append)
    result = load_ark()
    assert configuration["follow_redirects"] is False
    assert sleeps == [0.75]
    assert "OxygenPartsCatalog" in requests[0].headers["User-Agent"]
    assert result["summary"]["http_requests"] == 2


def test_compressed_http_body_is_decoded_once(monkeypatch):
    compressed = gzip.compress(json.dumps(page([product()])).encode())
    client = httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, content=compressed, headers={
            "Content-Encoding": "gzip", "Content-Length": str(len(compressed))})))
    monkeypatch.setattr(public_parts.httpx, "Client", lambda **kwargs: client)
    result = load_ark()
    assert len(result["records"]) == 1
    assert result["sources"][0]["derived_from_original_verified"] is True
