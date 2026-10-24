---
name: oxygen-cad-verify
description: Check an Oxygen CAD design, run a specified linear static study, or export its current STEP/STL and engineering report. Use for verification, simulation or handoff of an Oxygen CAD project.
---

# Verify and export Oxygen CAD

Locate the checkout from the plugin root (two directories above this skill) or the standalone skill's `runtime.json` repository. Verify `o3d` and the `oxygen-cad` pyproject. Run commands there with the same project variables as the studio. For native execution use the absolute `O3D_PROJECT_DIR`.

```sh
./o3d inspect --json
./o3d build --json
./o3d check --json
```

Read `geometry_state`, `analysis_state` and current evidence separately from `last_build`. Changes invalidate affected results. A check/simulation can exit with code 2 and still emit a useful JSON failure result: read it. A process finishing does not establish a passed check.

## Geometry and fit

Inspect measured dimensions, connectedness/validity, supplied-density mass, reference interference/clearance and the independent mesh audit as relevant to the user's requirements. Preserve the actual stated tolerances. A missing clearance rule or material is missing information, not an automatic pass. Numeric fit checks are bounded by the supplied models; they do not establish first-print fit or supplier acceptance.

## Static study

Read the project's schema, study and material inputs before running `./o3d simulate --json`. Material elastic properties and provenance, fully fixed planar supports, separate planar force regions, total force vectors and intended engineering limits must be supplied. If you need to change inputs, write a complete proposal and use `apply --expected-revision N --actor agent`; re-inspect after any concurrent change. Never invent load data, material certificates or passing results.

The solver uses mm/N/MPa, small-deflection linear elasticity and a single-solid static model. Inspect solver status, mesh refinement/convergence, force/reaction balance, displacement and stress against the specified requirements. A singular peak at a sharp corner or fixture needs interpretation. Moving mechanisms, contact, thermal/transient analysis, certification and physical validation are outside this workflow.

When required inputs are missing, list them and continue with any independently useful geometry checks or export requested by the user. Keep “not run,” “stale,” “failed” and “passed under the recorded assumptions” distinct.

## Handoff

After building the current geometry:

```sh
./o3d export --json
```

The returned `artifact` and `report` paths resolve under the selected project's `.o3d/`. Read `report.json` from the export directory and inspect the ZIP entries before claiming the handoff is ready. Export can package failed geometry checks and stale or absent analysis; a successful export is not an engineering pass.

The release ZIP contains STEP, STL, project JSON, BOM CSV and HTML/JSON reports; native builds can also include the native design and engine script. The BOM identifies reference sources; the ZIP does not include their original imported STEP assets. Keep the original project and `assets/` when an editable, reproducible handoff with references is needed. GLB, 3MF, DXF, drawings, slicer output, purchasing and sending to printers are not provided by this export command.

Report the project revision, paths, measured checks, analysis state and remaining assumptions. Do not describe an exported design as manufactured, certified or ordered.
