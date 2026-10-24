"""Unofficial CDFER JLCPCB CSV snapshots, converted to sourced metadata only.

Public distribution is not evidence of CAD or dataset reuse rights. No binary,
live offer, authentication, or purchase endpoint is used by this adapter.
"""
from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re

import httpx

from .http import USER_AGENT
from .offer_policy import strip_offer_fields

CSV_URL = "https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components-basic-preferred.csv"
PROJECT_URL = "https://github.com/CDFER/jlcpcb-parts-database"
MAX_CSV_BYTES = 20_000_000
DEFAULT_PATH = Path.home() / ".local/share/oxygen-parts/community-jlc/basic-preferred.csv"
_NOTE = (
    "Unofficial community snapshot of JLCPCB component metadata. Component source "
    "timestamps can precede CSV deployment and capture. Availability is unknown; "
    "offers are excluded. Datasheets are references only. CAD and dataset reuse "
    "rights are unknown; the project's code license does not establish those rights."
)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _raw_bytes(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, bytes) or len(value) > MAX_CSV_BYTES:
        raise ValueError("Expected CSV bytes within the 20 MB limit")
    return value


def fetch_csv(fetcher=None) -> tuple[bytes, dict]:
    """One fixed public GET, no redirects/retries; return bytes and capture proof.

    An injected callable or ``get(url, allowed_hosts=...)`` object may return
    bytes/text (unverified) or a response with status and its original capture
    timestamp in ``extensions['captured_at']``. Missing timestamps are never
    replaced with the replay time. The default transport records its receive time.
    """
    if fetcher is None:
        with httpx.Client(timeout=60, follow_redirects=False,
                          headers={"User-Agent": USER_AGENT}) as client:
            with client.stream("GET", CSV_URL) as response:
                if response.status_code != 200:
                    raise ValueError(f"Community CSV returned HTTP {response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_CSV_BYTES:
                        raise ValueError("Community CSV exceeds the 20 MB limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                status, captured = response.status_code, datetime.now(timezone.utc).isoformat()
                last_modified = response.headers.get("last-modified")
                verified = True
    else:
        response = (fetcher(CSV_URL) if callable(fetcher) else
                    fetcher.get(CSV_URL, allowed_hosts={"cdfer.github.io"}))
        status = getattr(response, "status_code", None)
        if status is not None and (type(status) is not int or status != 200):
            raise ValueError(f"Community CSV returned HTTP {status}")
        raw = _raw_bytes(response if isinstance(response, (bytes, str)) else response.content)
        extensions = getattr(response, "extensions", {})
        extensions = extensions if isinstance(extensions, dict) else {}
        captured = _timestamp(extensions.get("captured_at") or getattr(response, "captured_at", None))
        verified = status == 200 and captured is not None and extensions.get("http_verified") is not False
        last_modified = getattr(response, "headers", {}).get("last-modified")
    return raw, {"url": CSV_URL, "status": status, "captured_at": captured,
        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "http_verified": verified, "last_modified": last_modified}


def _json_object(value, field, row_number):
    if value in ("", "null"):
        return None

    def invalid_number(value):
        raise ValueError("Non-finite JSON number")

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number")
        return result

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = item
        return result

    try:
        result = json.loads(value, parse_constant=invalid_number, parse_float=number,
                            object_pairs_hook=pairs)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise ValueError("Expected JSON object or null")
        return result
    except (ValueError, TypeError):
        raise ValueError(f"Malformed {field} JSON in CSV row {row_number}") from None


def _component_time(value, row_number):
    if value in (None, ""):
        return None
    if not re.fullmatch(r"[0-9]{1,12}", value):
        raise ValueError(f"Invalid fetched_at epoch in CSV row {row_number}")
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"Invalid fetched_at epoch in CSV row {row_number}") from None


def load_cdfer_csv(path=None, metadata=None) -> dict:
    """Build a ManufacturingCatalog.ingest_bundle-compatible metadata bundle.

    ``path`` is a CSV path or bytes. The default local capture uses its sibling
    ``basic-preferred.meta.json``. Explicit metadata must describe these exact
    original bytes; verified provenance requires URL, digest, HTTP 200 and a
    timezone-aware original capture timestamp. Local bytes alone stay unverified.
    This function performs no network requests, database writes or file writes.
    """
    if path is None:
        path = DEFAULT_PATH
    if isinstance(path, bytes):
        raw = _raw_bytes(path)
    else:
        path = Path(path)
        if path.stat().st_size > MAX_CSV_BYTES:
            raise ValueError("Community CSV exceeds the 20 MB limit")
        raw = _raw_bytes(path.read_bytes())
        sibling = path.with_suffix(".meta.json")
        if metadata is None and sibling.exists():
            metadata = json.loads(sibling.read_text(encoding="utf-8"))
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Expected original capture metadata object")
    meta = dict(metadata or {})
    digest = hashlib.sha256(raw).hexdigest()
    if meta.get("url", CSV_URL) != CSV_URL:
        raise ValueError("Capture metadata URL is outside the fixed community source")
    if "sha256" in meta and meta["sha256"] != digest:
        raise ValueError("CSV original capture SHA-256 mismatch")
    if "bytes" in meta and (type(meta["bytes"]) is not int or meta["bytes"] != len(raw)):
        raise ValueError("CSV original capture byte count mismatch")
    captured = _timestamp(meta.get("captured_at"))
    status = meta.get("status") if type(meta.get("status")) is int else None
    verified = (meta.get("url") == CSV_URL and meta.get("sha256") == digest and
                status == 200 and captured is not None and meta.get("http_verified") is not False)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if (not reader.fieldnames or "lcsc" not in reader.fieldnames or
                len(set(reader.fieldnames)) != len(reader.fieldnames)):
            raise ValueError("Expected unique CSV headers including lcsc")
        records, rows, redactions = [], [], []
        for row_number, original in enumerate(reader, start=2):
            if None in original or any(value is None for value in original.values()):
                raise ValueError(f"CSV row {row_number} does not match its headers")
            lcsc = original["lcsc"]
            if not re.fullmatch(r"C?[0-9]{1,12}", lcsc):
                raise ValueError(f"Invalid LCSC identifier in CSV row {row_number}")
            sku = lcsc if lcsc.startswith("C") else "C" + lcsc
            facts = dict(original)
            for field in ("attributes", "attrition"):
                if field in facts:
                    facts[field] = _json_object(facts[field], field, row_number)
            component_time = _component_time(original.get("fetched_at"), row_number)
            facts, removed = strip_offer_fields(facts)
            redactions.extend(f"rows[{row_number - 2}].{key}" for key in removed)
            facts.update(sku=sku, manufacturer_part_number=facts.get("mfr") or None,
                         availability_unknown=True, cad_status="unknown")
            rows.append(facts)
            records.append({"external_id": sku, "name": facts.get("mfr") or sku,
                "record_type": "purchased_part", "product_url": None, "order_path": None,
                "source_url": CSV_URL, "origin": "community-cdfer", "facts": facts,
                "constraints": [], "api_access": "public", "license_status": "unknown",
                "availability_unknown": True, "fetched_at": component_time,
                "snapshot_captured_at": captured, "source_row_number": row_number,
                "notes": [_NOTE]})
    except (csv.Error, UnicodeError):
        raise ValueError("Invalid community CSV encoding or structure") from None
    # This real export repeats some SKUs with distinct assembly/procurement
    # facts. Keep source observations separate rather than picking one version.
    counts = Counter(record["external_id"] for record in records)
    occurrences = Counter()
    for record in records:
        sku = record["external_id"]
        if counts[sku] > 1:
            facts_hash = hashlib.sha256(json.dumps(record["facts"], ensure_ascii=False,
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            occurrences[(sku, facts_hash)] += 1
            record["external_id"] = f"{sku}:source-variant:{facts_hash}:{occurrences[(sku, facts_hash)]}"
            record["source_variant"] = True
            record["notes"].append("Repeated source SKU; each source observation is retained independently.")
    content = json.dumps({"origin": "community-cdfer", "rows": rows}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    source = {"url": CSV_URL, "status": status, "captured_at": captured,
        "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "media_type": "application/json", "source_kind": "derived_metadata",
        "original_sha256": digest, "original_bytes": len(raw),
        "original_http_status": status, "derived_from_original_verified": verified,
        "http_verified": verified,
        "verification": "verified_derived_metadata" if verified else "observational_unverified",
        "last_modified": meta.get("last_modified"),
        "redaction": {"method": "shared_offer_policy_v1", "fields_removed": redactions,
                      "original_response_retained": False}}
    return {"task": "community-jlc-cdfer", "provider": {"key": "jlcpcb", "name": "JLCPCB",
            "origin": "community-cdfer", "source_url": CSV_URL, "project_url": PROJECT_URL,
            "official_api": False, "license_status": "unknown", "availability_unknown": True},
        "records": records, "sources": [source], "blockers": [],
        "summary": {"records": len(records), "source_rows": len(rows),
                    "distinct_skus": len(counts), "repeated_sku_rows": len(rows) - len(counts),
                    "origin": "community-cdfer", "official_api": False,
                    "original_http_verified": verified, "note": _NOTE}}
