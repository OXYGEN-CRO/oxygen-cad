# Parts catalog for coding agents

The catalog now also supports [manufacturing providers and real public feeds](MANUFACTURING.md),
including custom-part capability/material search, quote handoffs and community
JLC component snapshots. The sections below describe the original CAD dataset;
their first-seed counts do not include the newer supply datasets.

PostgreSQL holds component identity, specifications, supplier links, provenance,
CAD references and embeddings. A content-addressed object store holds immutable
source responses and downloaded files. The initial implementation uses local
disk; its keys can move to S3 without changing part IDs.

This is a discovery service for the idea → sourced parts → custom geometry loop.
It does not establish physical compatibility or promise that a catalog item is
currently in stock. Read those states in each response.

## Start with Docker

The catalog has its own lightweight runtime; it does not build the CAD engine.
Requires Docker Compose. Choose a new password before startup:

```sh
python3 -c "import secrets; print('O3D_CATALOG_PASSWORD=' + secrets.token_hex(24))" > .env
chmod 600 .env
docker compose -f catalog.compose.yaml up -d --build --wait
docker compose -f catalog.compose.yaml exec catalog python -m o3d.catalog ingest adafruit-cad
docker compose -f catalog.compose.yaml exec catalog python -m o3d.catalog ingest step-parts
docker compose -f catalog.compose.yaml exec catalog python -m o3d.catalog embed --limit 100000
```

Open `http://localhost:8844/docs` for the API and request schemas. PostgreSQL
listens on localhost:55434. Named volumes persist the database, source snapshots,
CAD cache and local embedding model. `docker compose ... down` preserves data;
`down -v` deletes it. The service is intended for localhost; configure authentication
and deployment controls before exposing it to other machines.

The first full seed on 2026-10-24 loaded 478 Adafruit records and 16,847 step.parts
records, with 53,736 scalar specifications and 52,318 asset references (including
images and previews). These are provider records, not a deduplicated count of
purchasable products. Only requested CAD binaries are downloaded. Full loading
uses 35 source JSON responses, rather than crawling thousands of product pages.

## Use an existing PostgreSQL database

Requires PostgreSQL 17 with pgvector and pg_trgm. A database administrator enables
both extensions once. The application role needs access to its dedicated database
and permission to create the `o3d_catalog` schema/tables; it needs no superuser
privileges after extensions are installed.

```sh
python -m pip install -e '.[catalog-semantic]'
export O3D_CATALOG_DATABASE_URL='postgresql://USER:PASSWORD@localhost:5432/DATABASE'
export O3D_CATALOG_DATA="$HOME/.local/share/oxygen-parts"
o3d-parts init
o3d-parts ingest adafruit-cad
o3d-parts ingest step-parts
o3d-parts embed --limit 100000
o3d-parts serve
```

`pip install -e '.[catalog]'` omits the local embedding runtime. All lexical and
numeric retrieval still works. `python -m o3d.catalog` is equivalent to
`o3d-parts`. Native CAD installs also expose `o3d parts`; the standard CAD Docker
image does not install the optional database dependencies. Use the catalog runtime
for discovery and your full CAD install for importing references.

## Discover and inspect

Every command returns JSON. Do not parse formatted terminal tables.

```sh
o3d-parts status
o3d-parts search 'Feather RP2040' --provider adafruit-cad --downloadable
o3d-parts search 'small robot joint actuator' --hybrid
o3d-parts search --filters examples/catalog-search.json
o3d-parts get PART_UUID
```

The JSON search contract supports query, category, family, manufacturer, provider,
tags, procurement classification, exact JSON attributes, CAD availability and
numeric bounds. Example:

```json
{
  "q": "screw",
  "category": "fastener",
  "attributes": {"thread": "M3"},
  "numeric": [{"key": "length", "unit": "mm", "min": 6, "max": 12}],
  "cad_required": true,
  "limit": 10
}
```

Unknown values fail a requested numeric constraint. Units must match the normalized
specification unit exactly. Source fields with explicit unit suffixes are normalized
to mm, kg, N*m, V, A, W, rpm or degrees; all original attributes remain available.
A bare number without an explicit source unit is not promoted to a physical unit.

Lexical retrieval combines exact MPN/SKU/alias matches, Postgres full-text search
and trigram name similarity. Hybrid mode combines lexical and local BGE-small
384-dimensional semantic ranks using reciprocal rank fusion. The same hard
filters apply to both channels. A semantically similar motor cannot pass an unmet
torque constraint. Hybrid responses report semantic coverage; run `embed` again
after source changes. No external embedding API is called, and no paid API key is
required. First use downloads the open model from Hugging Face; subsequent
inference runs locally on CPU. Exact filtered vector ranking is used at this seed
size. A pgvector HNSW index is provisioned, but constrained search currently ranks
the full eligible set to avoid ANN underfilling. Large-scale retrieval needs measured
recall/latency tuning rather than a blanket performance claim.

Embedding uses two CPU threads by default. `O3D_CATALOG_EMBED_THREADS=8` can
increase throughput on a larger workstation (supported range: 1–16). Completed
batches persist, so interrupted embedding can be rerun without recomputing them.

```sh
curl 'http://localhost:8844/v1/parts?q=servo&limit=5'
curl -X POST http://localhost:8844/v1/search \
  -H 'Content-Type: application/json' --data-binary @examples/catalog-search.json
```

`GET /v1/providers`, `GET /v1/status`, `GET /v1/parts/{id}`,
`POST /v1/search` and `POST /v1/bom/check` are also available. The API provides no
arbitrary URL fetching, ingestion or purchasing endpoint.

## Bring a component into CAD

Inspect the part to choose the exact CAD asset. A product can have several revisions,
subassemblies and case files. Asset scope stays `unverified` unless source metadata
states otherwise. An STL preview and a STEP solid are different capabilities.

```sh
o3d-parts fetch-asset ASSET_UUID /absolute/path/to/downloads
o3d-parts import-part ASSET_UUID /absolute/path/to/project 3
```

`fetch-asset` uses pinned, allowlisted source repositories, bounds response size,
checks source SHA-256 where supplied, rejects Git LFS pointer text as STEP, and
records the downloaded hash. Each file has a `.LICENSE.txt` and `.catalog.json`
sidecar. Cache reads are checked too. Unknown asset licenses block download.

`import-part` requires the full CAD install. It uses the existing `Store.import_step`
revision check and records supplier/catalog provenance in
`.o3d/catalog-links/<reference-id>.json`. The reference retains its sourced
procurement classification, including `unknown`; custom features remain authored geometry. Rebuild
and check the project after import. No user project is changed by ingestion or
search. Import can fail on unsupported vendor STEP geometry; byte integrity is
not geometric or physical validation.

The real 2026-10-24 probe downloaded Adafruit Feather RP2040 STEP, checked its
MIT notice, imported it into a disposable bracket project, and rebuilt through the
Rust engine with 2,209 reference faces. This demonstrates one model, not universal
STEP support or verified mounting dimensions.

## Plan purchased versus custom parts

```sh
o3d-parts check-bom examples/robot-bom.json
curl -X POST http://localhost:8844/v1/bom/check \
  -H 'Content-Type: application/json' --data-binary @examples/robot-bom.json
```

Set `part_id` after selecting each purchased component. The BOM labels
`off_the_shelf` and `custom`, checks sourced requirements, and lists missing CAD,
mounting/interface data, failed/unknown limits, unselected parts, lifecycle issues,
unchecked stock/shipping and assembly-fit work. The prototype deliberately does
not issue a `ready_to_build: true` verdict: no physical-compatibility verification
or procurement adapter has been implemented.

## Data and provider boundaries

| Entity | Stored information |
| --- | --- |
| Providers | Name, website, storage/live-only/permission policy, license, terms and notes |
| Parts | Stable provider-scoped ID, source key, name, family/category, manufacturer/MPN where present, SKU, identifiers, aliases, lifecycle, procurement class, tags, original attributes, observation time |
| Specifications | Scalar value, numeric value where applicable, normalized unit, original field, source/derived/measured evidence and source document |
| Ports | Mechanical/electrical/bus/fluid interface, properties, optional coordinate frame, evidence and source; absent until sourced or measured |
| Assets | CAD/drawing/datasheet/image/simulation URL, format, scope, license, attribution, byte count, checksum, active status, downloaded object key |
| Documents | Immutable source-response object key, hash, URL, capture time and provider |
| Ingestion runs | Status, processed count, page checkpoint and diagnostic |
| Embeddings | Model, content hash, 384-dimensional vector and update time |

Adafruit ingestion uses its MIT CAD repository, not its differently licensed
storefront feed. step.parts metadata is ingested from its documented open API;
its MIT project license does not relicense third-party geometry. Currently only
step.parts assets traced to a pinned Adafruit source receive automatic MIT
download approval. Other model references remain discoverable with unknown
download permission until their file-level attribution is resolved. Source headers
and source-supplied geometry-envelope labels are retained; they are not verified
manufacturer dimensions. No part is automatically assigned mounting ports from
its name or guessed from a rendering. The first seed contains zero verified ports.

DigiKey is registered as live-only and MISUMI as permission-required. Neither has
an executable ingestion connector. Source storage checks enforce provider policy.
No live price/stock connector is implemented yet, so no potentially restricted
offer responses or invented prices are stored. A probe of ARK's previously
documented catalog returned HTTP 403 from this machine on 2026-10-24; no bypass
or working live adapter is claimed. Its access needs retesting before integration.

Part IDs are deterministic UUIDs from `(provider, external_id)`. Different providers
are intentionally distinct until exact manufacturer/MPN or a reviewed mapping
establishes equivalence. Commodity standards are not unique commercial SKUs.
The same SKU can identify several geometry subassemblies; do not merge by name.
Global identity resolution and reviewed equivalents remain follow-up work.

## Refresh, recovery and backup

Re-run ingestion to upsert stable IDs and preserve immutable source snapshots.
Changed text invalidates old embeddings. Removed source assets are retained for
audit but excluded from retrieval. An absent product is not automatically marked
discontinued: a partial run cannot establish that fact. The connectors identify
themselves, space requests, respect bounded `Retry-After` backoff, and stop on
unusable responses or truncated trees. A failed step.parts page can be resumed:

```sh
o3d-parts status
o3d-parts ingest step-parts --resume RUN_UUID
```

Use the same category as the original run. Checkpoints advance after a page
commits; retries replay stable keys. `--max-parts N` runs a bounded sample, which
is completed at that bound; use a fresh unbounded run for a full seed. An
interrupted Adafruit run safely restarts from its small Git tree. Automatic
scheduling and cross-run source-drift reconciliation are not yet implemented.

Back up PostgreSQL and the object store together. For Compose:

```sh
docker compose -f catalog.compose.yaml exec -T database pg_dump -U catalog -Fc catalog > catalog.dump
docker compose -f catalog.compose.yaml exec -T catalog tar -C /data -czf - objects > catalog-objects.tar.gz
```

Quiesce ingestion/download commands while taking the pair; retain the full objects
directory, not just requested CAD. On restore, use an empty database with the
extensions installed, `pg_restore` the dump, and restore objects at the same
configured root. Model vectors are in the dump; the inference model can be
downloaded again. Docker volume snapshots are another deployment-specific option.

## Checks

```sh
export O3D_CATALOG_TEST_DATABASE_URL='postgresql://USER:PASSWORD@localhost/oxygen_parts_test'
PYTHONPATH=src pytest -q tests/catalog
```

Tests refuse to clear databases without an `_test` suffix. The 17 initial cases
exercise real Postgres filtering/ranking, hybrid constraints, source identity,
idempotence, interrupted-page recovery, policy enforcement, metadata invalidation,
license/checksum handling, BOM gaps, API validation and object-path safety.
The dedicated GitHub workflow runs them against pgvector/Postgres. A generated
OpenAPI schema gives agents the complete retrieval contract.

## Next coverage work

1. Confirm and implement live supplier offers (stock, currency, price breaks,
   minimum order quantity, delivery region and lead time); enforce each feed's
   approved retention separately from stored metadata.
2. Resolve file-level licenses beyond Adafruit and add manufacturer-approved
   performance tables, datasheets, pinouts, drawings and CAD revisions.
3. Seed a reviewed robotics BOM with sourced mounting frames, hole patterns,
   shaft/spline dimensions, connector pinouts, voltage/current ranges and motor
   torque-speed/thermal curves. Validate pairings numerically and physically.
4. Add reviewed cross-supplier identity mappings, stale-offer policies and
   usage-based retrieval evaluations before scaling the supplier list.
