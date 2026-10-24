"""ARK's documented, public read-only catalog and ephemeral offer lookup.

Catalog snapshots retain only explicitly selected metadata, never price, stock,
shipping weight, lead time, checkout or payment data. A source is a *derived*
snapshot with a separate original-response digest, not a verbatim HTTP capture.
The supplier's public agent documentation is not a CAD/data reuse license.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import time
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .http import USER_AGENT

ARK_CATALOG_URL = "https://arkelectron.com/wp-json/ark/v1/catalog"
ARK_AGENT_DOCS_URL = "https://arkelectron.com/llms.txt"
_HOST = "arkelectron.com"
_MAX_BYTES = 10_000_000
_MAX_REQUESTS = 10
# Fail closed when the provider adds fields: unfamiliar fields are not retained.
_PRODUCT_FIELDS = {
    "id", "product_id", "parent_id", "sku", "parent_sku", "name", "slug",
    "type", "permalink", "dimensions_cm", "attributes", "docs_markdown",
    "datasheet_url", "export_review", "variation_skus",
}
_SPEC_FIELDS = {
    "voltage_range", "continuous_current", "burst_current", "protocol",
    "connector", "mounting", "ndaa", "made_in_usa", "firmware",
    "firmware_index", "datasheet_url", "docs_markdown",
}
_NOTE = (
    "Supplier metadata; null means unknown. Availability is unknown. Offers are "
    "excluded; use a fresh ephemeral lookup. Dimensions are catalog values, not "
    "a fit proof. export_review=false is not an export classification. CAD/data "
    "reuse license is unknown; linked documents are references only."
)


def _validate_url(url: str):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != _HOST or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment
            or not (parsed.path == "/wp-json/ark/v1/catalog"
                    or parsed.path.startswith("/wp-json/ark/v1/catalog/"))):
        raise ValueError("ARK request is outside the public catalog HTTPS allowlist")
    if set(parse_qs(parsed.query)) - {"ark_nocache", "page", "per_page", "sku", "search"}:
        raise ValueError("Unsupported ARK catalog query")


class _Fetcher:
    def __init__(self):
        self.client = httpx.Client(timeout=60, follow_redirects=False,
                                   headers={"User-Agent": USER_AGENT})
        self.last = None

    def get(self, url, *, allowed_hosts):
        _validate_url(url)
        if self.last is not None:
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        with self.client.stream("GET", url) as response:
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ValueError("ARK response exceeds byte limit")
                chunks.append(chunk)
            # iter_bytes() already decodes Content-Encoding. Reconstructing a
            # response with the old encoding would decode those bytes again.
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {"content-encoding", "content-length"}}
            return httpx.Response(response.status_code, content=b"".join(chunks), headers=headers)

    def close(self):
        self.client.close()


class _Reader:
    def __init__(self, fetcher):
        self.fetcher, self.requests = fetcher, 0

    def read(self, url):
        seen = set()
        while True:
            _validate_url(url)
            if url in seen or self.requests >= _MAX_REQUESTS:
                raise ValueError("ARK redirect/request limit reached")
            seen.add(url)
            self.requests += 1
            result = (self.fetcher(url) if callable(self.fetcher) else
                      self.fetcher.get(url, allowed_hosts={_HOST}))
            status = getattr(result, "status_code", 200)
            if status in (301, 302, 303, 307, 308):
                location = getattr(result, "headers", {}).get("location")
                if not location:
                    raise ValueError("ARK redirect lacks Location")
                url = urljoin(url, location)  # Follow the actual header only.
                continue
            if status != 200:
                # No retry, challenge solving, cache-busting or endpoint guessing.
                raise ValueError(f"ARK public catalog returned HTTP {status}")
            raw = result if isinstance(result, (bytes, str)) else result.content
            raw = raw.encode("utf-8") if isinstance(raw, str) else raw
            if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
                raise ValueError("Expected bounded ARK JSON bytes")
            payload = json.loads(raw.decode("utf-8"), parse_constant=_invalid_number,
                                 parse_float=_finite_float)
            if not isinstance(payload, dict):
                raise ValueError("Expected ARK catalog object")
            captured_at = getattr(result, "captured_at", None) or datetime.now(timezone.utc).isoformat()
            captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if captured.tzinfo is None:
                raise ValueError("ARK capture timestamp requires a timezone")
            return payload, {"url": url, "status": status,
                "captured_at": captured_at,
                "original_http_status": status,
                "derived_from_original_verified": hasattr(result, "status_code"),
                "original_sha256": hashlib.sha256(raw).hexdigest()}


def _invalid_number(value):
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("ARK JSON numeric value is not finite")
    return parsed


def _identifier(row):
    value = row.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Missing or invalid ARK product/variation id")
    if row.get("product_id", value) != value:
        raise ValueError("Conflicting ARK id and product_id")
    return str(value)


def _safe_product(row):
    if not isinstance(row, dict):
        raise ValueError("Expected ARK product object")
    _identifier(row)
    safe = {key: value for key, value in row.items() if key in _PRODUCT_FIELDS}
    # A newly structured field could conceal an offer. Preserve only the
    # documented scalar shapes, with explicit dimensional/variant dictionaries.
    for key, value in safe.items():
        if key == "dimensions_cm":
            if not isinstance(value, dict) or set(value) - {"length", "width", "height"}:
                raise ValueError("Unexpected ARK dimension fields")
            if any(not _scalar(x) for x in value.values()):
                raise ValueError("Expected scalar ARK dimensions")
        elif key == "attributes":
            if not isinstance(value, dict) or any(not isinstance(x, str) for x in value.values()):
                raise ValueError("Expected string ARK variant attributes")
        elif key == "variation_skus":
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise ValueError("Expected ARK variation SKU strings")
        elif not _scalar(value):
            raise ValueError(f"Unexpected structured ARK metadata: {key}")
    specs = row.get("specs")
    if specs is not None:
        if not isinstance(specs, dict):
            raise ValueError("Expected ARK specs object")
        safe["specs"] = {key: value for key, value in specs.items() if key in _SPEC_FIELDS}
        for value in safe["specs"].values():
            if not (_scalar(value) or isinstance(value, list) and all(_scalar(x) for x in value)):
                raise ValueError("Expected scalar or scalar-list ARK specifications")
    gaps = row.get("spec_gaps")
    if gaps is not None:
        if not isinstance(gaps, list) or any(not isinstance(x, str) for x in gaps):
            raise ValueError("Expected ARK spec_gaps string array")
        safe["spec_gaps"] = [x for x in gaps if x in _SPEC_FIELDS]
    if "variations" in row:
        variations = row["variations"]
        if not isinstance(variations, list):
            raise ValueError("Expected ARK variations array")
        safe["variations"] = [_safe_product(x) for x in variations]
    return safe


def _scalar(value):
    return value is None or type(value) in (str, int, float, bool)


def _page(payload):
    values = [payload.get(k) for k in ("page", "per_page", "total")]
    if any(type(x) is not int for x in values):
        raise ValueError("ARK pagination must contain integer page/per_page/total")
    page, per_page, total = values
    if page < 1 or per_page < 1 or total < 0:
        raise ValueError("Invalid ARK pagination")
    rows = payload.get("products")
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        raise ValueError("Expected ARK products array")
    expected = min(per_page, max(0, total - (page - 1) * per_page))
    if len(rows) != expected:
        raise ValueError("ARK page length does not match advertised pagination")
    return page, per_page, total, rows


def _derived_source(payload, provenance):
    safe = {key: payload[key] for key in ("store", "dimension_unit", "page", "per_page", "total")
            if key in payload}
    safe["products"] = [_safe_product(row) for row in payload["products"]]
    content = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**provenance, "content": content, "media_type": "application/json",
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "source_kind": "derived_metadata", "redaction": {
            "method": "explicit_metadata_field_allowlist_v1",
            "excluded": ["offers", "price", "currency", "stock", "purchasable",
                         "backorders", "weight_g (shipping alias)", "shipping",
                         "lead_time", "checkout", "payment", "unrecognized_fields"],
            "original_response_retained": False}}


def load_ark(fetcher=None) -> dict:
    """Fetch all advertised pages (at most ten requests) as metadata-only evidence.

    The default transport spaces requests by at least one second. Injected
    fetchers must provide the same rate limiting, and must expose redirects
    rather than follow them. A callable may return bytes/text or an HTTP response.
    Byte/text-only test adapters provide unverified original HTTP provenance.
    An HTTP-response adapter may attach its original ``captured_at`` for replay.
    Variable parents are context; only actual simple/bundle/variation IDs become
    purchased_part records. Shared variant SKUs never merge distinct IDs.
    """
    owned = fetcher is None
    fetcher = fetcher or _Fetcher()
    reader, sources, top_rows = _Reader(fetcher), [], {}
    try:
        payload, provenance = reader.read(ARK_CATALOG_URL)
        page, per_page, total, rows = _page(payload)
        if page != 1 or (total + per_page - 1) // per_page > _MAX_REQUESTS:
            raise ValueError("ARK catalog exceeds bounded pagination budget")
        first_url = provenance["url"]
        expected_pages = max(1, (total + per_page - 1) // per_page)
        for expected_page in range(1, expected_pages + 1):
            if expected_page > 1:
                parsed = urlsplit(first_url)
                query = parse_qs(parsed.query)
                query.update(page=[str(expected_page)], per_page=[str(per_page)])
                url = urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))
                payload, provenance = reader.read(url)
                actual, size, count, rows = _page(payload)
                if (actual, size, count) != (expected_page, per_page, total):
                    raise ValueError("ARK pagination changed during fetch")
            sources.append(_derived_source(payload, provenance))
            for row in rows:
                identity = _identifier(row)
                if identity in top_rows:
                    raise ValueError("ARK product id repeated across pages")
                top_rows[identity] = (row, provenance)
    finally:
        if owned:
            fetcher.close()
    # Nested variants are genuine source rows too; full top-level rows take
    # precedence. Never transfer parent electrical facts to a child implicitly.
    candidates = dict(top_rows)
    nested_parents = {}
    for parent_id, (parent, source) in top_rows.items():
        for variation in parent.get("variations", []):
            identity = _identifier(variation)
            if variation.get("parent_id") != parent.get("id"):
                raise ValueError("ARK nested variation has inconsistent parent_id")
            if identity in nested_parents and nested_parents[identity]["id"] != parent["id"]:
                raise ValueError("ARK variation appears under conflicting parents")
            if identity in top_rows and top_rows[identity][0].get("parent_id") != parent["id"]:
                raise ValueError("ARK top-level variation has inconsistent parent_id")
            candidates.setdefault(identity, (variation, source))
            nested_parents[identity] = parent
    records = []
    for identity, (row, source) in candidates.items():
        if row.get("type") == "variable":
            continue
        if row.get("type") not in ("simple", "bundle", "variation", None):
            raise ValueError("Unsupported ARK product type")
        parent = nested_parents.get(identity)
        if row.get("type") is None and parent is None:
            raise ValueError("ARK product type missing")
        facts = _safe_product(row)
        facts["manufacturer"] = "ARK Electronics"
        facts["availability_unknown"] = True
        url = row.get("permalink") or (parent or {}).get("permalink")
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise ValueError("ARK product lacks HTTPS product link")
        record = {"external_id": identity, "name": row.get("name") or row.get("sku") or identity,
            "record_type": "purchased_part", "product_url": url, "source_url": source["url"],
            "facts": facts, "constraints": [], "api_access": "public", "order_path": url,
            "license_status": "unknown", "availability_unknown": True,
            "notes": [_NOTE], "fetched_at": source["captured_at"]}
        records.append(record)
    return {"task": "public-parts-feed", "provider": "ark", "records": records,
        "sources": sources, "blockers": [], "provider_metadata": {
            "name": "ARK Electronics", "url": "https://arkelectron.com/",
            "agent_docs_url": ARK_AGENT_DOCS_URL, "api_access": "public",
            "license_status": "unknown", "availability_unknown": True},
        "summary": {"records": len(records), "source_rows": len(top_rows),
                    "provider_name": "ARK Electronics", "agent_docs_url": ARK_AGENT_DOCS_URL,
                    "pages": len(sources), "http_requests": reader.requests,
                    "variable_parents_excluded": sum(r.get("type") == "variable" for r, _ in top_rows.values()),
                    "note": _NOTE}}


def live_ark_lookup(sku: str, fetcher=None) -> dict:
    """Return a fresh supplier offer response, without persistence or caching.

    Shared/inherited SKUs may resolve to a variable parent; callers must select
    an actual variation ID. This does not quote shipping, reserve stock or order.
    """
    if (not isinstance(sku, str) or not sku.strip() or len(sku) > 200
            or sku in (".", "..") or any(ord(char) < 32 for char in sku)):
        raise ValueError("A bounded nonempty ARK SKU is required")
    owned = fetcher is None
    fetcher = fetcher or _Fetcher()
    try:
        payload, source = _Reader(fetcher).read(
            f"{ARK_CATALOG_URL}/{quote(sku, safe='')}?ark_nocache=1")
    finally:
        if owned:
            fetcher.close()
    return {"provider": "ark", "sku": sku, "response": payload,
        "observed_at": source["captured_at"], "source_url": source["url"],
        "http_status": source["status"], "ephemeral": True,
        "note": "Supplier observation only; stock and prices can change. No offer is persisted."}
