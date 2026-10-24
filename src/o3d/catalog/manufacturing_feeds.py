"""Read the providers' public metadata feeds without uploading or ordering.

The returned bundle includes checksummed source documents. SendCutSend source
documents are derived metadata with offer fields removed and original checksums
retained; the original public response remains outside catalog storage.
``fetcher`` may be a callable accepting a URL or an object with the catalog
Fetcher ``get(url, allowed_hosts=...)`` interface. It must return raw bytes,
text, or an HTTP response exposing ``content`` and ``status_code``.
Bare bytes/text provide no HTTP evidence. Recorded responses can supply their
original capture timestamp through ``response.extensions['captured_at']``;
the default fetcher sets that timestamp when it receives the public response.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from urllib.parse import urlsplit

import httpx

from .http import USER_AGENT
from .offer_policy import strip_offer_fields

SENDCUTSEND_CATALOG_URL = "https://app.sendcutsend.com/sendcutsend-catalog.json"
SENDCUTSEND_SPECS_URL = "https://app.sendcutsend.com/sendcutsend-specs.json"
LASERHUB_MATERIALS_URL = "https://app.laserhub.com/api/public/materials"

_FEED_URLS = {SENDCUTSEND_CATALOG_URL, SENDCUTSEND_SPECS_URL, LASERHUB_MATERIALS_URL}
_MAX_BYTES = 20_000_000
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_SNAPSHOT_NOTE = (
    "Public metadata snapshot; listed materials and limits do not prove part feasibility. "
    "No current price, stock, or availability guarantee is provided."
)


def _derived_metadata(payload, source: dict) -> tuple[object, dict]:
    clean, redactions = strip_offer_fields(payload)
    content = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    derived = {**source, "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "source_kind": "derived_metadata", "original_sha256": source["sha256"],
        "original_http_status": source["status"],
        "derived_from_original_verified": source.get("http_verified") is True and source["status"] == 200,
        "verification": "verified_derived_metadata" if source.get("http_verified") is True else "observational_unverified",
        "redactions": redactions, "derivation": "Remove offer fields; preserve engineering metadata and provider identifiers."}
    return clean, derived


class _PublicFetcher:
    """One bounded GET per feed, with no redirects, retries, or auth discovery."""

    def __init__(self):
        self.client = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        self.last_request: float | None = None

    def get(self, url: str, *, allowed_hosts: set[str]):
        if url not in _FEED_URLS or urlsplit(url).hostname not in allowed_hosts:
            raise ValueError("URL is outside the public manufacturing feed allowlist")
        if self.last_request is not None:
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        with self.client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ValueError(f"Public feed returned HTTP {response.status_code}: {url}")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ValueError("Public manufacturing feed exceeds response size limit")
                chunks.append(chunk)
            # iter_bytes has decoded the transfer encoding. Preserve the actual
            # status and capture time without decompressing this body again.
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {"content-encoding", "content-length"}}
            return httpx.Response(response.status_code, content=b"".join(chunks), headers=headers,
                extensions={"captured_at": datetime.now(timezone.utc).isoformat(), "http_verified": True})

    def close(self):
        self.client.close()


def _read(fetcher, url: str) -> tuple[object, dict]:
    result = fetcher(url) if callable(fetcher) else fetcher.get(url, allowed_hosts={urlsplit(url).hostname})
    status = getattr(result, "status_code", None)
    if status is not None and (type(status) is not int or status != 200):
        raise ValueError(f"Public feed returned HTTP {status}: {url}")
    raw = result if isinstance(result, (bytes, str)) else result.content
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
        raise ValueError("Expected bounded raw public feed bytes or text")
    content = raw.decode("utf-8")  # Never replace bytes and silently change source provenance.
    extensions = getattr(result, "extensions", {})
    extensions = extensions if isinstance(extensions, dict) else {}
    captured_at = extensions.get("captured_at") or getattr(result, "captured_at", None)
    valid_capture_time = False
    if isinstance(captured_at, str):
        try:
            valid_capture_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).tzinfo is not None
        except ValueError:
            pass
    verified = status == 200 and valid_capture_time and extensions.get("http_verified") is not False
    source = {
        "url": url, "status": status, "sha256": hashlib.sha256(raw).hexdigest(),
        "content": content, "media_type": "application/json",
        "captured_at": captured_at, "http_verified": verified,
        "verification": "verified_capture" if verified else "observational_unverified",
    }
    # Reject NaN/Infinity rather than persisting non-JSON numeric facts.
    payload = json.loads(content, parse_constant=lambda value: _invalid_json_number(value))
    return payload, source


def _invalid_json_number(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _rows(payload: dict, field: str) -> list[dict]:
    value = payload.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Expected {field} to be an array of objects")
    return value


def _identifier(value, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise ValueError(f"Missing or invalid provider {label}")
    return str(value)


def _metadata(payload: dict) -> dict:
    value = payload.get("_meta")
    return value if isinstance(value, dict) else {}


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        if not _NUMBER.fullmatch(value.strip()):
            return None
        value = float(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


# Only these scalar fields have unambiguous dimensional meanings in this schema.
# Sizes such as "36 x 46", gauges, thread labels and material properties stay raw.
_LENGTH_FIELDS = {
    "thickness", "thickness_tolerance_positive", "thickness_tolerance_negative",
    "min_hole_size", "min_bridge_size", "min_hole_to_edge", "min_part_length", "min_part_width",
    "cut_tolerance_positive", "cut_tolerance_negative", "tab_slot_tolerance",
    "bend_deduction", "bend_radius", "bend_relief_depth", "die_width", "effective_bend_radius",
    "max_bend_length", "max_joggle", "min_joggle", "min_corner_relief_distance_from_bend_line",
    "min_flange_length_after_bend", "min_flange_length_before_bend", "min_length_center_bend_line_acute",
    "countersink_max_major", "countersink_min_minor", "min_hole_center_to_material_edge",
    "min_hole_cl_to_bend_line", "min_required_hole_size", "min_distance_cl_hole_to_edge",
    "max_4_sided_box_flange_height", "hole_size", "thickness_above_material",
    "min_hole_cl_distance_between_same_hardware", "min_hole_to_edge",
}
_ANGLE_FIELDS = {"max_bend_angle", "min_bend_angle", "bend_angle_tolerance_over_24", "bend_angle_tolerance_up_to_24"}


def _sendcutsend_constraints(spec: dict, meta: dict, index: int) -> list[dict]:
    note = meta.get("units_note", "")
    if not isinstance(note, str):
        return []
    inches = "All dimensions are in inches unless otherwise noted." in note
    degrees = "Angles are in degrees." in note
    constraints = []
    for group, fields in spec.items():
        if not isinstance(fields, dict) or group == "material_properties":
            continue
        for field, value in fields.items():
            unit = "in" if inches and field in _LENGTH_FIELDS else "deg" if degrees and field in _ANGLE_FIELDS else None
            numeric = _number(value)
            if unit is not None and numeric is not None:
                constraints.append({"key": f"{group}.{field}", "value": numeric, "unit": unit,
                    "source_field": f"materials[{index}].{group}.{field}", "source_url": SENDCUTSEND_SPECS_URL})
    return constraints


def _base(external_id: str, name: str, record_type: str, url: str, order_path: str) -> dict:
    return {"external_id": external_id, "name": name, "record_type": record_type,
        "product_url": order_path, "source_url": url, "facts": {}, "constraints": [],
        "api_access": "public", "order_path": order_path, "license_status": "unknown",
        "notes": [_SNAPSHOT_NOTE]}


def load_sendcutsend(fetcher=None) -> dict:
    """Load exact SKUs and finish values; engineering joins require a unique SKU."""
    owned = fetcher is None
    fetcher = fetcher or _PublicFetcher()
    try:
        catalog, catalog_source = _read(fetcher, SENDCUTSEND_CATALOG_URL)
        specs, specs_source = _read(fetcher, SENDCUTSEND_SPECS_URL)
    finally:
        if owned:
            fetcher.close()
    if not isinstance(catalog, dict) or not isinstance(specs, dict):
        raise ValueError("Expected SendCutSend catalog and specifications objects")
    # Hardware may be stored as metadata, but a bundled source body must not
    # reintroduce price or availability snapshots omitted from record facts.
    catalog, catalog_source = _derived_metadata(catalog, catalog_source)
    specs, specs_source = _derived_metadata(specs, specs_source)
    engineering = defaultdict(list)
    for index, row in enumerate(_rows(specs, "materials")):
        if row.get("sku") is not None:
            engineering[str(row["sku"])].append((index, row))
    materials = _rows(catalog, "materials")
    counts = Counter(str(row.get("sku")) for row in materials)
    records, warnings = [], []
    for field, record_type, id_field in (("materials", "material", "sku"), ("hardware", "hardware", "sku"),
                                          ("finish_options", "finish", "value")):
        for row in _rows(catalog, field):
            external_id = _identifier(row.get(id_field), id_field)
            name = row.get("name") or row.get("title") or row.get("color") or external_id
            record = _base(external_id, name, record_type, SENDCUTSEND_CATALOG_URL, "https://app.sendcutsend.com")
            record["facts"] = {**row, "catalog_meta": _metadata(catalog)}
            if record_type == "material":
                record.update(process=row.get("cutting_process"), material=row.get("name"))
                matches = engineering.get(external_id, [])
                if len(matches) == 1 and counts[external_id] == 1:
                    index, spec = matches[0]
                    record["facts"].update(engineering_specs=spec, engineering_meta=_metadata(specs))
                    record["constraints"] = _sendcutsend_constraints(spec, _metadata(specs), index)
                    record["source_urls"] = [SENDCUTSEND_CATALOG_URL, SENDCUTSEND_SPECS_URL]
                    general = spec.get("general_specs")
                    if isinstance(general, dict) and general.get("learn_more_url"):
                        record["product_url"] = general["learn_more_url"]
                elif matches:
                    warning = f"Ambiguous engineering SKU {external_id}; specifications not joined."
                    record["notes"].append(warning)
                    warnings.append(warning)
            elif record_type == "finish":
                record["process"] = row.get("finish_type")
            records.append(record)
    return {"task": "public-manufacturing-feed", "provider": "sendcutsend", "records": records,
        "sources": [catalog_source, specs_source], "blockers": [], "warnings": warnings,
        "summary": {"records": len(records), "by_type": dict(Counter(r["record_type"] for r in records)),
            "catalog_generated_at": _metadata(catalog).get("generated_at"),
            "redacted_offer_fields": sum(len(source["redactions"]) for source in (catalog_source, specs_source)),
            "note": _SNAPSHOT_NOTE}}


def load_laserhub(fetcher=None) -> dict:
    """Load material/type IDs and source-authored variations without expanding joins.

    The public material response declares no dimensional unit. Its dimensions
    remain raw facts, including exact variation tuples and structured ranges.
    It is public metadata access, not an ordering API or feasibility result.
    """
    owned = fetcher is None
    fetcher = fetcher or _PublicFetcher()
    try:
        materials, source = _read(fetcher, LASERHUB_MATERIALS_URL)
    finally:
        if owned:
            fetcher.close()
    if not isinstance(materials, list) or any(not isinstance(row, dict) for row in materials):
        raise ValueError("Expected Laserhub public materials array")
    records, by_id = [], {}
    duplicates = 0
    for source_index, row in enumerate(materials):
        material_id = _identifier(row.get("id"), "material ID")
        manufacturing_type = _identifier(row.get("manufacturingType"), "manufacturing type")
        # The same numeric ID occurs under multiple source manufacturing types.
        external_id = f"{manufacturing_type}:{material_id}"
        if external_id in by_id:
            existing = by_id[external_id]
            existing["source_row_indices"].append(source_index)
            if row == existing["facts"]:
                duplicates += 1
            else:
                # Preserve conflicting rows under the same genuine identifier.
                # They are alternatives, never merged ranges or Cartesian tuples.
                existing.setdefault("source_record_alternatives", []).append(dict(row))
                existing["notes"].append("Provider repeats this material/type identifier with different metadata; alternatives retained without merging.")
            continue
        locale = row.get("locale")
        locale = locale if isinstance(locale, dict) else {}
        name = locale.get("en") or locale.get("de") or next((x for x in locale.values() if isinstance(x, str) and x), None)
        record = _base(external_id, name or external_id, "material", LASERHUB_MATERIALS_URL, "https://app.laserhub.com")
        record.update(process=manufacturing_type, material=name or row.get("group"), facts=dict(row))
        record["source_row_indices"] = [source_index]
        record["notes"].append("The feed declares no dimensional units; numeric dimensions are retained as raw facts. Public material listing is not an ordering API.")
        records.append(record)
        by_id[external_id] = record
    return {"task": "public-manufacturing-feed", "provider": "laserhub", "records": records,
        "sources": [source], "blockers": [], "summary": {"records": len(records), "source_rows": len(materials),
            "duplicate_source_rows": duplicates,
            "by_manufacturing_type": dict(Counter(r["process"] for r in records)),
            "source_variations": sum(len(r.get("variations") or []) for r in materials), "note": _SNAPSHOT_NOTE}}
