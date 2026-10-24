---
name: oxygen-cad-verify
description: Check an Oxygen CAD design, inspect available studies, run a specified mechanical or thermal analysis, or export current geometry and its engineering evidence. Use for verification, simulation or handoff of an Oxygen CAD project.
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

## Engineering studies

Run `./o3d studies --json` first. It lists executable and unavailable study types and current setup errors. Use the installed capability manifest and `docs/SIMULATION.md`, not the presence of a schema enum, to establish support. Unavailable physics must not be simulated with fabricated values or silently substituted by a static study.

Read the project's schema, study and material inputs before running `./o3d simulate --json`. Material elastic properties and provenance, fully fixed planar supports, separate planar force regions, total force vectors and intended engineering limits must be supplied. If you need to change inputs, write a complete proposal and use `apply --expected-revision N --actor agent`; re-inspect after any concurrent change. Never invent load data, material certificates or passing results.

Use `study create/clone/select/delete` with `--expected-revision` for persistent studies; `simulate --study-id ID` selects a solve snapshot without changing saved selection. `study compare ID ID` reports current quantities with units and states. Geometry and material are shared; study inputs and signatures are separate. Scope simulation requirements with `study_id`, or use null for all studies.

The current adapters cover single-solid isotropic static, unloaded modal, ideal buckling, steady conduction, sequential thermal stress and a nonlinear static subset. Modal studies need density and reject applied forces. Thermal studies require explicit conductivity, temperature boundaries and any heat power; thermal stress also requires expansion and a stress-free temperature. Native inputs are mm/N/MPa, g/cm³, W, W/(m K) and °C; adapters convert to consistent solver units. Never infer missing material or operating values.

Nonlinear static accepts large deformation and/or an explicitly sourced true-stress/equivalent-plastic-strain isotropic hardening curve. An empty load path is monotonic; explicit paths share one amplitude across all forces. Inspect full-path peak values separately from the final unloaded state, every requested increment, plastic strain, and load-step sensitivity. Do not treat normalized path time as a dynamic duration or isotropic hardening as a calibrated fatigue/cyclic law. Read `docs/SIMULATION.md` for curve conventions and remaining contact/damage limitations.

Inspect solver completion, mesh refinement, relevant force/moment or heat balance, and the actual requirement findings. Eigenmode amplitudes are arbitrary; ideal buckling is not nonlinear collapse. Singular peak stress does not establish strength. Requirements outside a selected study remain not evaluated. Moving mechanisms, contact, transient/event/CFD/molding analysis, certification and physical validation remain outside these adapters. The full parity ledger remains open.

When required inputs are missing, list them and continue with any independently useful geometry checks or export requested by the user. Keep “not run,” “stale,” “failed” and “passed under the recorded assumptions” distinct.

## Handoff

After building the current geometry:

```sh
./o3d export --json
```

The returned `artifact` and `report` paths resolve under the selected project's `.o3d/`. Read `report.json` from the export directory and inspect the ZIP entries before claiming the handoff is ready. Export can package failed geometry checks and stale or absent analysis; a successful export is not an engineering pass.

The release ZIP contains STEP, STL, project JSON, BOM CSV and HTML/JSON reports; native builds can also include the native design and engine script. When current analysis exists, `analysis/` preserves its input package, analysis STEP, mesh/decks, raw solver outputs/logs and normalized fields. Other current studies are included under `analyses/<id>/`; stale studies have no package. Inspect provenance and recorded numerical/physical-validation state. The BOM identifies reference sources; original imported reference STEP assets still require the original project and `assets/` for an editable handoff. GLB, 3MF, DXF, drawings, slicer output, purchasing and sending to printers are not provided by this command.

Report the project revision, paths, measured checks, analysis state and remaining assumptions. Do not describe an exported design as manufactured, certified or ordered.

For printability/process checks use oxygen-cad-dfam/oxygen-cad-dfm and the selected supplier rules; existing geometry checks are not comprehensive DFM. For derivative drawings, DXF or robot files, inspect their separate output evidence and consumer checks. Slicer/app handoff success does not establish manufacturing success.
