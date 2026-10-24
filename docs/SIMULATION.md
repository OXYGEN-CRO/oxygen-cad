# Simulation implementation ledger

The target is the complete eleven-study Fusion simulation capability map, plus
robotics load generation and the numerical/physical validation needed to trust
predictions. This ledger distinguishes working numerical workflows from remaining
implementation and physical evidence. Passing the current tests does not establish
full parity or real-world reliability.

## Current executable workflows

`o3d studies --json` returns the versioned capability manifest and setup errors.
`o3d simulate --json` and the studio run the same study model and external CalculiX
adapter. Existing projects default to `linear_static`; added schema fields have
defaults and do not require rewriting the user's source document by hand.

| Study type | Implementation | Evidence / remaining limits |
|---|---|---|
| `linear_static` | Experimental, executable | Cantilever displacement/stress, refinement and force/moment balance; small-deformation isotropic solid |
| `modal` | Experimental, executable | Cantilever frequencies compared with beam theory through both meshers; unloaded fixed-base modes only |
| `buckling` | Experimental, executable | Fixed-free column compared with Euler load; ideal eigenvalue analysis, not imperfect nonlinear collapse |
| `thermal` | Experimental, executable | Prescribed temperatures and total surface heat power; conduction/probe/heat balance checked through both meshers |
| `thermal_stress` | Experimental, executable | Temperature solve followed by mechanical solve on the same mesh; thermal-expansion reference test |
| `nonlinear_static` | Not implemented | Constitutive curves, large displacement, contact, increments and force-displacement validation |
| `quasistatic_event` | Not implemented | Event adapter, contact, energy and mass-scaling sensitivity |
| `dynamic_event` | Not implemented | Explicit solver adapter, timestep/contact/damage and physical drop correlation |
| `shape_optimization` | Not implemented | Optimization backend, preserve regions, filters, manufacturability, CAD reconstruction and reanalysis |
| `electronics_cooling` | Not implemented | Fluid domain extraction, airflow/CHT, fan/PCB models and heat-transfer validation |
| `injection_molding` | Not implemented | Polymer/process data and filling/packing/cooling/shrinkage/warpage/defect workflow |

Not-implemented studies are explicitly unavailable in the manifest and disabled in
the studio selector; the CLI refuses them rather than returning synthetic results.
The five current adapters support one connected solid, planar boundary regions and
quadratic tetrahedra. Assemblies, bolts/contact, shells/beams, anisotropy, calibrated
printed-material laws, convection/radiation and preloaded modes remain outside this
implementation. They are still part of the target where required.

## Input and unit contract

The typed project stores `study.type`, mesh backend/size/node limit, refinement
budget/tolerance, solver timeout and explicit assumption confirmation. Materials
record property source, optional process/condition and per-property source strings.
These source claims are retained, not authenticated or converted to a certificate.

- Mechanical: `fixed_faces`, total `forces[].vector_n`, elastic modulus in MPa,
  Poisson ratio and density in g/cm³. Modal analysis rejects unused forces.
- Thermal: `temperatures[]` contains a face and temperature in °C;
  `heat_sources[]` contains a face and total power in W. Conductivity is W/(m K).
  A prescribed-temperature region is required. Flux and temperature constraints
  sharing nodes refuse, as do incompatible temperatures at a shared node.
- Thermal stress additionally requires thermal expansion in 1/K and an explicitly
  supplied stress-free reference temperature. Temperature fields transfer node for
  node to the subsequent mechanical solve.
- Decks consistently use mm, N, s, MPa, tonne and °C: density converts by `1e-9`,
  heat power by `1000`, and conductivity retains the numerical W/(m K) value.
  No conductivity, expansion, load or reference temperature is inferred.
- `probe_point_mm` samples temperature or displacement at a fixed geometric point;
  `stress_probe_mm` samples the nodal screening stress field. Mesh comparisons use
  the same point and the quadratic tetrahedral shape functions.

The UI keeps missing new material/temperature/power inputs blank. An empty field
does not become a guessed production property. Forces added from a face start at
zero and must be specified before a mechanical solve can run.

## CAD → analysis and meshing

The SolidCraft build exports STEP and is independently loaded through OCCT.
Named-region transfer records the design geometry hash, semantic/strict selection,
signature, area, centroid, normal and bounding box. Netgen and Gmsh run in separate
processes from the CAD application. STEP matches must be unique by area, centroid
and bounds; an ambiguous/missing selection refuses. A face can carry separate
mechanical and thermal roles through explicit aliases without losing its mesh tag.

Both adapters emit CalculiX C3D10 ordering and quadratic boundary triangles.
Consistent surface integration puts the correct weights on midside nodes rather
than distributing traction equally among all six triangle nodes. Validation checks
finite coordinates, element connectivity, unused nodes, disconnected volumes,
corner degeneracy/orientation, boundary membership and a coarse CAD/mesh volume
scale check. This is not a certificate of every curved-element Jacobian.

The runtime includes Gmsh 4.15.2 alongside the pinned Netgen environment. Their
licences remain separate from original application code; see `THIRD_PARTY.md`.

## Results, provenance and release

Each study directory preserves the input package, region map, exact exported STEP,
each mesh request/mesh/quality audit, solver input decks, raw DAT/FRD/logs,
normalized fields and numerical metrics. The saved package's STEP path is relative
and its content hash is recorded. Mechanical and thermal solves have distinct
input/output files. Solver binaries and adapter sources have recorded SHA-256
identities; the study signature covers them as well as study/material/geometry and
relevant requirement inputs. Changing backend code/binary invalidates old results.

Static results expose displacement, screening stress, fixed-point probes, reaction
force/moment balance and mesh changes. Thermal results expose temperature, heat
reactions and heat balance. Eigenstudies expose requested values and complete mode
vectors; amplitudes are explicitly arbitrary. The studio renders temperature
ranges including negative values and allows selection of a mode with a relative
deformation scale. It does not label modal amplitudes as physical millimetres.

Runs with missing/incomplete/nonfinite output or no solver completion refuse.
Unconverged/unbalanced results are inconclusive. A linear displacement-to-model-
extent screening ratio above 5% is inconclusive; this coarse check is not a general
proof of small strain/rotation. Requirements unsupported by the selected study are
listed as not evaluated. Named stress limits require a stress probe; singular peak
stress is not silently treated as a strength certificate.

The release ZIP includes the current study's input package, analysis STEP, normalized
fields, meshes, decks, raw outputs and logs under `analysis/`, alongside the existing
design/BOM/report. A successful export does not turn a failed or inconclusive study
into an engineering pass. Preserve original imported assets for editable references.

## Verification commands

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q
cd frontend
npm run build
PYTHONPATH="$(cd ../src && pwd)" O3D_TEST_PORT=19528 npm test
```

`tests/test_simulation_studies.py` runs real CalculiX through both mesher adapters
and checks analytical modal, conduction, thermal-expansion and buckling responses,
energy balance, unsupported/missing input refusal, invalidation and release content.
The pre-existing static test remains. Browser tests exercise actual modal/thermal
jobs, fields, persistence, stale results, native-command editing and the rest of
the studio through a disposable project. Tests never edit a user's design.

## Remaining completion requirements

- [ ] Persistent named studies/load cases and comparisons; richer requirement scope.
- [ ] Broader geometry/edit fixtures, ten-part mesher bake-off and independent FE
  comparison beyond the analytical cases.
- [ ] Mode-shape correlation during refinement for repeated/crossing modes;
  currently comparisons use mode order and report that limitation.
- [ ] Assembly load paths, connectors/contact, material frames and anisotropic/process
  datasets, nonlinear laws and failure criteria.
- [ ] The remaining six study workflows in the table, each with its own evidence.
- [ ] MuJoCo/system-model actuator loads, frame-consistent transfer into FEA and
  measured actuator/contact calibration; rigid-body motion is not stress analysis.
- [ ] Parameter/topology optimization, fatigue/duty-cycle and uncertainty/sensitivity.
- [ ] Physical bracket/printed-coupon, modal, enclosure thermal, articulated payload,
  impact and molded-part correlations within declared operating/process envelopes.
- [ ] Linux/Windows/macOS distribution and resource/performance qualification;
  physical Apple Silicon execution is not established by Linux browser tests.

These unchecked items remain part of the implementation objective. No physical
measurements have been supplied or performed by this software implementation.
