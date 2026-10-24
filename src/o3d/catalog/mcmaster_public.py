"""User-selected public McMaster family pages; metadata only, no CAD/offer mirror.

McMaster's terms restrict external reuse. Public access does not grant that right.
These local research records preserve that restriction, and never authorize CAD.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from .http import Fetcher
from .models import Part, Provider, Spec, utcnow
from .offer_policy import contains_offer_values

PROVIDER = Provider(id="mcmaster-public", name="McMaster-Carr public metadata research",
    website="https://www.mcmaster.com", access="stored",
    license="Proprietary; external reuse restricted; no CAD permission granted",
    terms_url="https://www.mcmaster.com/termsandconditions/",
    notes="User-requested local metadata research. Not a licensed public catalog. No offers or CAD copied. "
          "External-service reuse requires appropriate supplier rights. Stock, CAD and exact fit unknown.")


def validate_url(url):
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != "www.mcmaster.com" or p.username or p.password
            or p.port not in (None, 443) or p.query or p.fragment
            or not re.fullmatch(r"/(?:products/)?[a-z0-9][a-z0-9%-]*/?", p.path)):
        raise ValueError("Expected a public McMaster family URL, without queries or private paths")
    if p.path.strip("/").lower() in {"orders", "order-history", "activity", "login", "catalog", "mv", "mx"}:
        raise ValueError("Private or excluded McMaster path")


class _JSONLD(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.current = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.active, self.current = True, []

    def handle_data(self, data):
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.documents.append(json.loads("".join(self.current)))
            self.active = False


def extract(raw: bytes, source_url: str):
    """Preserve SKU-level PropertyValues; never copy a family claim onto a variant."""
    validate_url(source_url)
    parser = _JSONLD()
    parser.feed(raw.decode("utf-8-sig"))
    records = []

    def walk(node, group=None):
        if isinstance(node, list):
            for child in node:
                walk(child, group)
        elif isinstance(node, dict):
            kind = node.get("@type")
            if kind == "ProductGroup":
                group = {"name": node.get("name"), "id": node.get("@id")}
            if kind == "Product" and node.get("sku"):
                sku = node["sku"]
                if not isinstance(sku, str) or not re.fullmatch(r"[0-9]{3,6}[A-Z][0-9]{1,5}", sku):
                    raise ValueError("Unexpected McMaster SKU")
                url = node.get("url")
                if url not in {f"https://www.mcmaster.com/{sku}", f"https://www.mcmaster.com/{sku}/"}:
                    raise ValueError("SKU/product URL mismatch")
                properties = []
                for prop in node.get("additionalProperty", []):
                    if not isinstance(prop, dict):
                        continue
                    name, value = prop.get("name"), prop.get("value")
                    if (not isinstance(name, str) or not isinstance(value, (str, int, float, bool))
                            or contains_offer_values({name: value})):
                        continue
                    properties.append({"name": name, "value": value})
                records.append({"sku": sku, "url": url,
                    "name": node.get("name") or (group or {}).get("name") or sku,
                    "group": group, "properties": properties})
            for key in ("@graph", "hasVariant", "mainEntity", "itemListElement", "item"):
                if key in node:
                    walk(node[key], group)

    walk(parser.documents)
    # Duplicate occurrences can disagree. Retain observations rather than
    # silently replacing one SKU's dimensions with a later occurrence.
    grouped = {}
    for row in records:
        existing = grouped.setdefault(row["sku"], {**row, "observations": []})
        if row not in existing["observations"]:
            existing["observations"].append(row)
    return list(grouped.values())


def as_part(row, source_url):
    observations = row["observations"]
    properties = []
    for obs in observations:
        for prop in obs["properties"]:
            if prop not in properties:
                properties.append(prop)
    specs = []
    for index, prop in enumerate(properties):
        # Original headers/strings retain units and fractions. No guessed unit,
        # conversion, manufacturer, material, thread size or mounting port.
        key = re.sub(r"[^a-z0-9]+", "_", prop["name"].lower()).strip("_")
        if not key or not key[0].isalpha():
            key = "property_" + key
        specs.append(Spec(key=f"{key}_{index}", value=prop["value"],
            source_field=f"sku_properties[{index}]"))
    return Part(external_id=row["sku"], supplier_sku=row["sku"], name=row["name"],
        product_url=row["url"], procurement="off_the_shelf", category="unclassified",
        identifiers={"mcmaster_part_number": row["sku"]}, aliases=[row["sku"]],
        tags=["public-metadata", "local-research", "cad-unresolved"],
        attributes={"source_family_url": source_url, "sku_properties": properties,
            "source_observations": observations, "reuse_status": "external_reuse_restricted",
            "cad_status": "not_retrieved", "sku_revision": "unverified"}, specs=specs)


def ingest(catalog, urls, *, fetcher=None):
    if not isinstance(urls, list) or not 1 <= len(urls) <= 30 or len(set(urls)) != len(urls):
        raise ValueError("Supply 1–30 distinct public family URLs")
    for url in urls:
        validate_url(url)
    owned = fetcher is None
    if owned:
        fetcher = Fetcher(interval=2)
        fetcher.client.headers["User-Agent"] = "OxygenPartsBot/1.0 (+https://github.com/OXYGEN-CRO/oxygen-cad)"
    catalog.register_provider(PROVIDER)
    report = {"provider": PROVIDER.id, "pages": [], "unique_skus": 0, "duplicate_skus_skipped": 0,
        "cad_files": 0, "offers_retained": False, "scope": "user_selected_family_pages"}
    seen = set()
    try:
        for url in urls:
            raw = fetcher.get(url, allowed_hosts={"www.mcmaster.com"}, max_bytes=25_000_000)
            rows = extract(raw, url)
            if not rows:
                report["pages"].append({"url": url, "records": 0, "status": "no_structured_products"})
                continue
            derived = {"source_url": url, "captured_at": utcnow().isoformat(),
                "original_sha256": hashlib.sha256(raw).hexdigest(),
                "source_kind": "derived_sku_metadata", "original_response_retained": False,
                "excluded": ["offers", "prices", "stock", "CAD", "images", "family_descriptions"],
                "products": rows}
            encoded = json.dumps(derived, sort_keys=True, allow_nan=False).encode()
            document = catalog.document(PROVIDER.id, url, encoded)
            for row in rows:
                if row["sku"] in seen:
                    report["duplicate_skus_skipped"] += 1
                    continue
                catalog.upsert(PROVIDER.id, as_part(row, url), document)
                seen.add(row["sku"])
            report["pages"].append({"url": url, "records": len(rows), "document_id": document,
                "original_sha256": derived["original_sha256"], "status": "imported"})
    finally:
        if owned:
            fetcher.close()
    report["unique_skus"] = len(seen)
    return report
