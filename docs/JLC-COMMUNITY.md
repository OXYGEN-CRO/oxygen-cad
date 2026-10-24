# JLCPCB metadata through the CDFER community snapshot

`o3d.catalog.community_jlc` provides an alternate component metadata source while
official JLCPCB Components API access is pending. This is **unofficial community
data**, not an authenticated JLCPCB API result. Provider identity remains
`jlcpcb` / `JLCPCB`; origin is explicitly `community-cdfer`.

Source project: <https://github.com/CDFER/jlcpcb-parts-database>

Public CSV: <https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components-basic-preferred.csv>

## Observed capture, 2026-10-24

The actual public GET returned HTTP 200. Its retained local capture was:

| Evidence | Value |
| --- | --- |
| Original bytes | 1,251,062 |
| Original SHA-256 | `9c213b2ae34979048371e233d5f07aa938f7ca0fd97219d5ea2c6ef200e3d9af` |
| Capture time | `2026-10-24T21:38:32.350499+00:00` |
| HTTP Last-Modified | `Sat, 24 Oct 2026 05:56:45 GMT` |
| CSV observations | 1,577 |
| Distinct LCSC SKUs | 1,551 |
| Repeated SKU rows | 26 |
| Earliest component fetched_at | `2026-09-21T17:23:20+00:00` |
| Latest component fetched_at | `2026-10-21T15:57:30+00:00` |

These are observations of this specific capture; later downloads can differ.
The CSV deployment and download dates do not imply fresh component observations.
Original `fetched_at` epoch strings remain in `facts`; the top-level `fetched_at`
is their UTC conversion, and `snapshot_captured_at` records the download time.

## Load and replay

The default local capture paths are outside the repository:

```text
~/.local/share/oxygen-parts/community-jlc/basic-preferred.csv
~/.local/share/oxygen-parts/community-jlc/basic-preferred.meta.json
```

```python
from o3d.catalog.community_jlc import fetch_csv, load_cdfer_csv

# No network or database writes: load the existing local CSV and sibling proof.
bundle = load_cdfer_csv()

# Explicit alternate path and original HTTP metadata are also accepted.
bundle = load_cdfer_csv("/path/to/capture.csv", metadata={
    "url": "https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components-basic-preferred.csv",
    "status": 200,
    "captured_at": "2026-10-24T21:38:32.350499+00:00",
    "sha256": "9c213b2ae34979048371e233d5f07aa938f7ca0fd97219d5ea2c6ef200e3d9af",
    "bytes": 1251062,
})

# Optional refresh: exactly one fixed public GET; bytes are not saved implicitly.
raw, original_http_metadata = fetch_csv()
bundle = load_cdfer_csv(raw, original_http_metadata)

# The caller controls persistence using the existing ManufacturingCatalog store.
# report = manufacturing_catalog.ingest_bundle(bundle)
```

`fetch_csv` has a 20 MB decoded byte limit, does not follow redirects, and does
not retry. Its injected fetcher accepts the fixed URL and returns bytes/text or
an HTTP response. Recorded HTTP responses must expose their original capture
time in `response.extensions['captured_at']`; the replay time is never substituted.
Bare fixture bytes have no verified HTTP provenance.

The replay generated from the observed capture is available locally at
`/tmp/oxygen-jlc-cdfer-bundle.json`. It contains metadata only; no source CSV or
datasheet binaries need to enter the repository or catalog object store.

## Record semantics

- Bare LCSC numeric IDs become `C` plus the original digits. `facts.lcsc` retains
  the original source ID; `facts.sku` is the normalized component SKU. `mfr` is
  retained as the source manufacturer part number, without inferred specifications.
- JSON `attributes` and `attrition` preserve their original nested values,
  including booleans and nulls. Empty JSON cells and JSON null become null.
  Malformed JSON, duplicate object keys, non-finite numbers, malformed CSV and
  invalid IDs or timestamps fail explicitly.
- Other source columns remain source values rather than inferred measurements.
  Procurement fields such as `Min Order Qty` and `attrition.minPurchaseNum` remain
  independent even when they disagree.
- Repeated SKUs remain distinct source observations. Their external IDs use
  `C<id>:source-variant:<SHA-256 of clean facts>:<occurrence>`. The occurrence
  suffix separates exact repeats. Distinct facts keep stable IDs when CSV rows
  reorder. Repeated observations do not imply additional distinct components.
- Shared `offer_policy.strip_offer_fields` removes stock, prices, last-on-stock
  and nested offer values from both records and the derived source body.
  Availability remains unknown; the snapshot is unsuitable for live quotations.
- Product and order URLs remain null because this adapter does not establish a
  verified official purchase-page URL template. Source datasheet URLs remain
  references; the adapter never fetches them.
- `license_status` and CAD availability remain unknown. A code repository's MIT
  license does not establish rights over component data or linked CAD/documents.
  This adapter downloads no CAD or datasheet binaries.

The derived source has its own content hash, plus the original CSV hash,
HTTP status and capture time. Supplied original metadata must match the actual
input bytes and fixed source URL. A valid original timestamp, matching original
hash and HTTP 200 are required to claim verified derived metadata. Hash or byte
count mismatches fail; incomplete HTTP proof stays observational and cannot
satisfy the catalog's verified-source admission rule.

## Validation

```sh
PYTHONPATH=src /tmp/oxygen-parts-venv/bin/python -m pytest -q tests/catalog/test_community_jlc.py
```

The 43 offline tests cover identifiers, duplicate observations, JSON preservation
and errors, conflicting procurement facts, recursive offer removal, timestamps,
original-byte verification, bounded downloads and unverified fixture provenance.
The actual 1,577-row capture was additionally replayed through the public record
schema and shared offer scanner. This establishes metadata compatibility; it does
not establish official API approval, live inventory, factual engineering accuracy,
CAD availability or reuse rights.
