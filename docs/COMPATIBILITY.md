# Sourced mounting interfaces and editable adapters

This feature evaluates one bounded mechanical scope: registration of two complete,
labeled circular through-hole patterns in a common assembly frame for a floating
straight fastener. `compatible` refers only to this scope. `overall_status` is
always `unknown`: component collision envelopes, heads and washers, access,
retention, engagement, strength, process capability, thermal behavior, electrical
pinouts and voltage, and bus/protocol behavior have not been evaluated. It does
not claim manufactured fit.

## Input and identity

`compatibility_models.ComponentInterface` binds explicit geometry to an exact
`provider`, `sku`, `asset_sha256`, and `interface_id`. These reuse the catalog's
provider/SKU/content-hash convention; they do not invent a competing global part
identifier. Dimensions are never inferred from names, descriptions, catalog port
properties, STEP existence, or bus/voltage marketing strings. No supplier data is
bundled. `examples/component-interfaces.json` is conspicuously synthetic; its
repeated-character hashes are illustrative, not claims of real captured assets.

Each interface contains:

- `units`: `mm` or `in` (exactly 25.4 mm per inch).
- `frame`: named `assembly_frame`, origin in the interface's units, and
  orthonormal dimensionless `x_axis`/`y_axis`. Their cross product defines the
  right-handed normal. Points transform as `origin + x*x_axis + y*y_axis`;
  the entire resulting point then converts to mm. There is no inferred alignment.
- `holes`: unique correspondence labels, local XY centers, circular diameters.
  Only `planar_through_holes` is supported, with at most 32 holes per interface.
  Coordinate bounds prevent overflow during transformation (±1,000,000 input units).
- `tolerance`: worst-case **radial** positional uncertainty and symmetric diameter
  uncertainty, both in the interface units. Position uncertainty must include any
  frame-registration uncertainty; these are not statistical or diametral GD&T
  position values. Zero means an explicit zero bound, not missing data.
- `evidence`: `kind` (`source` or `measured`), a document/measurement `reference`,
  exact `source_field`, and SHA-256 of supporting document/measurement bytes.
  This evidence must support the complete geometry, frame and tolerance assertion.

Asset and evidence hashes are assertions supplied by the caller. This module
does not fetch or authenticate their bytes. Integration must check them against
the selected catalog snapshot/asset when available. `source_signature` hashes the
entire normalized interface, including dimensions, tolerances, identity and evidence;
changing a hash, frame, SKU or dimension changes this signature. Retain the returned
source snapshots/signatures with every saved result and invalidate it when inputs
change. A signature detects a changed assertion; it does not certify its truth.

## Python/API contract

```python
from o3d.compatibility import (
    validate, compare, propose_adapter, source_signature, validate_adapter,
)

interface = validate(interface_json)  # ComponentInterface; raises ValueError
result = compare({"a": a, "b": b, "fastener_diameter_mm": 4.0})
payload = result.model_dump(mode="json")
proposal = propose_adapter(adapter_request)
editable_project = proposal.project
checked = validate_adapter(proposal)
```

Functions accept dictionaries or their corresponding Pydantic model and return
Pydantic models. Comparison is pure. Adapter generation and validation use the current
native engine and temporary STEP exports without writing to an active project.
They revalidate supplied model instances, including nested mutated values.
Boolean/string numeric dimensions, NaN, infinity, unsupported units, scale/shear frames, duplicate labels and
negative tolerances are validation errors. Missing geometry, frame, provenance,
tolerances or fastener diameter produces `unknown` comparison. Differently named,
nonparallel or noncoplanar mounting frames produce `unknown`; no automatic
registration is attempted. Equal-count patterns with unmatched labels also produce
`unknown` because correspondence is not supplied; differing complete pattern counts
are incompatible for this full-pattern scope.

`fastener_diameter_mm` is the explicit maximum effective diameter of the straight
fastener envelope being checked (include its own tolerance), not an unsourced
nominal thread designation. For each matched label, the measured worst-case center
mismatch is the transformed center distance plus both radial position bounds.
The limit is half the sum of both minimum hole diameters minus the fastener
diameter. Both minimum hole diameters must individually admit that fastener.
All labels must pass. A 1e-8 mm numeric epsilon resolves floating-point noise only;
it is not a manufacturing tolerance. This does not address a screw engaging a
threaded hole, fixed studs, force fits, or correlated GD&T datum systems.

`CompatibilityResult` keys: `status`, `overall_status`, `scope`, `sources`,
`reasons` (including `measured_mm` and `limit_mm` per evaluated hole), and `limits`.

## Deterministic adapter proposal

`AdapterRequest` has `a`, `b`, `thickness_mm`, positive `edge_clearance_mm`, positive
`web_clearance_mm`, and nonnegative `machining_clearance_mm`. Optional `width_mm`
and `height_mm` specify the rectangular plate size centered on the nominal pattern
envelope. Without these, the envelope plus edge bounds determines dimensions.
The generator requires complete sourced/measured input and both planes coplanar
in a common assembly XY frame. In-plane rotation/translation and opposite normals
work. Tilted or separated planes are rejected explicitly. Translation of the
common plane along assembly Z is supported.

Each adapter through-hole is placed at the transformed source center with the
source nominal diameter plus `machining_clearance_mm` (added diameter, not radial
clearance). Edge and inter-hole web checks conservatively include source positional
uncertainty plus half the diameter uncertainty. This allowance is a requested
geometric proposal; no process accuracy, bolt selection or final fit is promised.
Overlapping/coincident holes and insufficient edge/web clearances are rejected,
including nominally shared holes rather than silently deduplicating them. Plate
dimensions are bounded to 2000 mm and thickness to 100 mm.

`AdapterProposal` keys: `project`, `request`, `sources`, `acceptance`, `limits`.
The project contains a canonical, versioned `native_document` with editable
sketch/extrude timeline features, native parameters and sketch dimensions for the
plate and every hole diameter. The legacy `features` list is empty once native is
canonical. No opaque STEP or hidden script stands in for this editable source.
Edit dimensions using current native `parameters.change` transactions; changing
only a host parameter mirror does not edit a canonical design.

Acceptance independently imports the actual native STEP export through OCCT and
checks exact rectangular topology, bounds, volume, every circular through-hole
center/diameter/full-depth extent, and absence of unexpected solids/surfaces/meshes.
It also recomputes tolerance-bounded edge and web clearances from the source request.
The acceptance method is `independent_native_step_geometry`; `measured_volume_mm3`
and `native_geometry_signature` supplement the expected dimensions/volume.
`validate_adapter` checks preserved source signatures and placement, and remeasures
actual geometry; cached acceptance never substitutes for this check. External
links, assembly entities and unsupported native feature types are refused before
validation. An edited design can remain useful, but changing requested dimensions
requires regeneration to pass the original request-specific acceptance check.

The default creates a fresh `Project`. Optional `project=` must use SolidCraft and
have no native document, modeled features or modeling context, and identity placement.
Even an apparently empty existing canonical document is refused conservatively.
Other metadata, engineering brief/joint selections, studies, existing parameters
and locks are copied; generated parameter ID collisions fail. No active project,
Store, revision or asset is mutated. Temporary compilation files are removed.
API/CLI integration should export the complete proposal or offer an
explicit apply through `Store.apply(Command(expected_revision=...))` to an empty
project. Never replace a populated design as a side effect of generation.

## React contract

`frontend/src/CompatibilityPanel.tsx` exports a controlled `CompatibilityPanel`:

```tsx
<CompatibilityPanel
  inputs={inputs} onInputsChange={setInputs}
  result={result} proposal={proposal} busy={busy} error={error}
  onCompare={compareViaApi} onGenerate={generateViaApi}
/>
```

`CompatibilityInputs`: `a` and `b` JSON strings; numeric-or-empty-string
`fastenerDiameterMm`, `thicknessMm`, `edgeClearanceMm`, `webClearanceMm`, and
`machiningClearanceMm`. Input callbacks receive the full updated inputs. Comparison
and generation callbacks receive backend-compatible snake-case request objects.
The parent owns calls, saved source snapshots, clearing stale results when inputs
change, expected revisions, downloads and errors. The panel shows provenance,
source hashes, numerical reasons, explicit scope limits and editable source using
the existing theme controls. It never applies or sends a proposal itself.

## Verification

`tests/test_compatibility.py` covers missing-data/compatible/incompatible outcomes,
finite values, units and frame transformations, exact tolerance boundaries, source
signature invalidation, impossible adapters, preserved locks and numeric source
tampering. The integration case runs the actual pinned Rust engine and imports its
STEP output independently through OCCT/build123d to measure plate bounds, volume,
each cylinder axis and diameter, then performs a canonical native parameter edit
and measures the rebuilt STEP. Tests also exercise current Store revision/lock
guards and preserve existing multibody native documents, studies and joint metadata.
This verifies exported CAD geometry, not physical
manufacturing or component assembly fit. Run with the configured `O3D_ENGINE_BINARY`.
