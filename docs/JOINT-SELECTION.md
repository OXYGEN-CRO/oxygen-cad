# Requirements and engineering-aware component selection

Project tools → **Joint selection** opens a saved requirement/component workflow. The browser and `o3d joint` use the same typed project document and revision-checked Store. This is a bounded static selection workflow, not an autonomous humanoid engineer or physical qualification service.

## First task

1. Load the illustrative elbow or add an empty joint requirement. The example is synthetic and its static-model confirmation starts off.
2. Enter the payload and its horizontal moment arm, one link's mass and center-of-mass moment arm, duration, full supply range, ambient, cooling, duty, controller bus/protocol/connector and assumptions. Unknown values stay unknown. Confirm the horizontal static model only when it describes the task.
3. Load sourced actuators or import an exact catalog contract. Save using the ordinary Save changes action.
4. Evaluate. Each exact candidate shows per-check results, reasons and source links. Selecting a candidate does not turn missing evidence into a pass.
5. Change the task, geometry or a component/source revision. The old report remains auditable but is stale; evaluate the saved revision again.

The bundled library contains 12 ROBOTIS variants: XM430-W350/W210, XM540-W270/W150 and XH430-W350/W210, each as TTL `-T` and RS-485 `-R`. Each carries a dated official source URL, original-page hash, selected-variant identity, voltage/pinout/protocol facts and a **stall rating at 12 V**. No sustained rating, exact hardware revision, mounting qualification, stock or CAD rights is invented. Interface facts describe the documented variant; physical firmware/configuration/assembly still need verification.

A nominal stall screen may pass while sustained and mounting checks remain unknown. Matching a nominal aligned rectangular mount is also unresolved until fastener engagement, tolerances and clearance have separate evidence. The system does not claim physical fit from a STEP file or a bolt-grid match.

## CLI

Use the native launcher after normal setup, or the equivalent `python -m o3d.cli` in the installed environment:

```sh
O3D_NATIVE=1 ./o3d --project /tmp/joint-project init --json
O3D_NATIVE=1 ./o3d joint example --json > /tmp/joint-input.json
# Edit the synthetic inputs/assumptions before applying them to a real task.
O3D_NATIVE=1 ./o3d --project /tmp/joint-project joint configure /tmp/joint-input.json --expected-revision 1 --actor human --json
O3D_NATIVE=1 ./o3d --project /tmp/joint-project joint evaluate --expected-revision 2 --json
O3D_NATIVE=1 ./o3d --project /tmp/joint-project joint status --json
```

`evaluate` emits JSON and exits **2** for failed or unresolved requirements. `status` is read-only. `library` returns the source-linked component snapshots. `configure` accepts only `joint_requirements` and/or `components`, preserving other brief data. All writes require the current revision. Agents cannot remove or change an accepted joint requirement's statement, conditions, assumptions or bindings; they may change the selected candidate. Humans edit those same fields through the ordinary Store path.

## Existing catalog integration

The component contract is a typed view over a normal catalog Part's `attributes.engineering_contract`. No parallel database or SQL migration is introduced. The existing Part validator checks contract shape and exact manufacturer/MPN identity. Existing catalog ingestion and access/retention policies remain authoritative.

`GET /v1/parts/{uuid}/engineering` on the catalog API returns that typed contract or an explicit error for missing/unqualified data. `o3d --project PATH joint import-catalog UUID --expected-revision N` reads an existing record through `O3D_CATALOG_DATABASE_URL`, checks identity and copies its contract into the native project with its catalog UUID. Source revisions are snapshots; vendor refreshes are not silently applied. Importing the bundled JSON into a production provider namespace is not performed by startup or tests.

Custom contracts have exact variant/scope, source claims, voltage/control/pinout data, optional nominal mount and conditioned output-side ratings. Applicable sustained ratings require explicit voltage, ambient bounds, cooling and duty, plus duration for finite hold tests. These are declared source-backed claims, not independent physical validation. Conflicting applicable ratings remain unresolved. No interpolation/extrapolation or universal stall-to-continuous conversion is used.

## Binding to the CAD document

Native geometry and named parameter identities participate in evidence signatures. A task may use `subject` to refer to a feature/reference; missing subjects remain unresolved. `parameter_refs` tracks explicit dependencies. For a demand quantity derived from a native parameter, use `bindings` and leave its literal value null, for example:

```json
{"payload_arm": null, "bindings": {"payload_arm": "length"}}
```

Only payload/link masses and moment arms support these parameter bindings in v1. Native `g`/`mm` values are converted to SI. Wrong units or missing references do not produce a calculation. A quantity cannot carry both a literal value and a binding. Changing an accepted binding is a human requirement edit. Frame transforms, CAD-derived full assembly mass/inertia and dynamics are not implemented here.

## Evidence and limits

The static model computes `9.81 * (payload_mass * payload_arm + link_mass * link_com)` with explicit horizontal moment arms. It omits acceleration, contact, transmission losses, other links and external loads. Its confirmation and assumptions are part of the saved inputs.

Results retain input signatures, evaluator version, component/source revisions, per-check explanations and the exact project snapshot under `.o3d/joints/`. Later edits never relabel an old pass as current; a pure project rename does not invalidate physics. Whole-robot thermal/control behavior, moving-load sizing, tolerance qualification, live procurement and physical testing remain separate workflows.

Run `pytest tests/test_joint_selection.py`, the disposable-Postgres `tests/catalog/test_joint_contracts.py`, and `frontend/tests/joints.spec.ts` for this feature. Use an isolated project and port for browser acceptance. No test is evidence of a physically qualified robot or macOS compatibility.
