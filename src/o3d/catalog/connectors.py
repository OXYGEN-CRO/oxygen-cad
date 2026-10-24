"""Two permitted sources; stable keys, immutable captures and resumable paging."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from psycopg.types.json import Jsonb

from .db import Catalog
from .http import Fetcher
from .models import Asset, Part, Provider, Spec, utcnow

PROVIDERS = [
    Provider(id="adafruit-cad", name="Adafruit CAD Parts", website="https://www.adafruit.com",
        access="stored", license="MIT",
        terms_url="https://github.com/adafruit/Adafruit_CAD_Parts/blob/main/LICENSE",
        notes="Only the MIT CAD repository is ingested. Storefront API needs separate permission; stock/price unknown."),
    Provider(id="step-parts", name="step.parts", website="https://www.step.parts",
        access="stored", license="MIT for original metadata; asset licenses vary",
        terms_url="https://github.com/earthtojake/step.parts/blob/main/THIRD_PARTY_NOTICES.md",
        notes="Geometry directory, not a supplier. Per-file rights required; no stock or price claim."),
    Provider(id="digikey", name="DigiKey", website="https://www.digikey.com", access="live-only",
        license="Provider API terms", terms_url="https://www.digikey.com/en/terms-and-conditions/api-user-agreement",
        notes="No persistent ingestion connector. Requires approved live adapter and credentials."),
    Provider(id="misumi", name="MISUMI", website="https://www.misumi.com", access="permission-required",
        license="Provider terms", terms_url="https://us.misumi-ec.com/copyright/",
        notes="No bulk CAD ingestion without a supplier agreement."),
]


def register(catalog: Catalog):
    for provider in PROVIDERS:
        catalog.register_provider(provider)


def specifications(attributes: dict) -> list[Spec]:
    """Normalize only explicit unit suffixes; preserve every original scalar fact."""
    suffixes = {"Mm": ("mm", 1), "Cm": ("mm", 10), "Kg": ("kg", 1), "G": ("kg", .001),
        "Nm": ("N*m", 1), "Rpm": ("rpm", 1), "V": ("V", 1), "A": ("A", 1), "W": ("W", 1), "Deg": ("deg", 1)}
    result = []
    for original, value in attributes.items():
        if value is None or not isinstance(value, (str, float, int, bool)):
            continue
        base, unit, scale = original, None, 1
        if type(value) in (int, float):
            for suffix, (candidate_unit, candidate_scale) in suffixes.items():
                if original.endswith(suffix) and len(original) > len(suffix):
                    base, unit, scale = original[:-len(suffix)], candidate_unit, candidate_scale
                    break
        key = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower().replace("-", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            continue
        normalized = value * scale if type(value) in (int, float) else value
        result.append(Spec(key=key, value=normalized, unit=unit, source_field=f"attributes.{original}",
            evidence="derived" if scale != 1 else "source"))
    # Preserve ambiguous duplicate unit variants rather than silently choosing one.
    keys = [s.key for s in result]
    for spec in result:
        if keys.count(spec.key) > 1:
            spec.key += "_" + re.sub(r"[^a-z0-9]", "", (spec.unit or "unitless").lower())
    return result


def step_part(record: dict) -> Part:
    attrs = record.get("attributes", {})
    license_name, license_url, permitted = "unknown", None, False
    source = record.get("stepSource", "")
    parsed = urlsplit(source)
    if parsed.hostname == "raw.githubusercontent.com" and parsed.path.startswith("/adafruit/Adafruit_CAD_Parts/"):
        revision = parsed.path.split("/")[3]
        if re.fullmatch(r"[0-9a-f]{40}", revision):
            license_name, license_url, permitted = "MIT", f"https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/{revision}/LICENSE", True
    assets = [Asset(url=record["stepUrl"], format="step", sha256=record.get("sha256"),
        byte_size=record.get("byteSize"), license=license_name, license_url=license_url,
        attribution="step.parts; upstream geometry: " + (source or "not established; see per-file provenance"),
        download_allowed=permitted, scope=attrs.get("geometryRepresentation", "unverified"))]
    for field, fmt, kind in (("glbUrl", "glb", "cad"), ("pngUrl", "png", "image")):
        if record.get(field):
            assets.append(Asset(url=record[field], format=fmt, kind=kind,
                attribution="step.parts generated preview; per-file license not verified"))
    return Part(external_id=record["id"], name=record["name"], description=record.get("description", ""),
        category=record.get("category", "unclassified"), family=record.get("family"),
        manufacturer=attrs.get("manufacturer"), mpn=attrs.get("mpn"), supplier_sku=attrs.get("sku"),
        product_url=record.get("productPage"), procurement="unknown",
        aliases=record.get("aliases", []), tags=record.get("tags", []),
        attributes={**attrs, "source_standard": record.get("standard"), "upstream_step_source": source},
        specs=specifications(attrs), assets=assets)


def adafruit_parts(tree: dict) -> list[Part]:
    if tree.get("truncated"):
        raise ValueError("GitHub tree was truncated; refusing incomplete catalog")
    revision = tree["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a pinned Git tree revision")
    groups = defaultdict(list)
    for entry in tree["tree"]:
        if entry["type"] == "blob" and "/" in entry["path"]:
            groups[entry["path"].split("/")[0]].append(entry)
    result = []
    formats = {"step", "stp", "stl", "f3d", "3mf", "iges", "igs", "obj", "sldprt", "pdf", "png", "jpg", "gif"}
    for folder, entries in sorted(groups.items()):
        match = re.match(r"^(\d+)\s+(.+)", folder)
        if not match:
            continue
        sku, name = match.groups()
        assets = []
        for entry in entries:
            fmt = entry["path"].rsplit(".", 1)[-1].lower()
            if fmt not in formats:
                continue
            assets.append(Asset(url=f"https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/{revision}/{quote(entry['path'])}",
                format=fmt, kind="drawing" if fmt == "pdf" else "image" if fmt in {"png", "jpg", "gif"} else "cad",
                byte_size=entry.get("size"), license="MIT",
                license_url=f"https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/{revision}/LICENSE",
                attribution=f"Copyright (c) 2016 Adafruit Industries. Repository path: {entry['path']}", download_allowed=True))
        if not any(a.kind == "cad" for a in assets):
            continue
        # Folder/SKU label is sourced; no dimensions, stock or mounting data inferred from names.
        result.append(Part(external_id=folder, name=name, family="adafruit", category="unclassified",
            manufacturer=None, supplier_sku=sku, product_url=f"https://www.adafruit.com/product/{sku}",
            procurement="off_the_shelf", tags=["adafruit", "cad-model"], aliases=[folder, f"Adafruit {sku}"],
            identifiers={"adafruit_product_id": sku}, attributes={"repository_path": folder, "revision": revision}, assets=assets))
    return result


def ingest(catalog: Catalog, provider: str, *, max_parts: int | None = None, category: str | None = None,
           resume: str | None = None, fetcher: Fetcher | None = None):
    register(catalog)
    catalog.require_stored(provider)
    if provider not in {"step-parts", "adafruit-cad"}:
        raise ValueError("No ingestion connector for this provider")
    if max_parts is not None and max_parts < 1:
        raise ValueError("max_parts must be positive")
    owned = fetcher is None
    fetcher = fetcher or Fetcher()
    processed, page = 0, 1
    if resume:
        old = catalog.conn.execute("SELECT * FROM ingestion_runs WHERE id=%s AND provider_id=%s", (resume, provider)).fetchone()
        if not old or old["status"] == "completed":
            raise ValueError("Resume requires an incomplete run for this provider")
        if old["checkpoint"].get("category") != category:
            raise ValueError("Resume category must match the original run")
        page = old["checkpoint"].get("page", 1)
    run_id = str(uuid4())
    catalog.conn.execute("INSERT INTO ingestion_runs(id,provider_id,checkpoint) VALUES (%s,%s,%s)",
        (run_id, provider, Jsonb({"page": page, "category": category, "resume_from": resume})))
    try:
        if provider == "adafruit-cad":
            url = "https://api.github.com/repos/adafruit/Adafruit_CAD_Parts/git/trees/main?recursive=1"
            raw = fetcher.get(url, allowed_hosts={"api.github.com"})
            document = catalog.document(provider, url, raw)
            parts = adafruit_parts(json.loads(raw))
            if category:
                raise ValueError("Adafruit CAD source has no source-authored category facet")
            for part in parts[:max_parts]:
                catalog.upsert(provider, part, document)
                processed += 1
        else:
            while True:
                query = {"page": page, "pageSize": 500}
                if category:
                    query["category"] = category
                url = "https://api.step.parts/v1/parts?" + urlencode(query)
                raw = fetcher.get(url, allowed_hosts={"api.step.parts"})
                payload = json.loads(raw)
                if payload.get("page") != page or not isinstance(payload.get("items"), list):
                    raise ValueError("Unexpected provider pagination response")
                document = catalog.document(provider, url, raw)
                with catalog.conn.transaction():
                    for record in payload["items"]:
                        if max_parts and processed >= max_parts:
                            break
                        catalog.upsert(provider, step_part(record), document)
                        processed += 1
                    page += 1
                    # Only advance after the whole page commits. Replays are idempotent.
                    catalog.conn.execute("UPDATE ingestion_runs SET processed=%s,checkpoint=%s WHERE id=%s",
                        (processed, Jsonb({"page": page, "category": category}), run_id))
                if not payload.get("hasNextPage") or (max_parts and processed >= max_parts):
                    break
                if not payload["items"]:
                    raise ValueError("Empty page claims a following page")
        catalog.conn.execute("UPDATE ingestion_runs SET status='completed',processed=%s,finished_at=now() WHERE id=%s", (processed, run_id))
        return {"run_id": run_id, "provider": provider, "processed": processed, "status": "completed"}
    except Exception as error:
        catalog.conn.execute("UPDATE ingestion_runs SET status='failed',processed=%s,error=%s,finished_at=now() WHERE id=%s",
            (processed, f"{type(error).__name__}: {error}"[:1000], run_id))
        raise
    finally:
        if owned:
            fetcher.close()
