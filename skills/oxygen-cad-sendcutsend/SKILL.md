---
name: oxygen-cad-sendcutsend
description: Prepare a SendCutSend-specific review of an Oxygen STEP or DXF upload against current material, thickness, SKU and service requirements.
---

# Sendcutsend

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Read [supplier evidence](references/supplier-evidence.md), then fetch the linked official guide/material/service pages for this review. This is a local preflight workflow; it does not upload or order.

Record the exact artifact and export revision, selected process, SKU/material/thickness, quantity and optional bending/tapping/countersinking/hardware/finish. If intent is ambiguous, compare concrete options before giving a readiness judgment. Do not assume all services apply to all stock.

Use the bundled oxygen-cad-dfm `part_facts.py` for STEP and oxygen-cad-dxf `profiles.py inspect` for DXF. Keep the full bundle installed. Add targeted measurements with the CAD environment only when the method is reliable. A source parameter or screenshot is weaker evidence than the actual upload geometry.

For a cut profile review units, bounds, contour closure, duplicates and layer intent; compare measured hole/web/edge distances only when available. For bending review tooling radius, local flange depth on both sides of every bend, relief/contact and adjacent cut geometry, using the selected material's current rules. Existing general helpers do not measure all of those: list missing local measurements rather than infer a pass. For STEP, distinguish an actual solid from a surface envelope and section DXF from a true unfolded blank.

Each finding needs a measured fact, source URL/access date and applicable section/threshold. Current source fetch failures, conflicts or missing data leave dependent checks unverified. Vendor JSON endpoints are not assumed stable or part of Oxygen's API. Report readiness only for the selected scope with every required check covered; otherwise provide a specific change/evidence list. Keep diagnostics derived from real geometry; a generated illustration is not measurement evidence.
