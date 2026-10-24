# Find a supplier for purchased and custom parts

The catalog has three complementary datasets:

- CAD/component references in the original parts catalog.
- Sourced manufacturer capabilities, materials, finishes and purchased-part records.
- An unofficial JLC component snapshot for broad component discovery while official
  JLCAPI access is pending. Its original fetch dates remain visible.

These are different kinds of evidence. A material listing is not an engineering
approval, and a snapshot is not current inventory. Every returned manufacturing
record exposes its supplier, source, capture time, constraints and review state.

## Set up and load real public feeds

Use the [catalog setup](CATALOG.md), then run:

```sh
o3d-parts init
o3d-parts supply ingest sendcutsend
o3d-parts supply ingest laserhub
o3d-parts supply ingest ark
o3d-parts supply ingest cdfer
o3d-parts supply status
```

`init` preserves the original schema and applies additive manufacturing migrations.
Docker's catalog startup runs it too. Provider adapters use fixed HTTPS sources,
bounded responses and request spacing. They do not create accounts, upload user
designs or place orders.

| Feed | Verified source | Imported meaning |
| --- | --- | --- |
| SendCutSend | Official agent catalog and engineering-spec JSON | 177 materials, 153 hardware entries, 25 finishes; engineering facts joined only by exact source SKU |
| Laserhub | Official public material JSON | 232 distinct material/process records from 298 source rows, retaining duplicate-row evidence and 3,521 exact variation tuples |
| ARK Electronics | Official public agent catalog | 169 exact parts/variants from 188 source rows; variable parents retained as context rather than invented purchasable variants |
| CDFER | Public community JLC CSV | 1,577 observations for 1,551 distinct Basic/Preferred C-codes, including conflicting repeated rows |

The initial dated provider-research import adds further directly cited records.
Counts describe source observations and capabilities, not unique orderable SKUs.
Some providers have only access-policy evidence or zero admitted product records.
Run `status` for the current database counts rather than assuming a fixed seed.

The feeds and research metadata preserve source qualifiers. No Laserhub numerical
unit is inferred merely because a field is called `thickness`. A source's stated
process/material tuple is retained; adapters never form a Cartesian product of
unrelated lists. Conflicting dimensions, torque definitions, configurations and
source timestamps stay separate. Unknown CAD permissions do not become MIT
because a scraper's code is MIT.

## Search the manufacturing and provider records

```sh
o3d-parts supply search --provider jlccnc
o3d-parts supply search aluminum --provider sendcutsend
o3d-parts supply search --provider jlc3dp
o3d-parts supply search --provider ark --record-type purchased_part
o3d-parts supply get RECORD_UUID
```

Structured JSON filters support exact provider/process/material/type facets,
source facts and numeric constraints with explicit units. Use a source's actual
process label. Numeric values without source units or source-field evidence do
not satisfy a requested physical bound.

The original `o3d-parts search` continues to search the original CAD reference
catalog. Use `supply search` for these manufacturing records and the component
snapshot commands for the full electronics mirror; these namespaces do not claim
global identity resolution or hide source overlap.

## Compare a custom-part job

For exact purchased-component CAD, use the dedicated [vendor CAD workflow](VENDOR-CAD.md):

```sh
o3d-parts supply cad search --provider gobilda
o3d-parts supply cad search --sku 1611-0514-0008
o3d-parts supply cad inspect RECORD_UUID --cad-python /path/to/full-cad/python
```

It exposes actual source-linked CAD and purchase URLs and records local geometry
inspection separately from revision, variant, use rights and physical accuracy.
The large electronics snapshot is metadata coverage, not a CAD library.

```sh
o3d-parts supply match examples/manufacturing-job.json
```

A job can specify provider, process, material, formats, quantity, x/y/z bounds,
tolerance and exact numeric filters. Process/material selection happens before
pagination and considers capability and material records. Explicit mismatches
are excluded; missing source values remain review requirements. Other requested
limits return `candidate`, `needs_review` or `rejected` with reasons. Even a
candidate requires the supplier's engineering review and a current quote.

The matcher recognizes only documented canonical bounds (`max_x/y/z`,
`min_quantity`, `max_quantity`, `minimum_tolerance`) with matching units, plus
explicit `supported_formats`. Supplier-specific constraints remain queryable by
their source keys. It does not rotate parts, infer a manufacturing tolerance from
a machine specification, perform DFM, or declare the job ready to order.

Each relevant record includes the provider's real quote/upload handoff where
sourced. No ordering or private-file-upload endpoint is exposed by this service.

## Read a live ARK offer

```sh
o3d-parts supply ark-live ARK-FLOW
curl http://localhost:8844/v1/live/ark/ARK-FLOW
```

This performs an actual, ephemeral provider request with an observation timestamp.
HTTP responses use `Cache-Control: no-store`; a failed lookup does not substitute
a cached price or stock value. Persisted purchased metadata excludes structured
price, inventory, purchasability and lead-time fields and monetary observations.
Original research captures remain separate historical evidence, not live offers.

## Official JLCAPI

See [JLCAPI configuration and verification](JLCAPI.md). Credentials belong in
local environment configuration, outside repositories and research captures.

```sh
o3d-parts supply jlc-status
o3d-parts supply jlc-component C25804
o3d-parts supply jlc-library --page 1 --page-size 1
o3d-parts supply jlc-feed
```

The three read operations require approved Components API access. The initial
real signed probes all returned JSON 403, "API insufficient permissions, access
denied"; the app owner confirmed review was pending. Those failures are recorded
as failures, not replaced by community data under an official API label. The
client stops on permission denial; it does not poll for approval or create orders.

For alternate sources, see [community CSV](JLC-COMMUNITY.md) and
[full component snapshot](COMPONENT-SNAPSHOT.md). They require no official API
approval, but have separate provenance, update cadence and unresolved dataset
reuse rights. Public availability does not license third-party CAD downloads.

## HTTP contract

Interactive OpenAPI is at `http://localhost:8844/docs`.

| Operation | Route |
| --- | --- |
| Manufacturing counts, sources and suppliers | `GET /v1/manufacturing/status` |
| Keyword/facet discovery | `GET /v1/manufacturing/records` |
| Full structured search | `POST /v1/manufacturing/search` |
| Source-backed record detail | `GET /v1/manufacturing/records/{id}` |
| Conservative job comparison | `POST /v1/manufacturing/match` |
| JLC credential configuration status | `GET /v1/integrations/jlcpcb/status` |
| Ephemeral ARK offer | `GET /v1/live/ark/{sku}` |

## Import inspected provider evidence

```sh
o3d-parts supply import-evidence /path/to/research --dry-run --report plan.json
o3d-parts supply import-evidence /path/to/research --report import-result.json
```

The offline normalizer reads leaf assignments, verifies capture paths, timestamps,
hashes and source statuses, preserves supplier IDs or explicitly labels locally
derived evidence IDs, and separates repeated variants. It never executes source
content. Explicit ingestion restrictions, missing captures, blocked pages,
index-only leads and unsupported facts go to quarantine. Successful source
retrieval does not itself establish a license or prove every extracted claim.

Purchased records are imported from metadata-only derived snapshots with the
original source hash, timestamp and redaction audit. Derived snapshots are
labelled differently from original HTTP captures. Ordinary capability/material
observations remain source-qualified; unknown rights remain unknown. Full raw
research is an operator-owned evidence archive and is not bundled with the public
application. Optional alias mappings do not silently equate different products.

## Persistence and limits

Back up PostgreSQL and the configured catalog data directory together, including
community snapshot files and source objects. Source hashes make archived evidence
inspectable; immutable captures are not a refresh or restore test. Research
quarantine counts are audit history, not a count of currently failing live feeds.

Current limits include unknown live stock outside an explicit live adapter,
unresolved CAD rights, incomplete mating interfaces and performance curves,
manual supplier review, no authenticated ordering and no manufacturing guarantee.
The large component mirror uses indexed exact/full-text/JSON retrieval; it does
not claim that every component has CAD or a local semantic embedding.
