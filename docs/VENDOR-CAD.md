# Exact vendor SKU and local CAD inspection

Vendor CAD discovery links an existing purchased-part or hardware record to its
literal product/order URL and the CAD URLs retained in that same source record.
It does not guess URLs, join similar SKUs, treat a CAD-available flag as a file,
or turn a library reference page into a verified STEP model. Search filters run
before pagination; `scanned_records`, `eligible_records` and `total` describe the
scan, purchasable records with CAD references, and the final query matches.

The Python entry points are `VendorCAD(catalog).search(VendorCADSearch(...))` and
`VendorCAD(catalog).inspect(record_id, cad_url=..., expected_dimensions_mm=...,
tolerance_mm=0.05, cad_python=...)`. The catalog CLI exposes explicit selection
and inspection; the HTTP API exposes read-only discovery. Search accepts `q`,
`provider`, exact `sku`, `format`, `geometry_validated`, `limit` and `offset`.

Inspection downloads only an individually selected CAD URL already present on
that exact record. A record with several direct CAD links requires an explicit
URL. References and F3D remain discoverable but require a source-provided STEP
export for this inspection path. The local inspector does not bypass login,
403 responses, paid access, private uploads or vendor restrictions.


```sh
o3d-parts supply cad search --sku REV-41-1300
o3d-parts supply cad inspect RECORD_UUID --cad-url 'EXACT_SOURCED_CAD_URL' \
  --expected-mm 15,15,420 --cad-python /path/to/cad-venv/bin/python
o3d-parts supply cad search --validated
```

Read-only HTTP discovery uses `GET /v1/vendor-cad` or
`POST /v1/vendor-cad/search`; inspection is intentionally CLI-only.

## Download boundaries

The fixed HTTPS allowlist contains `revrobotics.com`, `gobilda.com`,
`servocity.com`, `cubemars.com`, and `robotis.com`, including their `www` hosts.
GitHub raw downloads are limited to `adafruit/Adafruit_CAD_Parts`,
`luxonis/depthai-hardware`, or `ROBOTIS-GIT` source-linked repositories. Every
redirect repeats the checks. DNS results must be public and the checked address
is pinned for the connection while preserving the vendor TLS hostname. No
credentials, authorization headers, cookies or environment proxies are sent.
A public vendor CDN outside this list requires a reviewed allowlist change.

Downloads are limited to 20 MB. ZIP files are limited to 100 members and 50 MB
expanded content. No archive is extracted to its original paths. Absolute paths,
traversal, symlinks and encrypted members are rejected. A STEP member must match
the exact SKU or sourced base identifier in its filename; ambiguous or unmatched
archives return member choices for review, without arbitrary selection.

## What the results establish

- `source_provided`: the source supplied a CAD/reference URL for this SKU.
- `step_signature_verified`: downloaded bytes carry a STEP signature; no solid
  or dimension claim has been established yet.
- `parsed_surface_geometry`: the parser measured valid geometry without a solid
  model. Surface-only models can be useful references but are not solid-ready.
- `geometry_validated`: the parser measured valid solid geometry and bounds, with
  a declared STEP length unit and millimeter-normalized parser output.

The optional parser is a separate process with a fixed `build123d` program, a
60-second timeout and a minimal environment without API credentials. Select its
Python using `cad_python` or `O3D_CAD_PYTHON`. Otherwise the current interpreter
is used only when it has `build123d`; a missing parser saves the STEP bytes and
reports `parser_unavailable`. Missing source units remain `unit_unverified`.
The static parser reads the STEP as data and never executes source file text.

`expected_dimensions_mm` compares the three sorted bounding-box dimensions with
a caller-declared numerical tolerance. This is an axis-order-independent envelope
check, not a manufacturing tolerance, interface check or physical-fit proof.
`physical_accuracy`, assembly scope and current-SKU revision match remain
unverified, including after a successful parse. Pack quantity, optional horns,
flanges, internal geometry and vendor revision changes require separate review.

## Local storage and rights

Download and selected STEP hashes are recorded separately for ZIPs. File bytes
are stored as read-only content-addressed objects. Reports are also stored in
`catalog.objects`; the atomic local index under `catalog.objects.root/vendor-cad`
uses the hashed record ID and CAD URL and points to the report object. A later
inspection updates the index while previous report objects remain addressable.
Source records, source facts, database migrations and existing asset
`download_allowed` permissions are unchanged.

The inspector preserves an explicitly supplied CAD license, otherwise `unknown`.
Local public access does not establish redistribution or design-use rights.
Reports retain `redistribution: unknown` and `design_use: review_required`;
source-linked or geometry-validated results do not promise import or distribution
permission, live stock, a purchasable current revision, or physical accuracy.

Run the offline contract and boundary tests with:

```sh
PYTHONPATH=src python -m pytest -q tests/catalog/test_vendor_cad.py
```

These tests use mocked sources and a fake interpreter for the subprocess
contract. They do not establish the geometry of any real vendor SKU.
