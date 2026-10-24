"""Offline community snapshot contracts; fixtures do not establish live access."""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json

import httpx
import pytest

from o3d.catalog import community_jlc as community
from o3d.catalog.manufacturing_models import ManufacturingRecord
from o3d.catalog.offer_policy import contains_offer_values


def csv_bytes(*rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def row(**changes):
    return {"lcsc": "25804", "mfr": "EXAMPLE-100", "manufacturer": "Example",
            "package": "0603", "fetched_at": "1790674493", "stock": "123456",
            "price": "1-49:0.0887", "last_on_stock": "1790674493",
            "attributes": '{"Resistance":"10kΩ","verified":false,"dimensions":null}',
            "attrition": '{"leastNumber":2,"minPurchaseNum":1}',
            "Min Order Qty": "3", "datasheet": "https://example.org/data.pdf", **changes}


def proof(raw, **changes):
    return {"url": community.CSV_URL, "status": 200,
            "captured_at": "2026-10-24T21:38:32.350499+00:00",
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), **changes}


def test_snapshot_is_engineering_metadata_with_exact_original_proof():
    raw = csv_bytes(row())
    bundle = community.load_cdfer_csv(raw, proof(raw))
    assert bundle["provider"]["key"] == "jlcpcb"
    assert bundle["provider"]["name"] == "JLCPCB"
    record, = bundle["records"]
    ManufacturingRecord.model_validate(record)
    assert record["external_id"] == "C25804"
    assert record["facts"]["lcsc"] == "25804"
    assert record["facts"]["manufacturer_part_number"] == "EXAMPLE-100"
    assert record["facts"]["attributes"] == {"Resistance": "10kΩ", "verified": False, "dimensions": None}
    assert record["facts"]["attrition"] == {"leastNumber": 2, "minPurchaseNum": 1}
    # Conflicting original procurement fields remain independent facts.
    assert record["facts"]["Min Order Qty"] == "3"
    assert record["facts"]["datasheet"] == "https://example.org/data.pdf"
    assert record["origin"] == "community-cdfer"
    assert record["fetched_at"] == datetime.fromtimestamp(1790674493, timezone.utc).isoformat()
    assert record["snapshot_captured_at"] == proof(raw)["captured_at"]
    assert record["fetched_at"] < record["snapshot_captured_at"]
    assert record["product_url"] is None and record["order_path"] is None
    assert record["license_status"] == "unknown"
    assert record["facts"]["cad_status"] == "unknown"
    assert record["facts"]["availability_unknown"] is True
    assert not contains_offer_values(record)
    source, = bundle["sources"]
    assert source["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert source["sha256"] == hashlib.sha256(source["content"].encode()).hexdigest()
    assert source["derived_from_original_verified"] is True
    assert source["original_http_status"] == 200
    retained = json.loads(source["content"])
    assert not contains_offer_values(retained)
    assert "123456" not in source["content"]
    assert "0.0887" not in source["content"]
    assert any(path.endswith(".last_on_stock") for path in source["redaction"]["fields_removed"])


def test_recursive_offer_policy_and_unknown_additional_source_columns():
    raw = csv_bytes(row(attributes=json.dumps({"voltage": "5V", "nested": {"stock": 34},
        "Price (USD)": 3, "rows": [{"key": "availability", "value": "ready"}],
        "free_text": "USD 12"}), extra_metadata="original string"))
    record, = community.load_cdfer_csv(raw)["records"]
    assert record["facts"]["attributes"] == {"voltage": "5V", "nested": {}, "rows": []}
    assert record["facts"]["extra_metadata"] == "original string"
    assert not contains_offer_values(record)


def test_default_local_capture_and_sibling_metadata(tmp_path, monkeypatch):
    path = tmp_path / "basic-preferred.csv"
    raw = csv_bytes(row())
    path.write_bytes(raw)
    path.with_suffix(".meta.json").write_text(json.dumps(proof(raw)))
    monkeypatch.setattr(community, "DEFAULT_PATH", path)
    assert community.load_cdfer_csv()["summary"]["original_http_verified"] is True


@pytest.mark.parametrize("attributes", ["", "null"])
def test_null_json_and_missing_component_time_remain_unknown(attributes):
    record, = community.load_cdfer_csv(csv_bytes(row(attributes=attributes, attrition="null", fetched_at="")))["records"]
    assert record["facts"]["attributes"] is None
    assert record["facts"]["attrition"] is None
    assert record["fetched_at"] is None


@pytest.mark.parametrize("value", ['{"x":', "[]", "false", '{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}'])
@pytest.mark.parametrize("field", ["attributes", "attrition"])
def test_malformed_json_is_not_silently_dropped(value, field):
    with pytest.raises(ValueError, match=f"Malformed {field} JSON"):
        community.load_cdfer_csv(csv_bytes(row(**{field: value})))


def test_duplicate_skus_retain_conflicting_and_identical_observations():
    raw = csv_bytes(row(), row(**{"Min Order Qty": "5"}), row())
    bundle = community.load_cdfer_csv(raw)
    records = bundle["records"]
    assert len(records) == 3
    assert len({record["external_id"] for record in records}) == 3
    assert all(record["facts"]["sku"] == "C25804" for record in records)
    assert all(record["source_variant"] is True for record in records)
    assert [record["facts"]["Min Order Qty"] for record in records] == ["3", "5", "3"]
    assert records[0]["external_id"].endswith(":1")
    assert records[2]["external_id"].endswith(":2")
    reordered = community.load_cdfer_csv(csv_bytes(row(**{"Min Order Qty": "5"}), row(), row()))
    assert {r["external_id"] for r in reordered["records"]} == {r["external_id"] for r in records}
    assert bundle["summary"]["distinct_skus"] == 1
    assert bundle["summary"]["repeated_sku_rows"] == 2


@pytest.mark.parametrize("value", ["", "c12", "12.0", "C1/../", "C1234567890123"])
def test_invalid_lcsc(value):
    with pytest.raises(ValueError, match="Invalid LCSC"):
        community.load_cdfer_csv(csv_bytes(row(lcsc=value)))


@pytest.mark.parametrize("value", ["2026-10-24", "oops", "-1", "1.1"])
def test_invalid_component_timestamp(value):
    with pytest.raises(ValueError, match="Invalid fetched_at"):
        community.load_cdfer_csv(csv_bytes(row(fetched_at=value)))


@pytest.mark.parametrize("changes", [{"sha256": "0" * 64}, {"bytes": 1},
                                      {"url": "https://example.org/elsewhere"}])
def test_original_capture_mismatch_rejected(changes):
    raw = csv_bytes(row())
    with pytest.raises(ValueError):
        community.load_cdfer_csv(raw, proof(raw, **changes))


@pytest.mark.parametrize("changes", [{"status": 403}, {"status": "200"}, {"http_verified": False},
    {"captured_at": None}, {"captured_at": "2026-10-24T00:00:00"}, {"captured_at": "bad"}])
def test_incomplete_proof_is_unverified(changes):
    raw = csv_bytes(row())
    source, = community.load_cdfer_csv(raw, proof(raw, **changes))["sources"]
    assert source["derived_from_original_verified"] is False
    assert source["verification"] == "observational_unverified"


def test_bare_csv_never_claims_http_or_capture_time():
    source, = community.load_cdfer_csv(csv_bytes(row()))["sources"]
    assert source["captured_at"] is None
    assert source["status"] is None
    assert source["derived_from_original_verified"] is False


def test_fetch_uses_one_fixed_get_with_original_timestamp():
    raw = csv_bytes(row())
    calls = []
    def fetch(url):
        calls.append(url)
        return httpx.Response(200, content=raw, extensions={"captured_at": proof(raw)["captured_at"]})
    body, meta = community.fetch_csv(fetch)
    assert body == raw
    assert calls == [community.CSV_URL]
    assert meta["http_verified"] is True
    assert community.load_cdfer_csv(body, meta)["summary"]["original_http_verified"] is True


@pytest.mark.parametrize("status", [301, 403, 500])
def test_fetch_does_not_follow_redirects_or_retry(status):
    calls = []
    def fetch(url):
        calls.append(url)
        return httpx.Response(status, headers={"location": "https://example.org/"})
    with pytest.raises(ValueError, match=f"HTTP {status}"):
        community.fetch_csv(fetch)
    assert len(calls) == 1


def test_bare_fetch_and_http_without_capture_time_are_unverified():
    raw = csv_bytes(row())
    for response in (raw, raw.decode(), httpx.Response(200, content=raw)):
        body, meta = community.fetch_csv(lambda url: response)
        assert meta["http_verified"] is False
        assert meta["captured_at"] is None


def test_bounded_input_and_csv_structure(monkeypatch):
    monkeypatch.setattr(community, "MAX_CSV_BYTES", 4)
    with pytest.raises(ValueError, match="20 MB"):
        community.fetch_csv(lambda url: b"12345")
    with pytest.raises(ValueError, match="20 MB"):
        community.load_cdfer_csv(b"12345")
    monkeypatch.setattr(community, "MAX_CSV_BYTES", 1000)
    for raw in (b"lcsc,lcsc\n1,2\n", b"lcsc,mfr\n1\n", b"lcsc\n1,2\n", b"mfr\nx\n", b"\xff"):
        with pytest.raises(ValueError):
            community.load_cdfer_csv(raw)
