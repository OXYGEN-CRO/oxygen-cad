# Supplied-snapshot procurement

Procurement is an offline, bounded engineering aid. It validates a typed BOM, assesses explicitly supplied offer observations, reports disclosed subtotals per currency, and compares sourced alternative properties against declared constraints. It performs no network requests, catalog ingestion, purchases, reservations or currency conversion.

`examples/procurement-bom.json` is a **synthetic offline fixture**, including synthetic offer prices, inventory, dates and source hashes. Its REV product/CAD URL shape follows the existing `catalog.vendor_cad` source-chain contract; this is not a live quote or a verified product assessment. Its explicit historical `as_of` demonstrates a reproducible evaluation clock. Omit `as_of` for current UTC evaluation. Both clocks report `evidence_mode: supplied_snapshot`.

## Python integration

```python
from o3d.procurement import inspect_bom, evaluate_procurement
from o3d.procurement_models import ProcurementBOM, ProcurementEvaluationRequest

inspection = inspect_bom({"lines": []})
report = evaluate_procurement({"bom": {"lines": []}, "offers": []})
```

Both functions accept JSON objects or the corresponding validated Pydantic model and return JSON-compatible dictionaries. Validation failures raise `pydantic.ValidationError`; catalog offer values incorrectly embedded in BOM metadata raise `ValueError`. API/CLI transport is provided by the application integration.

`inspect_bom` accepts a BOM object, not a project or an evaluation envelope. It returns the normalized `bom`, `bom_sha256`, classification `counts`, and each line's identity/quantity state. `evaluate_procurement` accepts:

| Field               | Contract                                                                                                                                                                                                                                                                                      |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bom.lines[]`       | Unique `id`, `name`, explicit `classification` (`purchased`, `custom`, `unknown`), optional positive integer `quantity`, optional `identity`, `requirements`, `hard_constraints_complete`, optional `offer_id`                                                                                |
| `identity`          | Exact `provider`, `sku`, `asset_id` and lowercase `source_sha256`; missing fields remain unknown. Asset ID is caller-supplied exact catalog/import identity, including a literal sourced CAD URL when that is the selected source asset. No matching by display name or guessed CAD identity. |
| `offers[]`          | Unique `id`, `identity`, `provenance`, `observed_at`, `expires_at`, `currency`, `regions`, `minimum_order_quantity`, `quantity_breaks`, `stock_state`, `stock_quantity`, `lead_time_state`, optional `lead_time_days`, `support`                                                              |
| `provenance`        | Explicit HTTPS `source_url` without credentials plus lowercase `source_sha256`; these are declared evidence pointers, not fetched or independently verified here                                                                                                                              |
| `quantity_breaks[]` | Unique positive `minimum_quantity`, nonnegative decimal-string `unit_price`                                                                                                                                                                                                                   |
| `region`            | Explicit destination identifier matched literally to the offer's declared regions; no implicit global or neighboring-region coverage                                                                                                                                                          |
| `as_of`             | Optional ISO timestamp with timezone; omission uses current UTC, supplied value is labeled `explicit_snapshot`                                                                                                                                                                                |
| `alternatives[]`    | Unique `id`, `line_id`, exact candidate `identity` and sourced `properties[]`                                                                                                                                                                                                                 |

Every timestamp requires a timezone; expiry must follow observation. NaN, infinity, negative prices, booleans as quantities, fractional quantities, duplicate IDs/breaks, contradictory stock/lead-time declarations and excessive bounds are rejected. Money requires strings with at most six decimal places, a maximum magnitude of 1e12 and bounded exponent. Quantities are bounded to 1e9 per line; list sizes are bounded. Currency uses explicit three-uppercase-letter codes, without assuming exchange rates or tax rules.

## Offer assessment and subtotals

Each line retains its original classification. Catalog `purchased_part` record types or vendor names do not convert a project line classified `unknown` into a purchased line. Custom lines remain unquoted. Offer values remain in the evaluation envelope, separate from component identities and metadata. The existing `catalog.offer_policy.contains_offer_values` guard rejects observed offer data accidentally placed in BOM metadata.

The evaluator reports `fresh_available`, `insufficient`, `expired`, `unknown` or `unsupported` observations:

- Provider, SKU, asset or source-hash mismatch is unsupported. Incomplete identity is unknown.
- Expired observations never price a line. Missing observation/expiry/provenance or future observations are unknown.
- Unsupported regions are unsupported; unknown destinations/coverage remain unknown.
- Demand below MOQ, unavailable/backordered stock or stock below demand is insufficient. No automatic overbuy is performed.
- Unknown stock quantity, currency or applicable quantity break remains unknown. Known lead time cannot establish stock availability; its state is shown separately.

Repeated lines share combined demand for MOQ, stock and quantity-break checks only when they have the same exact identity and select the same supplied offer observation. Each unique observation ID is a separate offer/order scope, with its own currency and region coverage; prices, MOQ and stock never aggregate across different selected offers. A missing quantity leaves only that offer scope unknown. Automatic selection groups unassigned lines with the same exact identity, evaluates each candidate together with any demand explicitly choosing that same offer, then recomputes final pricing after assignment. This prevents automatic and explicit lines from claiming the same stock twice. An available offer's applicable break is the largest declared threshold at or below scoped demand. Extended prices use the original line quantity. Among fresh eligible observations in the same currency, the evaluator chooses the lowest price with deterministic ID tie-breaking. Multiple eligible currencies require explicit `line.offer_id`; currency amounts are never implicitly compared. A selected absent or mismatched offer cannot silently fall back to another observation.

`totals` contains currency, exact decimal-string amount and scope. `partial_known_lines` excludes any unknown/custom/ineligible line and lists all exclusions in `excluded_line_ids`. `complete_supplied_bom` means all declared lines have eligible supplied observations; it still excludes shipping/tax and makes no landed-cost, reservation, physical-fit or live-supply promise. Empty BOMs are incomplete. Mixed currencies remain separate subtotal entries, with no overall converted total. Offer observations from multiple times and source documents are not independently reconciled; the caller is responsible for their authoritative scope. Suppliers can change inventory before ordering.

## Constrained alternatives

A hard requirement declares `key`, `kind` (`interface` or `performance`), `operator` (`eq`, `gte`, `lte`), string `value`, optional exact `unit`, and `hard: true`. Numeric comparisons use bounded decimal values with exactly matching units. There is no unit conversion. Alternative properties declare `key`, `kind`, string `value`, optional `unit`, and their own `provenance`.

`constraints_satisfied` requires a purchased line, complete original and alternative identities, explicitly declared `hard_constraints_complete: true`, at least one hard interface constraint and one hard performance constraint, and sourced evidence satisfying every hard constraint. Missing evidence or incomplete hard-constraint declarations yield `unknown`. A sourced incompatible hard constraint yields `rejected`. Soft constraints do not establish substitution. Matching names, vendor CAD discovery and a matching bounding box are not substitution evidence. This state establishes only the declared constraint comparison; physical fit, certification, qualification and alternative supply remain unverified. Alternatives never replace the BOM identity or contribute prices automatically.

## Assessment invalidation and storage policy

`bom_sha256` hashes the complete normalized BOM, including identity/source hashes, quantities, classification and constraints. `input_sha256` additionally covers all normalized observations, candidate evidence, region and explicit clock. `assessment_sha256` covers the complete report including the effective evaluation clock. Editing source hashes, offers, requirements or candidate evidence changes the relevant signature. Reports must be reevaluated after edits and after their supplied observations expire; the consumer must not treat a retained report as perpetually current.

No evaluator writes observations or reports anywhere. Provider-specific terms still determine whether the caller may retain, display, cache or redistribute supplied prices, inventory and lead times. The existing catalog ingestion and `offer_policy` metadata boundary remains in force: do not put these transient observations into manufacturing records, component snapshots, catalog metadata or project catalog links. A caller that exports a report must apply the source provider's storage/reuse policy itself.

## Controlled React panel

`frontend/src/ProcurementPanel.tsx` exports `ProcurementPanel`, `ProcurementPanelProps` and `ProcurementReport`.

```tsx
<ProcurementPanel
  value={procurementJson}
  onChange={setProcurementJson}
  result={report}
  onEvaluate={async (data: unknown) => {
    /* call the evaluator transport */
  }}
  busy={evaluating}
  error={evaluationError}
/>
```

`value` is the complete transient evaluation-envelope JSON editor value; `onChange` supplies edits. `result` is optional/null and has the report contract returned by `evaluate_procurement`; decimal strings are rendered without JavaScript numeric conversion. `onEvaluate(data)` receives parsed JSON and returns void or a promise; backend validation remains authoritative. Optional `busy` disables evaluation and hides prior results during requests. The panel shows line identities/classifications, quantities, observation/expiry and lead-time states, partial subtotals, exclusions, alternative states and limitations, using the existing theme classes. Local edits hide the prior result until reevaluation.

The owning application must clear/replace `result` when input/project selection changes, set `busy` for pending requests, reject responses from superseded input, and avoid saving transient offers into project/catalog data. Supplying a result from another input cannot establish its freshness merely because a panel was mounted.

## Validation scope

Deterministic unit tests use the existing vendor catalog source shape and synthetic offers. They exercise pricing/MOQ/stock/expiry/region, quantity aggregation, unknown data, mixed currencies, incomplete totals, numeric/timestamp rejection, source/signature changes and constrained alternatives. They establish offline evaluator behavior, not provider API connectivity, a live quote, physical compatibility, manufacturing quality or macOS behavior.
