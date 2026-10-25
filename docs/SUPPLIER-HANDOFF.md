# Offline supplier preflight and manual quote request

The supported supplier workflow is **one closed exact solid**. Handoff creation
refuses multiple bodies, open surfaces, mesh bodies and empty scenes with an
instruction to export the complete canonical CAD release for assembly review or
create a separate single-body project. It never fuses or flattens independent
bodies, substitutes a per-body STEP for a complete assembly, or assigns a solid
volume to a mesh/surface.

The frozen handoff contains every canonical `release-manifest.json` file,
including `native/project.oxygen.zip`, native design, scene, exact per-body geometry,
immutable source assets and saved study evidence. Reopen from the nested portable
project archive to retain the native document, stable IDs, history, domains,
named studies and all declared dependencies. Loose stale files in a reused export
directory are never copied: the handoff verifies the actual canonical ZIP bytes
against its release manifest. `geometry_scope.selectable_artifacts` explicitly
lists the combined single-solid STEP/STL and any equivalent per-body STEP; request
selection is restricted to those paths. Reference assets and drawing derivatives
cannot be selected as if they were the manufactured solid.

This companion to `o3d.handoff` checks a **selected** provider, process, material,
quantity and actual exported CAD files against one supplied capability/material
record. It reuses `ManufacturingRecord` and its source constraints. It does not
fetch supplier information, upload files, place orders, perform checkout or call
a quote API. A passing CAD export, solver result or rule comparison never establishes
manufacturability or supplier acceptance.

## Python integration

All paths below are relative to the selected Store's `.o3d` cache. Root API/CLI
handlers should accept JSON objects and use these same functions:

```python
from o3d.supplier_handoff import (
    capture_source, preflight, create_request, status, record_response,
)

attached = capture_source(store, supplied_evidence_text)
# Set profile.source.capture and profile.source.sha256 from attached, and supply
# its source URL, timezone-aware captured_at/expires_at and kind=supplied_capture.
evaluation = preflight(store, profile, request)
package = create_request(store, profile, request, expected_revision=store.load().revision)
current = status(store, package['manifest'], profile, request)
# Supply response correspondence only when actually available:
current = record_response(store, package['manifest'], response, expected_revision=store.load().revision)
```

Exact signatures:

- `capture_source(store: Store, content: str | bytes) -> dict`
- `preflight(store, profile: SupplierProfile | dict, request: SupplierRequest | dict, *, now: datetime | None = None) -> dict`
- `create_request(store, profile, request, *, now=None, expected_revision: int | None = None) -> dict`
- `status(store, manifest: str, profile=None, request=None, *, now=None) -> dict`
- `record_response(store, manifest: str, response: SupplierResponse | dict, *, now=None, expected_revision: int | None = None) -> dict`

`handoff.create(..., expected_revision=None)`, `create_request` and `record_response`
check optional `expected_revision` inside the Store lock against their selected
snapshot, raising `o3d.store.Conflict` on mismatch.
They recheck the selected revision/document before completing. A failed concurrent
change removes the newly created package/response event; no current quote is returned.
Current Store read helpers take their own locks. The supplier workflow uses short
snapshot/publication sections and never holds a Store lock while calling canonical
export, status, state or other locking read helpers. `authorized(store, 'export')`
revalidates prepare/package identities at snapshot and publication; attachment and
response writes use `authorized(store, 'edit')`. CLI filesystem owners remain
trusted. Viewer/reader/editor-agent roles follow the existing identity policy;
revocation during generation prevents publication and removes the new package.
Existing callers may omit the revision guard. API/CLI callers should propagate their expected
revision into the helper instead of relying on an earlier external revision check.

Models live in `supplier_models.py`. An optional `now` is timezone-aware and exists
for reproducible local evaluation/tests. API callers should use server time.
`handoff.create(store, section_z, overhang_degrees=45, *, expected_revision=None)`
returns `geometry_scope`, including `step`, `stl` and `selectable_artifacts` paths.
`capture_source` accepts 1 byte–2 MiB and stores immutable hash-addressed bytes;
its receipt explicitly says `user_supplied_bytes`, `vendor_verified: false`.
No URL is opened. A retained capture verifies bytes and supplied provenance;
it does not independently verify the extraction, URL ownership, factual truth,
supplier certification or an HTTP retrieval.

`profile` has `schema_version: 1`, `provider`, `record` (the existing manufacturing
record) and `source`. The source has `url`, `capture` under
`supplier-sources/<file>`, a SHA-256 of exact retained bytes, timezone-aware
`captured_at`, `expires_at`, and `kind` (`supplied_capture` or `synthetic_fixture`).
The record's `source_url` must equal the source URL. Copy catalog provenance into
a retained user-supplied attachment before evaluation; the adapter does not read
arbitrary catalog object paths or pull catalog data over a network.

`request` requires `request_id`, `provider`, `process`, `material`, positive integer
`quantity`, `handoff_manifest` (`handoffs/<generated-id>/manifest.json`) and a
nonempty `artifacts` list. Artifact names are paths *inside that verified handoff*;
for example `native/part.step`. They must be listed with matching hashes and selected explicitly from the
single-solid canonical report scope. Combined STEP/STL and complete equivalent
per-body STEP are supported; other formats and multi-body scenes require a
separate reviewed workflow. Optional `tolerance` and `minimum_wall`
are `{value: positive finite number, unit: exact source unit}`. These optional
inputs are supplied claims, not independently measured wall/tolerance evidence.

## Supported source semantics

These keys are supported only when explicitly supplied in the record. There are
**no default process limits**. Every numeric source constraint needs a positive
finite value, exact unit and nonempty `source_field`.

| Record constraint | Compared input | Required comparison |
| --- | --- | --- |
| `max_x`, `max_y`, `max_z` | Hash-verified handoff report's built geometry axis dimensions in `mm` | Dimension ≤ source value |
| `min_quantity`, `max_quantity` | Explicit request quantity in `count` | Quantity ≥ minimum and ≤ maximum |
| `minimum_tolerance` | Supplied absolute ± tolerance magnitude | Requested magnitude ≥ source minimum |
| `min_wall` | Supplied minimum wall magnitude | Requested magnitude ≥ source minimum |

The tolerance source field must describe an absolute ± magnitude, never a total
band or inferred ISO class. Normalizing that semantic remains the responsibility
of the supplied source record, as in the existing catalog matcher.

Core checks require all three dimension bounds and both quantity bounds.
Missing bounds remain `unknown`; they do not mean unlimited. Other source keys,
missing optional measurements required by a rule, missing material/process,
missing formats, alias-only records and expired/future-dated evidence remain
`unknown`. An explicit label, format, numeric or exact-unit mismatch is `fail`.
Supported formats come only from `record.facts.supported_formats` and compare
case-insensitively to actual selected artifact extensions. `stp` is not silently
converted to `step`; no synonyms, axis rotation, process/material join or unit
conversions are inferred.

Dimensions come from the existing immutable handoff's measured engineering report,
matched by revision and geometry key. Mutable build-cache dimensions and caller
dimension claims cannot override that report.

The result's `preflight_state` is `pass`, `fail` or `unknown`, describing only
these sourced comparisons. `state: request_ready` means the listed comparisons
passed and the current package can be prepared for manual submission. It is not a
manufacturing approval. `manual_handoff` retains unresolved/failed findings in a
package for human review. The report always keeps `manufacturability: unverified`,
`supplier_acceptance: unknown`, and `submission: not_sent`.

## Packages, stale evidence and supplied quote states

`create_request` writes `.o3d/supplier-requests/request-<id>/` containing an
inspectable `manifest.json`, `request.json`, `profile.json`, `preflight.json`,
retained source bytes, and the existing immutable handoff's listed files under
`handoff/`. `request.zip` contains these files and the manifest. A separate receipt
binds the manifest and ZIP hashes. Generation uses a private pending directory;
the complete request directory is published by atomic rename under a Store lock
after revalidating the current export role and selected document signature. `artifact` and `manifest` result paths work
with the existing Store artifact download route. The archive is constructed
from verified relative paths and is never extracted by this module.

The binding includes the full project revision/document signature, geometry key,
request identity and all request fields, full profile hash, retained source hash,
existing handoff manifest hash and selected actual artifact hashes. A changed
revision, geometry, quantity, provider, material, profile or selected artifact
makes previous evidence and quotes stale. `status` checks packaged bytes *and*
current original handoff/source files. Pass current edited profile/request when
checking an edited selection; without them it checks the frozen selection.
A source's expiry removes readiness even when the document is unchanged.

`record_response` accepts explicit supplied correspondence only. `response`
requires `state` (`awaiting_response`, `quoted`, `rejected`, `unknown`), matching
`request_id`, copied `binding_sha256`, a separate current `source` capture and a
`note`. A `quoted` response additionally requires decimal-string `amount` and
three-letter `currency`. Price and state remain in this revision-bound quote
workflow; they never enter persisted catalog capability metadata. No price,
submission, supplier status or acceptance is synthesized. A rejected response
retains its provenance. Expired evidence gives `expired`; changed selection gives
`stale`. Quote correspondence is append-only, hash-addressed under
`supplier-events/`, leaving request ZIP bytes frozen. Supplied `quoted` is still
not supplier acceptance or validated manufacturability.

Paths reject absolute paths, traversal, Windows separators/drive syntax and
symlinks in any supplied path component. Reads stay within handoffs, supplier
sources or supplier packages in Store.cache. At most 512 handoff files, 64 MiB
per input file and 256 MiB total handoff payload are accepted. This module does
not unpack any supplier archive. These are local application bounds, not process
capabilities. Corrupted/missing evidence is refused, never silently accepted.

## Frontend contract

`SupplierPanel.tsx` exports a controlled `SupplierPanel`, `SupplierPanelProps`,
`SupplierResult` and `SupplierCheck`. Props:

```ts
{
  profileJson: string;
  requestJson: string;
  onProfileChange: (value: string) => void;
  onRequestChange: (value: string) => void;
  onEvaluate: () => void;
  onPackage: () => void;
  result?: SupplierResult | null;
  busy?: boolean;
  error?: string | null;
  artifactUrl?: (relative: string) => string;
}
```

The host owns evidence attachment, API calls and error/loading state. Results
include supplied `inputs` snapshots so *every* profile/request edit immediately
marks visible old evidence stale. Hosts should clear prior errors/results when
changing project revision and refresh package status after a project edit.
Downloads always identify the frozen package, even when the design is stale.

## Synthetic example

`examples/supplier-request.json` is explicitly synthetic. Its numbers are fixture
values, not vendor standards. To inspect it locally, attach the exact UTF-8 bytes
`Synthetic fixture only.\n` through `capture_source`, replace the example's capture
path/hash and generated handoff path, and use a current evidence date window.
Keep `kind: synthetic_fixture`: this deliberately prevents a ready verdict.
Use an actual supplied retained capability record to investigate a real provider.
