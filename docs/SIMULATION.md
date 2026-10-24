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

Numerical examples are available through `init --template modal`, `buckling`,
`thermal`, `thermal-stress`, `nonlinear` and `plastic-unload` in a new project, as well as the existing `cantilever`.
Their values are explicit analytical test inputs, not certified production data.
The prepared [physical correlation protocol](PHYSICAL-VALIDATION.md) records what
must be measured before claiming physical validation.

| Study type | Implementation | Evidence / remaining limits |
|---|---|---|
| `linear_static` | Experimental, executable | Cantilever displacement/stress, refinement and force/moment balance; small-deformation isotropic solid |
| `modal` | Experimental, executable | Cantilever frequencies compared with beam theory through both meshers; unloaded fixed-base modes only |
| `buckling` | Experimental, executable | Fixed-free column compared with Euler load; ideal eigenvalue analysis, not imperfect nonlinear collapse |
| `thermal` | Experimental, executable | Prescribed temperatures and total surface heat power; conduction/probe/heat balance checked through both meshers |
| `thermal_stress` | Experimental, executable | Temperature solve followed by mechanical solve on the same mesh; thermal-expansion reference test |
| `nonlinear_static` | Experimental, executable subset | Large deformation or isotropic plastic hardening, prescribed load history, adaptive increments, full-path output; elastica and synthetic plastic unload references; no contact/damage |
| `quasistatic_event` | Not implemented | Event adapter, contact, energy and mass-scaling sensitivity |
| `dynamic_event` | Not implemented | Explicit solver adapter, timestep/contact/damage and physical drop correlation |
| `shape_optimization` | Not implemented | Optimization backend, preserve regions, filters, manufacturability, CAD reconstruction and reanalysis |
| `electronics_cooling` | Not implemented | Fluid domain extraction, airflow/CHT, fan/PCB models and heat-transfer validation |
| `injection_molding` | Not implemented | Polymer/process data and filling/packing/cooling/shrinkage/warpage/defect workflow |

Not-implemented studies are explicitly unavailable in the manifest and disabled in
the studio selector; the CLI refuses them rather than returning synthetic results.
The six current adapters support one connected solid, planar boundary regions and
quadratic tetrahedra. Assemblies, bolts/contact, shells/beams, anisotropy, calibrated
printed-material laws, contact/damage, convection/radiation and preloaded modes remain outside this
implementation. They are still part of the target where required.

## Saved studies and comparisons

The primary study remains in `study` for compatibility. `named_studies` stores up
to 30 additional studies by stable ID, with `active_study_id` selecting one.
Materials and geometry are project-wide; loads, supports, mesh and numerical
settings belong to each study. Duplication retains inputs and clears assumption
confirmation. Requirements with `study_id: null` apply to all studies; an explicit
ID scopes a simulation requirement to that study. Geometry checks stay global.

```sh
o3d study clone service_load --name 'Service load' --expected-revision 1
o3d study select default --expected-revision 2
o3d simulate --study-id service_load --json
o3d study compare default service_load
```

Commands use the same revision-checked document path as the studio. A one-off
`simulate --study-id` does not change saved selection. Inspect `studies --json`
for inputs, setup errors, per-study signatures, states and current results. Changing
one study's load invalidates only that study; changing shared material/geometry
invalidates every dependent study. Names alone do not invalidate numerical results.
The studio creates/duplicates/selects studies and compares current quantities with
units and result states. Results disappear during unsaved edits. A comparison does
not assert that different loads/supports describe an equivalent physical problem.

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
- Nonlinear static: `large_deformation` selects geometric nonlinearity;
  `nonlinear_increments` bounds the maximum normalized load increment and defines
  observation times. `max_nonlinear_increments` bounds accepted solver increments; solver cutback limits apply separately. An empty
  `load_path` means `(time,factor)=(0,0)..(1,1)`; otherwise supply strictly increasing
  normalized times starting at zero load and ending at time one. Every force is
  multiplied by the same piecewise-linear amplitude; forces retain world-frame
  directions (dead nodal loading), not follower-pressure semantics. Time is path
  position, not a physical transient. Output includes every requested observation
  and path kink. Incomplete paths refuse; adaptive internal increments remain in
  the raw STA/log. Mesh convergence compares the same full-path probe observations.
- Optional `material.plastic_curve` stores true von Mises stress in MPa versus
  equivalent plastic strain. At least two points are required, first plastic strain
  zero, strictly increasing strain and nondecreasing stress. Retain a separate
  `plastic_curve_source` explaining convention/data provenance. No conversion from
  engineering stress/total strain is guessed. This is isotropic hardening, with no
  kinematic hardening, cyclic calibration, damage, fracture, hyperelasticity or
  rate/temperature dependence. Small-deformation nonlinear studies require a plastic
  curve. Other adapters reject plastic curves rather than silently ignoring them. Response beyond the supplied curve strain range is inconclusive; the nodal screening fields do not establish integration-point material validity.
- Decks consistently use mm, N, s, MPa, tonne and °C: density converts by `1e-9`,
  heat power by `1000`, and conductivity retains the numerical W/(m K) value.
  No conductivity, expansion, load or reference temperature is inferred.
- `probe_point_mm` samples temperature or displacement at a fixed geometric point;
  `stress_probe_mm` samples the nodal screening stress field. Mesh comparisons use
  the same physical point, inverting the full quadratic geometry map before
  evaluating the field. The four corner coordinates alone are insufficient for
  curved cells. Default probes come from CAD rather than a changing mesh average: a
  trimmed-face centroid if it lies in material, otherwise an interior CAD
  tessellation patch; unloaded thermal-stress studies use a CAD solid-interior
  point. The chosen coordinates/method are recorded. An explicit point in a bore
  or outside material refuses; it is never silently moved.

The UI keeps missing new material/temperature/power inputs blank. An empty field
does not become a guessed production property. Forces added from a face start at
zero and must be specified before a mechanical solve can run.

The deck conventions follow the [CalculiX 2.23 manual](https://www.dhondt.de/ccx_2.23.htm.tar.bz2), including `*PLASTIC`, `*AMPLITUDE`, `*STATIC` and `*TIME POINTS`. Large deformation alone does not establish that a material model is valid at arbitrary large strain.

## CAD → analysis and meshing

The SolidCraft build exports STEP and is independently loaded through OCCT.
Named-region transfer records the design geometry hash, semantic/strict selection,
signature, area, centroid, normal and bounding box. Netgen and Gmsh run in separate
processes from the CAD application. STEP matches must be unique by area, centroid
and bounds; an ambiguous/missing selection refuses. A face can carry separate
mechanical and thermal roles through explicit aliases without losing its mesh tag.

Both adapters emit CalculiX C3D10 ordering and quadratic boundary triangles.
Planar boundary integration includes the full quadratic surface Jacobian. For
affine triangles the corner weights vanish and each midside weight is area/3;
curved planar triangles can have nonzero or negative corner weights. Gauss4/Duffy
integration preserves force, first moment and heat power. It does not clamp
legitimate negative consistent nodal loads or replace them with equal weights.

Validation checks finite coordinates, unique/conforming connectivity, duplicate or
nonmanifold cells, unused nodes, complete-face volume connectivity (point/edge
contact alone is insufficient), boundary membership and midside ordering. Each
mesh then goes through an isolated Gmsh quality worker, regardless of its mesher.
Gmsh's `minDetJac`/`maxDetJac` return adaptive Bernstein bounds over the complete
quadratic element; checking corners, interpolation nodes or quadrature samples
alone can miss a fold. Nonpositive bounds or a lower/upper ratio <=1e-6 refuse.
Degree-four tetrahedral quadrature supplies actual curved-cell volumes. The 10%
CAD-volume gate remains a coarse scale/transfer guard, not an accuracy promise.
Selected planar regions must match their CAD plane, integrated area (2% envelope)
and centroid (1% of square-root area, with the stated absolute tolerance).

`curved_jacobian_checked` records this whole-element floating-point library check.
`curved_jacobian_certified` remains false: this is not a formal outward-rounded
interval proof, a global overlap proof, a check of every future deformed
configuration or physical validation. Raw bounds, exact mesh hash, integrated
volumes and backend binary identity remain in `curved-quality.json`.

Netgen uses curvature safety 4 and two segments per edge; Gmsh runs its CAD-aware
high-order optimizer after creating quadratic elements. Their settings are
recorded. Both must pass the independent quality gate; there is no fallback that
accepts an invalid mesh. Field display subdivides each quadratic boundary face
into four triangles so midside geometry/results participate in the preview.

The runtime includes Gmsh 4.15.2 alongside the pinned Netgen environment. Their
licences remain separate from original application code; see `THIRD_PARTY.md`.

## Results, provenance and release

Each study directory preserves the input package, region map, exact exported STEP,
each mesh request/mesh/quality audit, solver input decks, raw DAT/FRD/logs,
normalized fields and numerical metrics. The saved package's STEP path is relative
and its content hash is recorded. Mechanical and thermal solves have distinct
input/output files. Solver binaries, adapter sources and installed mesher interpreter/library/package files have recorded SHA-256
identities; the study signature covers them as well as study/material/geometry and
relevant requirement inputs. Changing backend code/binary invalidates old results. A runtime change during a study refuses completion. Refinement must add nodes/elements, decrease maximum integrated cell volume and change the mesh fingerprint; repeating one mesh cannot earn convergence.

Static results expose displacement, screening stress, fixed-point probes, reaction
force/moment balance and mesh changes. Nonlinear results preserve each load observation, displacement/stress/plastic-strain fields, peak-over-path metrics and distinct final-state metrics. Requirement displacement/stress checks use the whole path, so unloading cannot hide an earlier overload. Large-deformation moment balance uses deformed node positions. Thermal results expose temperature, heat
reactions and heat balance. Eigenstudies expose requested values and complete mode
vectors; amplitudes are explicitly arbitrary. Four additional spectral guard
modes are solved and retained to detect a requested window that cuts a repeated
family. CalculiX buckling output starts with the static reference-load response
at factor zero; it is excluded from eigenmodes. Every remaining displacement
frame's recorded factor/frequency must match the corresponding DAT eigenvalue.
The studio renders temperature
ranges including negative values and allows selection of a mode with a relative
deformation scale. It does not label modal amplitudes as physical millimetres.

Runs with missing/incomplete/nonfinite output or no solver completion refuse.
Unconverged/unbalanced results are inconclusive. A linear displacement-to-model-
extent screening ratio above 5% is inconclusive; this coarse check is not a general
proof of small strain/rotation. Requirements unsupported by the selected study are
listed as not evaluated. Named stress limits require a stress probe; singular peak
stress is not silently treated as a strength certificate.

The release ZIP includes the active current study under `analysis/` and other current studies under `analyses/<id>/`: input packages, analysis STEP, normalized fields (including nonlinear observations), meshes, decks, raw outputs and logs. `report.json` records all study states and package locations. Stale studies have no numerical package. Each export uses a fresh staging directory and an explicit manifest, preventing a previous export from leaking stale evidence into the ZIP. The complete ZIP is published atomically after verifying the saved revision. These accompany the design/BOM/report. A successful export does not turn a failed or inconclusive study
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
the studio through a disposable project. Named-study tests check doubled-load response, independent invalidation, comparison, scoped requirements and releases. Nonlinear tests compare against an independent elastica boundary-value solution and analytical synthetic plastic loading/unloading, including load-step sensitivity and incomplete-history refusal. Browser tests configure plasticity and inspect load-increment fields. Tests never edit a user's design.

## Reproducible mesh and independent-solver checks

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/qualify_mesh.py \
  --output /tmp/oxygen-new-mesh-qualification --refinements 2
```

The script refuses an existing output directory, creates only disposable projects
and retains each exact mesh/request/bound/log. Ten **synthetic task-shaped**
families cover a cantilever, camera bracket, bored/bolted plates, thin panel, slot,
fillets, ribs, tube and open enclosure. Two sizes through each mesher produce 40
runs. This does not substitute for ten actual customer parts. The initial run
found two invalid coarse meshes; the final settings pass all 40, with selected-face
area error below 0.052% and integrated volume error below 0.024% on these fixtures.

An optional independent checker uses scikit-fem 12.0.2, installed with the pinned
wheel hash into an existing pinned scientific/CAD environment (or a separate
environment containing its NumPy/SciPy dependencies):

```sh
.venv/bin/python -m pip install --no-deps --require-hashes -r verification-requirements.lock
PYTHONPATH="$PWD/src" O3D_REFERENCE_PYTHON="$PWD/.venv/bin/python" \
  .venv/bin/python -m pytest -q tests/test_reference_solver.py
```

The standalone reference worker imports no Oxygen FEM assembly, boundary-load or
interpolation routines. It reconstructs the same isoparametric C3D10 geometry,
assembles independent elastic/conduction matrices and facet loads, applies the
recorded support/temperature regions and solves through SciPy. Four cases cover
cantilever, hollow-tube and bored-plate structure plus hollow-tube conduction.
Comparisons include full displacement/temperature fields, consistent nodal
load/heat vectors, strain energy and equilibrium. Four-point cell integration
matches the CalculiX formulation; a separate order-six solve measures quadrature
sensitivity. Gates are 0.2% for agreement/balance and 2% for quadrature sensitivity.
The current fixtures agree below 0.00004% in displacement/temperature and below
0.013% under the higher quadrature order.

These are numerical cross-checks on the **same** material/geometry/BC idealization,
not physical validation or independent approval of the setup. Stress averaging,
nonlinear/contact/buckling and other study families are not independently
qualified by those four cases. Two further unloaded-modal cases use independent
stiffness and mass assembly plus a SciPy generalized eigensolve. Rectangle and
square-section beams cover distinct and repeated bending modes through Netgen
and Gmsh. Compare full common-mesh nodal subspaces (minimum squared principal
cosine >0.995), frequency differences (<0.2%), eigen-equation residuals (<1e-7)
and integration-order sensitivity (<2%). This nodal subspace check is separate
from the application's volume-weighted cross-mesh tracker. Neither establishes
measured modal accuracy. CI installs the optional hash-pinned wheel into a
disposable container for a separate check; production startup does not require it.

## Eigenmode tracking during mesh refinement

Frequency order alone cannot identify a mode across remeshing. The adapter maps
complete quadratic eigenvector fields onto common physical quadrature points
from **every cell** of the preceding mesh. A bounding-volume hierarchy and damped
Newton inversion use the full C3D10 geometry; node IDs are unrelated and points
outside the new mesh refuse correlation rather than being moved/extrapolated.
Positive physical volume weights define an L2 inner product. Constant isotropic
density cancels here; this convention does not cover multi-material assemblies.

Squared normalized inner products (MAC) and maximum-weight one-to-one assignment
match sign/scale-independent shapes. A transitive 1% relative spectral-gap rule
on either mesh groups close/repeated families. Their **whole subspaces** are
compared with principal angles, so rotating a repeated pair's basis is allowed.
Rank-deficient duplicate vectors refuse. Every requested family must have minimum
squared principal cosine >=0.98, and matched scalar values must meet the user's
convergence tolerance. Eigenvalues inside a close family are compared as sorted
sets; mode labels remain in the current solver's ascending order.

Positive tensor Gauss4/Gauss5 quadrature under a Duffy tetrahedron map checks all
coarse cells. Gauss5 exactly integrates the preceding quadratic field products
times its cubic geometry determinant; transferred piecewise fields are sampled
numerically. The two orders' MAC entries touching requested modes must differ
by <=0.005, and both shape checks must pass. This is a practical integration
sensitivity check, not a rigorous bound on every unresolved oscillation.
The combined cloud is limited to 1,000,000 points per comparison. Missing points,
exceeded resources, a low score, or a family crossing the requested mode window
prevent that comparison from establishing convergence. A later complete passing
comparison may establish convergence after further refinement; the earlier failed
audit is preserved. The real curved hollow-tube fixture exercises this case: a
coarse comparison misses four physical points, while the final comparison covers
all points and passes. Without a passing comparison the study is **inconclusive**.
Increase the mode count to include a complete
family; use a coarser initial mesh if the comparison resource limit is exceeded.

`mode-tracking.json` retains source/target mesh hashes, cloud hashes, volumes,
counts, both MAC matrices, matching, family/rank/window diagnostics, scalar
changes, thresholds and integration sensitivity. It is included in study exports.
The study panel shows correlation status and per-family agreement. Arbitrary
eigenvector amplitudes remain relative. Mode shapes are not physical dynamic
displacements, and linear buckling is not nonlinear collapse prediction.

`tests/test_mode_tracking.py` covers crossings, repeated-family rotations,
truncated windows, duplicate/missing/zero fields, changed topology/node IDs,
curved geometry, outside-point refusal and real modal/buckling beam families.
The optional independent reference and the browser modal workflow check the
actual computed fields and exposed correlation evidence.

## Remaining completion requirements

- [x] Persistent named studies, independent loads/results, current-result comparison and scoped simulation requirements. Independently varying loads within a shared study remain separate named studies.
- [x] Ten synthetic CAD task families through both meshers/two sizes, complete
  curved-element/region checks, and four independent static/conduction comparisons.
- [ ] Actual customer-part/edit fixtures, assembly/repeated-occurrence topology,
  broader independent FE families and physical validation remain required.
- [x] Volume-weighted mode-shape correlation during refinement, repeated-family
  subspaces, spectral guard modes and refusal of a cut requested window.
- [ ] Adaptive correlation integration/error bounds for unresolved oscillations,
  larger resource budgets, multi-material mass products and measured modal data.
- [ ] Assembly load paths, connectors/contact, material frames and anisotropic/process
  datasets, nonlinear laws and failure criteria.
- [x] Nonlinear single-solid large-deformation/plastic-hardening slice, full load-path evidence and numerical reference cases.
- [ ] The remaining five study branches and missing nonlinear contact/material capabilities in the table, each with its own evidence.
- [ ] MuJoCo/system-model actuator loads, frame-consistent transfer into FEA and
  measured actuator/contact calibration; rigid-body motion is not stress analysis.
- [ ] Parameter/topology optimization, fatigue/duty-cycle and uncertainty/sensitivity.
- [ ] Physical bracket/printed-coupon, modal, enclosure thermal, articulated payload,
  impact and molded-part correlations within declared operating/process envelopes.
- [ ] Linux/Windows/macOS distribution and resource/performance qualification;
  physical Apple Silicon execution is not established by Linux browser tests.

These unchecked items remain part of the implementation objective. No physical
measurements have been supplied or performed by this software implementation.
