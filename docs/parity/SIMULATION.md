# Simulation implementation and verification boundary

The simulation domain stores independent analysis models, revisioned sourced material cards, studies and load cases. Its public entry points are `validate(data)` and `execute(store, project, operation, payload, cancel)`. Domain records use schema version 1 and SI-derived engineering units: mm, N, s, tonne, MPa, K and W. Thermal conductivity is entered in W/(m·K), film coefficient in W/(m²·K), surface heat flux in W/mm² and body heat as total W. Gravity and acceleration vectors use mm/s². Positive pressure is compression. All vectors are world-frame vectors.

The native geometry bridge consumes exact per-body STEP plus face ID, body ID, centroid, area and normal descriptors. It refuses ambiguous regions, surfaces and display meshes. Netgen runs in a separate interpreter and emits the declared C3D4 or C3D10 family with corresponding three- or six-node boundary triangles. Bodies retain separate connectivity; proximity does not create a bond. Explicit reference meshes are also accepted with declared provenance and an exact geometry fingerprint. A supplied fingerprint or CAD-volume field is an association claim, not independent proof that a user mesh represents the CAD.

## Canonical quadratic transfer

Adapter `oxygen-canonical-5` connects static and nonlinear C3D10 CalculiX studies
to these same canonical models, materials, cases and result signatures. No legacy
Project is reconstructed, no midside node is discarded and no material/load is
invented. Multiple bodies/materials are tested as separately supported solids;
this does not establish quadratic contact or general assembly parity. Quadratic steady thermal/prescribed-film electronics conduction also uses these records, without elastic-property prerequisites. Other
canonical study families still use their C3D4 adapters. Sequential linear thermal-stress transfer is connected with explicit expansion and stress-free temperature. The separate legacy
quadratic study path is retained pending complete unification/migration.

Quadratic thermal uses full ten-node conduction and volume heat, six-node
consistent power/flux/film/radiation integration, Kelvin inputs and explicit
material conductivity. Every disconnected solid requires heat rejection or a
temperature anchor. Interfaces, mechanical supports/loads/history and connectors
refuse. Quadrature sensitivity, safeguarded nonlinear radiation, thermostat
reactions, heat-scaled residuals and rise-scaled mesh checks remain explicit.
Temperature limits use conservative Bernstein bounds of the complete quadratic
interpolation; nodal/quadrature extrema and volume-average heat flux are labeled
separately. No airflow/CHT, material calibration or physical passing value is
inferred. Neutral exports retain thermal solution JSON and numerical meshes;
there is no executed CalculiX thermal deck from this SciPy adapter.

Quadratic thermal stress uses the same full mesh and sourced materials in
conduction and a subsequent linear elastic CalculiX solve. Explicit stress-free
kelvin and expansion are required. Each solved nodal temperature is serialized,
checked for coverage/precision and written with EXPANSION/INITIAL CONDITIONS/
TEMPERATURE. Phase IDs and temperature hashes are retained. Mechanical inputs
remain in the structural phase; no thermal eigenstrain is duplicated as CLOAD.
Its integrated equivalent vector provides a nonzero verification scale for
thermal-only reactions. Both phase residuals and temperature/displacement mesh
convergence are required. Full integration-point stress and mean normal stress
remain distinct from extrapolated nodal von Mises screening; declared yield
exceedance or large linear thermal strain makes the result inconclusive. The
neutral archive includes both phases and the exact executed structural deck.
General nonlinear/coupled feedback, thermal contacts and calibrated material or
failure criteria remain unfinished.

Geometry qualification is independent of elastic properties. It checks conforming
connectivity, rejects point/edge-only connections, checks boundary identity/order,
uses isolated Gmsh whole-element Jacobian bounds and independently compares
integrated cell volumes with the load quadrature. The exact serialized coordinates
are audited. CalculiX's 20-character numeric readers previously truncated some
long exponent tokens; scientific formatting now fits complete tokens, records
coordinate rounding and refuses insufficient coordinate resolution at large
origins. Actual generated meshes exercise this failure and correction.
Label values as well as counts are bounded before invoking CalculiX; sparse
imported labels cannot inflate its label-indexed allocations. Quality results
record the mapping from audit indices to actual solver element IDs.

The New study material import maps the legacy thermal/plastic field names
explicitly and preserves process, condition and all per-property citations.
Original citation keys remain; canonical aliases are added without overwriting
explicit alternatives. Metadata retention does not create a calibrated process
or constitutive model. A real browser/API roundtrip verifies the imported card.

Refinement retains the count/maximum-cell-volume checks below. A smaller size
request sometimes produces a larger worst cell; generation now measures the
candidate and can lower the size within four bounded attempts rather than
claiming refinement. All attempts are retained. Worker checks repeat independently
on the actual mesh, so a user-supplied repeated/renumbered mesh still cannot pass.

Quadratic force/pressure transfer integrates shape functions, area and physical
normals with Gauss6/Gauss8 sensitivity checks. Body gravity integrates signed
quadratic weights with sourced density; negative corner weights are genuine.
Remote force/moment transfer preserves the requested resultant and moment, with
its nodal idealization explicit. Fixed/world-axis partial supports are translated
without rotation/MPC approximations, and each independent solid must restrain
all six rigid motions. Quadratic contact/bolts/inertial relief, bearing/hydrostatic,
modal/buckling translation remains unavailable and refuses execution. Steady solid thermal transfer is described above.

User-declared `probe_point_mm`/`probe_body_id` settings identify a stable physical
static displacement probe for either canonical family. Outside points refuse;
multi-body probes require identity. Quadratic numerical verification additionally
requires the probe and its refinement. Nonlinear output includes every recorded
increment's stress/displacement/probe extrema and equilibrium, plus separately
labeled final fields. Stress maxima remain unverified screening values.

The neutral handoff includes complete immutable numerical-run evidence and file
hashes. The exact executed finest-mesh deck is preserved where available;
regenerated compatible decks are labeled. Export staging is unique, hashes are
verified and changed inputs refuse publication. Numerical meshes are included;
the source CAD/STEP and Ansys study semantics are not.

## Linear reference adapters and remaining capabilities

The NumPy/SciPy worker assembles constant-strain C3D4 stiffness, consistent face tractions and lumped translational mass. It solves small-strain linear elasticity, solid conduction with nonlinear radiation, sequential thermal expansion, mass-normalized modal eigenpairs, initial-stress buckling and a bounded central-difference event subset. Axis and arbitrary plane-normal constraints are eliminated using local orthonormal bases. Linear tetrahedra can lock or over-stiffen bending; near incompressibility and thin sections require particular care. The buckling regression deliberately demonstrates coarse-mesh stiffness error instead of calling a finite eigenvalue an accurate critical load.

Adapter `oxygen-tet4-2` corrects an earlier density conversion: structural cards
in g/cm³ convert by `1e-9` to tonne/mm³; hydrostatic fluid inputs already in
kg/m³ convert by `1e-12`. The earlier adapter underreported mass and gravity
forces by 1000 and overpredicted modal frequencies by √1000. Inertial-relief
accelerations, event timing/energies and affected requirement/search outcomes
also require recomputation. Previous inputs and immutable artifacts are retained;
the new adapter and unit-source fingerprints mark their results stale. The
separate quadratic CalculiX path already used the correct `1e-9` conversion.
Independent SI regressions cover actual mass, weight/reactions, axial frequency,
inertial-relief acceleration and free-flight displacement/work/kinetic energy;
an actual CalculiX solve checks translated gravity and deck density. These checks
do not establish a real material card or physical validation.

The same adapter corrects support-reaction accounting when a nodal load acts on
a constrained component: CalculiX `RF` includes the applied load, so the adapter
subtracts the final history-scaled `CLOAD` there. Raw solver forces, applied loads
and corrected support reactions remain in the field artifact. No correction is
applied to unconstrained components. This is distinct from the density fix.

Contact is a sourced finite-stiffness paired-node idealization: elastic bonded springs or frictionless normal separation springs, with explicit gap and active-set reporting. It is not general surface-to-surface sliding, Coulomb friction, self-contact or automatic contact qualification. Detection proposes one-to-one node pairs and reports ambiguity; it never silently inserts physical interface properties. Bolts are axial elastic trusses with specified preload; thread, washer, bending and torque conversion are absent. Inertial relief explicitly assembles translational/angular acceleration and removes only six rigid displacement gauges for a connected free solid.

CalculiX is invoked as a separate GPL-licensed executable for nonlinear C3D4/C3D10 studies and quadratic linear-static cases. The adapter translates isotropic elasticity, supplied true-stress/equivalent-plastic-strain J2 hardening, optional finite deformation, dead nodal loads and world-axis zero supports. Increment histories support loading and unloading. It checks complete step time, per-increment convergence records, constrained-DOF reactions and load-step sensitivity. It refuses unimplemented nonlinear contact/connector and follower-load translation. An analytical plastic bar reproduces both the loaded strain and permanent strain after unloading. Material curves in tests are synthetic and do not establish a real grade or process calibration.

Events use physical lumped mass, an explicit stability bound including potential closed-contact stiffness, user load histories, initial velocity and optional viscous damping. No mass scaling is applied. Timestep refinement, work/kinetic/strain/damping energy and significant-energy kinetic/internal ratios are reported. Peak scalar stress/displacement are accumulated at **every** integration step; displayed history decimation never defines an extremum. Stored spatial fields are labeled final-state fields and include a separate stress envelope. Large motion, rate laws, damage/deletion, prescribed-motion events and advanced impact qualification require another adapter. A quasi-static label requires a sufficiently small kinetic/internal ratio over the significant strain-energy history.

Filtered SIMP evaluates compliance under a volume constraint, preserves explicit cells, excludes explicit obstacle cells and independently reanalyses the thresholded tetrahedral solid. Disconnected or ill-conditioned reconstructions fail reanalysis. Gray-density volume and retained-solid volume are separate outputs. Generative evaluation runs bounded sourced material alternatives, evaluates limits on reconstructed solids and reports volume misses. Neither path claims a global optimum, manufacturing-specific constraints, editable CAD reconstruction or final-CAD verification. Optimization studies remain inconclusive pending the latter work.

Electronics cooling currently provides a solid/prescribed-film thermal calculation with explicit powers and heat rejection. It supplies no air domain, fan solution, buoyancy or conjugate CFD. General CFD, fatigue, harmonic/random response, transient thermal and molding adapters refuse execution. The capability register records these boundaries across all 20 audited families.

Every run retains a unique immutable run directory, exact inputs and source/package/backend fingerprints. Relevant edits produce a new signature; results remain inspectable and are marked stale. Cancellation propagates through the scientific worker to nested solver process groups. The API's generic jobs layer supplies asynchronous execution and cancellation. Study/model clones return domain edits for Store revision checks rather than writing the project file directly.

Equation residuals, force/moment or heat balance, energy, genuine mesh refinement and timestep/load-step sensitivity are distinct evidence. Duplicate or renumbered identical tetrahedral geometry cannot demonstrate refinement. A refinement must add nodes/elements and reduce maximum cell volume. Static solutions check factor pivots for all problem sizes and additionally test positive stiffness on small systems. A clean equilibrium residual cannot establish a well-posed constraint system. Stress maxima remain screening values without a converged nonsingular stress probe. Requirement outcomes remain separate from numerical verification and physical validation; no whole-product safety badge is produced.

The workspace panel preserves the existing application shell and themes. It edits sourced materials and cases, binds selected CAD faces, runs meshes/studies, opens exact snapshots, compares studies and renders numerical fields in a local Three.js view. Advanced typed inputs remain available as JSON. Reports include numerical evidence and limitations. Handoff exports a neutral versioned ZIP and compatible CalculiX decks with a transfer-loss manifest. It does not create an Ansys `.sdz` archive or claim geometry/condition parity with Ansys.

Validation uses disposable projects. The browser form fixture verifies real panel interactions and callback persistence; it deliberately cannot execute a solver. Python tests execute the actual numerical and external-worker paths. Linux results do not establish macOS packaging, hardware correlation or complete Fusion parity.
