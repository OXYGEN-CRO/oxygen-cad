# Simulation implementation ledger

The target is the complete eleven-study Fusion simulation capability map, plus
robotics load generation and the numerical/physical validation needed to trust
predictions. This ledger distinguishes working numerical workflows from remaining
implementation and physical evidence. Passing the current tests does not establish
full parity or real-world reliability.

This ledger covers the quadratic CalculiX path in `project.study` and
`named_studies`. The canonical engineering workspace in `project.domains.simulation`
has shared analysis-model/material/case records, audited quadratic static/nonlinear
CalculiX transfer, quadratic solid thermal conduction, and linear-tetrahedron adapters for the other bounded study,
contact/event/optimization subsets. Its implementation limits
are recorded in [the domain ledger](parity/SIMULATION.md). Neither layer silently
converts the other's inputs or establishes full assembly/physics parity.

## Canonical bonded assemblies

Canonical quadratic linear-static studies can connect separately meshed solids
with `Interface.formulation = "quadratic_surface_mortar"`, `kind = "bonded"`,
explicit primary/secondary face-region IDs, mapping tolerance and assumption
source. The studio edits these selections and keeps the source/tolerance through
normal revision-checked save. Every active interface is translated or refused.
Existing paired-node penalties keep their separate declared semantics.

The adapter intersects the two surface discretizations in a common chart and
integrates full six-node traces with physical curved area measures. Standard
secondary trace multipliers produce `D u_secondary = B u_primary`; the exact
L2 projection is eliminated as constraints. This is a perfect zero-thickness
variational bond, not a penalty spring or nearest-node map. Common-refinement
integration preserves constant-stress patches on nonmatching quadratic meshes.
Straight and curved graph-patch cases are checked against independent
scikit-fem and GEOS/Shapely supermeshing. The reference packages are optional
verification dependencies, not production solver dependencies.

Qualified charts have affine projected edges, while their normal geometry may
be quadratic and curved. The chart derives from local geometry, so world rotation
does not change the supported graph problem. General curved edge intersections
remain unqualified and refuse; no corner faceting fallback or node adjustment is
used. A Bernstein bound checks coincidence across each common graph patch.
Incomplete/ambiguous coverage, opposing-normal failure, point/edge-only load
paths, cyclic/dependent-support conflicts and unresolved integration refuse.
Rigid-motion checks apply to each complete bonded load path, so a bonded solid
can transmit loads without an artificial individual clamp.

Bulk stiffness uses the existing full C3D10 operator. Reduced residuals, strain
energy, actual support reactions, interface action/reaction forces and moments,
physical-point probes, sampled stress and real refinement remain inspectable.
The strong trace jump is reported separately from weak mortar continuity;
excessive relative jump requires further interface refinement. Four standard
postprocessing stress points and volume-weighted nodal recovery are screening
fields, not continuous stress bounds. Existing yield/deformation screens apply.

Neutral handoff retains bulk stiffness, constraint transformation, mortar D/B,
DOF order and hashed interface-force/geometry audits. Complete interface data is
an explicit download; ordinary results retain compact summaries. No executed
CalculiX assembly deck is claimed. Dense mortar factors/transfers are capped at
three million entries, expanded constraints at ten million entries and reduced
mechanical unknowns at 60,000. Larger/dual-space qualification remains open.

This bonded increment is linear perfect bonding. Experimental separation/friction
is described below; qualification,
adhesive compliance/damage, general connectors/preload, thermal interfaces,
bonded modal/buckling/nonlinear transfer and physical interface calibration remain
open under the complete simulation roadmap.

## Experimental quadratic separation and friction

Canonical nonlinear-static C3D10 studies now translate explicit
`surface_to_surface_penalty` interfaces with `kind = "separation"` into
CalculiX 2.23 face-to-face contact. Full six-node exterior faces retain their
owning element and S1/S2/S3/S4 identity. Primary/secondary regions must belong
to distinct bodies and each region to one connected solid. Secondary-node
ownership cannot be ambiguous across pairs. No coordinate adjustment, invented
spring or missing-interface fallback is used. Perfect linear bonds keep their
separate mortar formulation; mixed bond/contact, self-contact and connector
translation remain unavailable.

Inputs require a source, primary/secondary regions, initial geometry tolerance,
normal pressure/overclosure slope in **N/mm³**, an explicit friction coefficient
(including zero), and a maximum permitted penetration in mm. Positive friction
also requires a tangential stick slope in N/mm³. These surface slopes are not
paired-node N/mm stiffnesses. Actual mesh geometry defines gaps; the paired-node
`initial_gap_mm` cannot be used as a surface offset. The geometry tolerance only
bounds the coincident initial-contact support audit, not solver search or a
coordinate move.

The adapter first verifies independent mounting constraints or the rank of
supports plus coincident full-quadratic **normal** contact rigid spaces. Friction
is never counted as a mounting constraint. That necessary initial test is capped
at 32 solids and inherits the current bounded graph-mortar geometry/integration
limits when contact carries rigid support. Gapped/partial contacts cannot repair
an initially unsupported load path through this audit. Changing contact patches,
sliding constraints and active-set tangent stability throughout loading remain
unqualified. All surface-contact runs therefore remain **inconclusive**, even
when their numerical checks pass. Stock CalculiX output cannot establish integrated
frictional work; the pinned instrumented runtime adds the separate audit below.

An unloaded first step completes CalculiX's first-step penetration-removal
logic before the actual loading path. Without it, a series-bar probe had correct
final displacement but incorrect intermediate geometric gaps. The adapter checks
that initialization neither moves nor loads the design beyond roundoff, and
rejects hidden interference/prestress. User load parameters subtract that period;
raw solver total times and the mapping remain in evidence. This is a numerical
initialization of a rate-independent static model, not an inferred physical
waiting period. The implementation follows the official 2.23
`gencontelem_f2f.f` and `springforc_f2f.f` behavior, and retains original solver
outputs.

Each mesh runs the declared increments, half-sized increments, and a separate
half-increment trial with doubled normal/tangential penalties. Returned fields
always use the nominal penalties. Displacement-history penalty sensitivity,
full-path residuals, whole-model and per-body force/moment equilibrium, contact
pressure/overclosure and Coulomb bounds, and the supplied penetration limit are
reported. `contact.json` retains integration-point records, each pair's force and
moment resultants, spring energy, initial support audit and history. The handoff
keeps every executed trial and exports the exact executed nominal finest deck.
Spring energy is recoverable penalty energy, **not** accumulated frictional
heat/work. Integration-point coordinates/weights and frictional dissipation are
not inferred from the printed output.

The packaged runtime builds the hash-pinned upstream source with a read-only
accepted-state exporter. Numerical routines remain unchanged. An opt-in child
process flag writes `analysis.contact.jsonl` with 17-digit coordinates,
increment-start/current displacement, complete quadratic face ownership,
quadrature coordinates/weights, frozen normal/area, spring energy and plastic-slip
state. Only accepted static contact output is recorded; incomplete transactions,
nonfinite data, source/receipt mismatch or output above 512 MiB refuse. The
application checks exported identities against the independently parsed accepted
increment log. Stock external CalculiX remains usable, with work unqualified.

`contact-work.json` independently reconstructs the full quadratic geometry,
increment-start area/normal, current gap, relative state, traction resultants,
spring energy and **traction dot incremental plastic slip times area**. It checks
these against exported solver values and printed pair force/moment resultants.
Bulk internal energy integrates the four solver energy-density points with
reference-volume Jacobians. External work integrates the actual dead nodal forces
and full-precision displacement path by the trapezoid rule. Frictional dissipation
is accumulated from plastic slip, never assigned the leftover energy residual.
The energy residual remains visible. Units are N mm. An unloaded initialization
may not introduce finite energy.

The worker compares complete cumulative-slip-work histories on both increment
grids, checks energy balance against `energy_tolerance`, and adds work/energy to
mesh and penalty sensitivity. Coarse proportional-normal/shear sliding can satisfy
final displacement while failing work convergence. The independent simple-shear
reference is 0.025125 N mm; refinement must approach it. Single solves do not earn
work qualification. `contact.json`, downloadable in the studio, includes the work
audit and history; the handoff preserves every raw state/trial. Active-set tangent
stability remains unqualified even when these energy checks pass. Independent normal preload and a guided symmetric shear reversal now have
analytical work checks through the independent load histories below. General
cyclic friction, large sliding, nonlinear constitutive energy and measured
friction require further qualification.

The binary, adjacent build receipt and actual loaded shared libraries are hashed
in result provenance. Runtime library drift invalidates results or refuses an
incompatible build. The external GPL-2-only solver bundle contains the exact
upstream archive, read-only patch/header, build recipe and GPL text; original
application code keeps its MIT licence. See
[`third_party/calculix/README.md`](../third_party/calculix/README.md).

The studio edits these sourced inputs and offers pressure, shear and negative
penetration plots on secondary faces only. They are nodal recoveries, distinct
from integration-point peaks. CalculiX COPEN clips positive opening to zero;
zero COPEN cannot establish a closed interface or measure a positive clearance.
Empty CELS output is allowed only when independently printed contact count and
spring energy are both zero; required displacement/stress/contact fields remain
complete and finite. Undefined zero-area center/normal statistics stay in raw
DAT output and are not promoted to measured geometry. Fortran three-digit
exponents without an E are parsed without replacing small values with zero.

Independent SI equations check all increments of two/three-bar compression,
penalty compliance, open-interface zero tension, and guided simple-shear stick
and Coulomb sliding limits. A native CAD → UI → mesh → solver → field download
workflow is also exercised. These synthetic numerical cases do not calibrate a
material/interface, establish general sliding convergence, or prove hardware
reliability. Contact tangent qualification, broader cyclic dissipation/energy,
nontrivial sliding/unloading, general curved mapping and measured interface tests
remain part of the full roadmap.

Primary solver definitions: [CalculiX official downloads](https://www.dhondt.de/),
[2.23 HTML manual archive](https://www.dhondt.de/ccx_2.23.htm.tar.bz2), and
[2.23 source](https://www.dhondt.de/ccx_2.23.src.tar.bz2). No new production solver
or dependency is introduced by this contact adapter.

### Current contact restraint diagnostics

Adapter `oxygen-canonical-12` audits the accepted contact map at every loaded
increment when the pinned full-precision exporter is available. It checks each
closed point's force, nine friction state variables and local derivative against
the Coulomb return map. Sliding retains the normal-to-shear coupling and its
**nonsymmetric** derivative; symmetrizing it would change the law. An independent
verification executable compiles unmodified upstream `springforc_f2f.f` and
`springstiff_f2f.f`. Eight closed-point cases cover frictionless, stick, sliding
and reversed slip in axis-aligned and rotated planes, comparing the application
derivative with native stiffness and native-force finite differences. This checks
the frozen local law, not contact generation or global stability.

For small-strain isotropic elastic bulk, the audit checks every element's full
ten-node, four-point strain operator: 24 nonrigid strain directions and six rigid
directions. It then tests actual supports plus current contact rows in the six
rigid motions of each connected solid. Closed normal contact supplies normal
restraint; stick supplies two tangential directions; sliding supplies only the
transverse tangential direction. Numerically zero pressure and stick/slip
boundaries stay ambiguous. Guaranteed and maximum-possible ranks remain separate.
A deficient maximum-possible rank with a checked linear bulk kernel is reported
as a numerical rigid mechanism. Friction still cannot repair an unsupported
initial mounting configuration. Self-contact through different body labels but
shared volume connectivity now explicitly refuses.

`contact-stability.json` retains all increment ranks, state counts, local
force/state residuals, derivative asymmetry and the worst increment. Its companion
`contact-restraint-operators.npz` retains normalized constraint operators, the
supported rigid-space tangent and up to six mechanism witnesses. Witness amplitudes
are arbitrary, not a predicted displacement. The studio displays the summary;
`contact.json` includes its history and the handoff preserves both files for every
trial. The audit is capped at 32 connected solids; exceeding it records an
unperformed audit. Geometric/material nonlinearities do not inherit the linear
bulk positivity check.

The full deformable frozen-map operator is also assembled at every accepted
increment when the bulk is linear isotropic. It uses the native four C3D10
quadrature points, every signed six-node trace coefficient, exact support DOFs
and the nonsymmetric normal-to-shear friction coupling. It is capped at 12,000
total DOFs and eight million assembly entries; missing traces, one-sided states,
resource limits and nonlinear bulk are explicit incomplete/unperformed audits.

`contact-global.json` records each operator hash, LU outcome, solve backward
error and one-norm condition estimate. This estimate is a lower bound, not an
exact condition number or a stability certificate. The worst estimated-condition
frame retains bulk/contact/reduced CSR matrices, node/free/fixed DOF order,
quadratic point traces and an inverse-action displacement witness. Its symmetric
part is inspected with a dense lowest eigenpair for at most 2,048 free DOFs; a
negative direction diagnoses loss of symmetric coercivity, not dynamic friction
instability. Other frames are not assigned that spectral result. Witness
amplitudes are arbitrary, not a predicted physical displacement. Handoff retains
these files for every trial; independent scikit-fem and unmodified native-contact
kernel comparisons check full operators and force derivatives separately.

These diagnostics do **not** qualify full contact stability. Nonconservative
sliding stability, changing
projections/normals/areas, one-sided opening or stick/slip transitions and exact
accepted-state nonlinear tangent qualification remain outstanding. Full-rank rigid restraint is not a
passing physical-stability result. All contact results remain inconclusive.
Fault-injected loss-of-contact and radial-slip tests independently verify null
witnesses against a separately assembled bulk stiffness; they are diagnostic
tests, not claims that a native solver accepted an unstable equilibrium.

### Actual native Newton systems

Adapter `oxygen-canonical-14` also records the actual pre-solve native Newton
matrix, equation map, assembly displacement, right-hand side and computed linear
correction from the pinned external solver. Accepted displacements are saved
separately. This works with plastic hardening and large deformation as well as
linear-bulk contact; it does not replace nonlinear bulk with an elastic matrix.
`native-newton-audit.json` binds every committed frame to accepted STA increments,
checks the free-DOF map against declared supports, checks the final displacement
against printed results, and verifies `K * correction = rhs` by normalized backward
error. Binary/source/runtime-library and per-frame operator/file hashes accompany
the evidence. Handoff includes the original binary-format matrices and audit.

The format declares integer width and byte order. The reader rejects incomplete
payloads, unsafe paths, invalid sparse entries, nonfinite values, repeated/missing
frames, unsupported eliminated MPCs, and false solved corrections. The exporter
is opt-in (`O3D_CCX_NATIVE_TANGENT=1`) and read-only; the application enables it only
for a matching build receipt. Build inputs are frozen before compilation. Matrix
limits are 30,000 equations, eight million stored off-diagonals, 100,000 native
node labels and 512 MiB total committed binary output. Resource-limited frames
explicitly report unavailable. Paths without an export report an unperformed
audit; they do not acquire a substituted matrix.

The last assembly may precede the accepted displacement and internal material or
contact state. Even a roundoff displacement difference does not establish equal
constitutive history. `accepted_state_tangent_qualified` and
`full_contact_stability_qualified` therefore remain false. Controlled numerical
tests cover nonsymmetric sliding, synthetic plastic loading/unloading and large
geometry. An independent uniaxial check obtains plastic compliance
`L/A * (1/E + 1/H)` and elastic unloading compliance `L/(A*E)` from the exported
native matrices. These tests do not establish physical materials or general
nonconservative stability.

### Accepted constitutive and bulk force state

Adapter `oxygen-canonical-15` also stages the last post-correction native
constitutive/force evaluation, after line search and before output converts stress
measures. Its transaction commits only at an accepted increment. The reader
requires exact equality of candidate and accepted displacement and of the material
history before and after output. Source/ABI, element labels/connectivity, native
quadrature and accepted STA coverage are checked. Small-strain stress and NLGEOM
PK2 stress remain explicitly distinguished. Increment-start displacement, stress,
strain and internal state are retained for independent material replay.

`accepted-bulk-audit.json` records these bindings and original per-frame hashes.
The operator includes the full native material tensor and geometric stress
stiffness for C3D4/C3D10. Bulk plus full quadratic contact forces reproduce the
native post-correction nodal forces. Final bulk matrices/forces and worst-frame
bulk/contact matrices are exported separately; a changing bulk is saved from the
same frame as its contact operator. Unavailable state or resource limits remain
unperformed. State output is bounded to 10,000 active elements, 40,000 points,
64 state variables, 64 MiB per committed frame and 512 MiB total. Assembly retains
the existing 12,000-DOF/eight-million-entry audit budget.

State binding does not establish a material force derivative. Separate GPL probes
invoke unmodified native element and small/finite-strain J2 routines. Small-strain
plastic loading/unloading replay reproduces the saved stress, material history and
tensor; multiaxial native stress differences also agree closely. The pinned
finite-strain plastic symmetric tensor has a measured relative difference about
0.000832 from stress finite differences, with a nonsymmetric derivative. Refining
the strain step from 1e-6 to 1e-9 preserves that difference. This is a tangent
qualification limit, not evidence by itself of erroneous converged forces or a
physical hardware failure.

Plastic algorithmic tensors therefore remain explicitly incomplete as force
Jacobians pending per-state derivative qualification. They are retained rather
than replaced with elastic matrices. Exact accepted-state full contact stability,
changing projections/normals/areas, one-sided material/contact transitions and
physical calibration remain open; qualification flags stay false. Large elastic
geometry and plastic/large/combined contact cases have separate numerical state,
force and derivative-status checks. The companion native archive and probe source
retain their GPL provenance; the MIT application remains a separate process.

### Refined native material replay

Adapter `oxygen-canonical-16` adds a separate final-state material audit. With
`O3D_BULK_KERNEL_EVIDENCE` configured, its GPL material executable must match the
pinned driver, source archive, exact solver-build receipt, executable hash and
actual loaded runtime libraries. The tool identity enters the study fingerprint;
changed or unrelated tools cannot reuse prior results. An absent tool is reported
as unavailable. It is never obtained from a project-controlled command.

Every final plastic Gauss point, within a 4,096-point budget, replays the original
increment-start history and the exact deck material numbers. Native stress,
updated internal history and algorithmic tensor must match the captured state.
The audit then compares central and both one-sided native stress derivatives at
engineering-strain steps 1e-6, 1e-7, 1e-8, 1e-9, 3e-10 and 1e-10. Each column must have two successive
converged refinements and matching one-sided limits within 2e-6 relative. The
largest qualifying step is retained to limit cancellation; comparisons are per
column so a large bulk modulus cannot hide another component's discrepancy.
Yield boundaries and replay mismatches remain unqualified. Baseline results must
also agree exactly across all six independent replay calls.

`material-derivative-audit.json` records every point and its failure reason.
`material-replay-input.txt` and `material-replay-output.txt` retain exact native
inputs/outputs and hashes. `material-derivative-tensors.npz` keeps nonsymmetric
derivatives with per-point qualification masks. Only if all final plastic points
pass does `material-final-force-jacobian.npz` contain a separate candidate bulk
force Jacobian, including geometric stress stiffness. C3D4 and curved C3D10
checks differentiate every nodal force column through the native finite-strain
plastic law; they distinguish this candidate from its original algorithmic
tensor. The full solver evidence and handoff include these files.

This is a bounded numerical derivative of the native constitutive update at one
accepted state and fixed increment-start history. It does not replace the native
Newton solver tensor, upgrade earlier increments, or qualify changing contact
maps, dynamic stability or physical materials. Existing bulk/contact operator
qualification flags remain separate and conservative. The audit is bounded to
60 seconds, 32 MiB input and 128 MiB output; unsupported history, missing source,
resource limits and native failures retain explicit unavailable diagnostics.

## Canonical quadratic study integration

Simulation jobs share the platform's guarded status publication. A polling-cache
write failure can use the durable job detail. If the final detail cannot be
saved, the API reports a failed memory-only status and exposes no passing job
result; existing numerical artifacts remain available for explicit inspection.
An already committed domain revision is preserved. Inspect it before retrying.
Restart recovery does not rerun a solver, and cannot invent evidence that a full
disk never stored. See [job persistence](parity/PLATFORM.md) for the recovery and
HTTP submission contract. Job completion is separate from numerical verification
and physical validation.

The Simulate workspace can generate and solve `C3D10` meshes directly from its
versioned body/material/case records. Static, nonlinear-static, steady solid thermal, sequential thermal-stress, unloaded modal and linearized buckling studies default
to quadratic meshing; the element-family selector also retains the C3D4 path.
Existing saved C3D4 models keep their declared family. This is the first shared
quadratic transfer, not automatic migration of `project.study`/`named_studies`.

All ten element nodes and six boundary nodes reach the solver. Native body IDs,
exact per-body STEP regions and distinct sourced material assignments are
preserved. Regions require matching plane, integrated area and centroid. Every
quadratic mesh receives an isolated whole-element curved-Jacobian audit, including
the precise numerical coordinates consumed by each solver and any exported CalculiX deck. A smaller
requested mesh size alone does not count as refinement: generation checks node/
cell counts and maximum integrated cell volume, with at most four bounded size
attempts per level. The worker independently checks those conditions again.
External solver labels are bounded to 100,000 nodes and 150,000 elements,
including label values; compact sparse imported labels before using CalculiX.
Audit indices retain an explicit mapping to the actual element labels.

Creating a study from the project's legacy material maps conductivity, heat
capacity, expansion and plastic-curve fields into the canonical names while
preserving process, condition and every property citation. Canonical citation
aliases are added without overwriting an already supplied citation. These are
declared inputs; recording a process does not calibrate an isotropic material law.

The current canonical quadratic transfer supports fixed/world-axis partial
constraints, consistent force/pressure/gravity loads, and conservative remote
force/moment idealizations. Disconnected static load paths require adequate supports; validated perfect
surface bonds and experimental nonlinear face contact can connect solids. Modal rigid motions are preserved explicitly. Shared points/edges do not become a solid load path. Linear perfect surface bonds and experimental separation/friction are described above.
General contact qualification, bolts,
inertial relief and bearing/hydrostatic loads
are not yet connected to this quadratic adapter; unsupported conditions refuse.
The existing C3D4 and legacy quadratic study capabilities remain separately
listed. Full study/assembly unification remains required.

Canonical C3D10 modal studies assemble full consistent mass and isotropic
stiffness using all ten nodes. Signed quadratic row sums are never lumped.
Source density is required on every body. Matrix integration compares orders
4/6, escalating to 6/8 when necessary; invalid geometry/integration refuses.
Each independent solid's constraints define its remaining analytical rigid
space. Free bodies retain six rigid modes and partial constraints retain the
appropriate subset. No arbitrary frequency threshold classifies them. Elastic
spectra use LAPACK or scaled ARPACK shift-invert, with rigid-space projection,
Rayleigh-Ritz extraction, residuals and physical mass orthogonality checks.

Mode count means requested elastic modes; rigid modes are additionally reported,
with four elastic guard modes. Shape pairing uses positive physical mass
quadrature for each declared body/density, never cross-body interpolation.
Repeated-family principal angles, complete spectral windows, full geometric
coverage and two-order integration sensitivity are required for convergence.
A frequency-only match is insufficient. Source and loaded scientific-library
hashes invalidate changed results. Preloads, thermal fields, interfaces,
connectors, histories and initial velocity are not translated into this unloaded
quadratic study. Existing C3D4 capabilities remain separate.

Mass-normalized vectors and participation retain their engineering meaning;
separate unit-amplitude display shapes avoid magnifying mass normalization into
apparent physical motion. The viewer starts at the first elastic mode and allows
rigid-mode inspection with a type/frequency label. Neutral handoff includes CSR
stiffness/consistent-mass NPZ, DOF order, all guard/rigid/elastic fields and raw
correlation/quality evidence; no executed CalculiX modal deck is claimed.
Current resource budgets: 40 million element-matrix entries, 60,000 unconstrained
DOFs per independent component, 12 million stored field components and one
million correlation quadrature points. Larger-model qualification remains open.

Canonical C3D10 buckling solves a complete linear elastic reference-load problem,
then integrates the initial-stress geometric stiffness from all ten-node physical
gradients. The generalized equation is `(K + factor * Kg) shape = 0`.
Positive factors scale every declared dead reference load together; there is no
independent preload or follower-load stiffness. Density is not required except
when a gravity reference load needs it. Supports must remove rigid motion.
The reciprocal symmetric problem uses positive elastic stiffness as its metric,
never indefinite geometric stiffness as a mass matrix. Strict eigen residuals,
elastic-stiffness orthogonality, reference force/moment/equation checks and
quadrature sensitivity execute. Reference fields, full K/Kg, DOF order and four
spectral guards are retained. Physical-volume body-preserving correlation,
complete repeated families and real refinement are required for qualification.
Displayed shapes have unit arbitrary amplitude. Supplied yield data screen
stress scaled to the first critical load; missing yield remains unknown. A large
reference deformation or declared yield exceeded makes the result inconclusive.
This is ideal elastic instability, not imperfect collapse or postbuckling.
Contact, independent preload, thermal/history/velocity/follower inputs refuse.
The assembly/field/correlation budgets above apply; buckling additionally limits
the whole constrained solve to 60,000 free DOFs. Numerical references include SI
Euler columns and independently assembled scikit-fem straight/curved problems.

Canonical C3D10 steady thermal and prescribed-film electronics thermal studies
use sourced conductivity without requiring elasticity. They integrate all ten
shape functions for conduction/body heat and all six boundary functions for
power/flux, convection and prescribed-surroundings radiation. Conductivity is
W/(m·K), film coefficient W/(m²·K), flux W/mm², body heat total W, and temperature
absolute kelvin. No airflow or conjugate CFD is implied. Every disconnected solid
needs a thermal anchor. Mechanical supports/loads/history, interfaces and bolts
refuse rather than disappearing.

Conduction and boundary integration have quadrature sensitivity checks. Newton
iteration uses a consistent radiation tangent, positive-temperature bounds and
residual-decreasing steps. Heat balance includes prescribed-temperature reactions
and loads at those nodes. Residuals scale to actual heat, and temperature mesh
changes scale to the thermal excursion rather than absolute ambient kelvin.
Sources below temperature representation refuse; no zero-load pass is invented.
Reported temperature limits use conservative Bernstein bounds over the complete
quadratic interpolated field. Sampled extrema are separately retained, and heat
flux is labeled a volume average rather than a constant field. This conservative
bound also needs mesh convergence. The neutral handoff includes these inputs,
quality and thermal solution JSON; no executed CalculiX thermal deck is claimed.

Canonical quadratic thermal stress solves conduction first, then transfers every
exact nodal temperature into a linear isotropic CalculiX structural step on the
same mesh. Supply elasticity, conductivity, expansion and an explicit uniform
stress-free temperature in kelvin. Mechanical loads/supports stay in the
structural phase; thermal boundaries stay in conduction. No connector/contact,
plastic or nonlinear thermo-mechanical feedback is translated. The deck records
EXPANSION, INITIAL CONDITIONS and TEMPERATURE, with no duplicate equivalent
thermal CLOAD. Source/serialized temperatures, node coverage, rounding and phase
IDs are retained. An unresolvable thermal excursion refuses.

Thermal eigenstrain can create stresses/reactions without applied mechanical
force. Independently integrated eigenstrain loads scale equilibrium/residual
checks; they are not applied a second time. Both thermal and mechanical equation
checks, heat balance, thermal excursion and physical displacement-probe mesh
changes matter. Large displacement or free thermal strain remains inconclusive
under the linear screening gates. Complete four-point C3D10 integration-point
stress tensors and body-wise yield screens are retained alongside extrapolated
nodal fields. Mean normal stress is reported: a fully restrained heated solid
can have large hydrostatic stress with zero von Mises stress. A supplied yield
exceedance marks the elastic model inconclusive; absent calibration/failure
criteria remain absent. These screens are not nonsingular stress convergence.
The handoff retains both phases and the exact finest executed structural deck;
it refuses to regenerate missing thermal temperatures/decks.

Set `settings.probe_point_mm` and, for multiple bodies, `settings.probe_body_id`.
The workspace edits and saves these fields through the same Store path as CLI
`workspace save`/`workspace run`. The point must lie inside the declared body;
an outside or ambiguous point is never replaced by a convenient mesh node.
Both canonical element families can interpolate a physical static displacement
probe. Quadratic mechanical results stay inconclusive without one, and convergence checks
include the same point on every level as well as displacement maxima. Peak
stress remains a screening quantity, not a converged nonsingular stress probe.

Canonical nonlinear results retain stress/displacement/probe peaks across every
recorded increment, final fields and per-increment force/moment balance. Loading,
unloading and load-step sensitivity remain distinct checks. A final field is
labeled explicitly and need not depict the load-history peak.

Handoff packages retain the immutable run's inputs, meshes, fields, exact executed
decks, raw outputs and quality evidence with checked file hashes. A regenerated
compatible C3D4 handoff deck is labeled separately from an executed deck. Export
uses unique staging and rechecks current study inputs before publication. Source
CAD/STEP is not included; numerical meshes are included. None of this establishes
physical correlation or complete Fusion parity.

## Legacy single-study executable workflows

`o3d studies --json` returns the versioned capability manifest and setup errors.
`o3d simulate --json` and the studio run the same study model and external CalculiX
adapter. Existing projects default to `linear_static`; added schema fields have
defaults and do not require rewriting the user's source document by hand.

Numerical examples are available through `init --template modal`, `buckling`,
`thermal`, `ambient-thermal`, `thermal-stress`, `nonlinear` and `plastic-unload` in a new project, as well as the existing `cantilever`.
Their values are explicit analytical test inputs, not certified production data.
The prepared [physical correlation protocol](PHYSICAL-VALIDATION.md) records what
must be measured before claiming physical validation.

| Study type | Implementation | Evidence / remaining limits |
|---|---|---|
| `linear_static` | Experimental, executable | Cantilever displacement/stress, refinement and force/moment balance; small-deformation isotropic solid |
| `modal` | Experimental, executable | Cantilever frequencies compared with beam theory through both meshers; unloaded fixed-base modes only |
| `buckling` | Experimental, executable | Fixed-free column compared with Euler load; ideal eigenvalue analysis, not imperfect nonlinear collapse |
| `thermal` | Experimental, executable | Temperature, surface/internal power, sourced convection and prescribed-environment radiation; conduction/nonlinear/heat-balance checks through both meshers |
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
printed-material laws, contact/damage, cavity radiation/airflow CFD and preloaded modes remain outside this
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
  Supply at least one prescribed-temperature, convection or radiation region.
  Surface heat-power and temperature constraints sharing nodes refuse, as do
  incompatible temperatures at a shared node. Uniform body generation and
  surface exchange can meet temperature constraints; their nodal contributions
  are included in the external thermostat power rather than dropped.
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

## Heat generation and environment boundaries

`convection[]` requires `face`, positive `coefficient_w_m2_k`,
`ambient_temperature_c` above absolute zero and a nonblank `source` describing
the data/operating conditions. `radiation[]` requires `face`, positive `emissivity`
at most one, surroundings temperature and its source. The radiation model is a
gray diffuse surface exchanging with unobstructed uniform surroundings at unit
view factor. Coefficients/environment temperatures are never guessed. No cavity
view factors, shadowing, fluid motion, fan curve, boiling or calibration is solved.
Convection and radiation can share a face, as can surface heating and exchange.
Overlapping duplicate conditions of the same kind refuse, including distinct
selectors resolving to the same actual mesh faces.

`internal_heat_w` is total uniform generation within the one connected solid,
with an accompanying `internal_heat_source`. `null` disables it; negative power
represents an explicitly supplied cooling term. Positive Duffy/Gauss4 volume
quadrature integrates the C3D10 shape functions times the cubic geometry Jacobian.
Consistent nodal heat loads preserve total power and first moments, including
legitimate negative corner weights. No equal nodal split is used.

The deck uses actual exterior-element face labels for `*FILM` and `*RADIATE`.
Film coefficients convert by `1e-3` into N/(s mm K); the Stefan-Boltzmann value
5.670374419e-8 W/(m² K⁴) converts to 5.670374419e-11 in the deck's units.
`*PHYSICAL CONSTANTS` sets Celsius absolute zero to -273.15. Radiation uses
adaptive nonlinear steady load increments; a derived initial-temperature guess
does not replace any environmental input. The final temperature frame must be
at completed load-step time 1, with every prescribed temperature satisfied.

Independent Gauss6/Gauss8 physical-surface integration evaluates signed heat to
the surroundings from the actual temperature field. A boundary can supply heat
when it is warmer than the solid. Integration sensitivity must be below 1e-4
relative to absolute boundary exchange. Global heat balance is
`applied power + external thermostat power - heat to surroundings = 0`.
At prescribed-temperature nodes CalculiX RFL is conductive nodal flow; external
thermostat power additionally subtracts applied nodal sources and adds surface
exchange there. Raw flows and both corrections remain in `heat-balance.json`.
Balance must be within 1% and the exchange integration check must pass.

Thermal refinement compares fixed-probe, maximum and minimum temperature rises
relative to the supplied temperature/environment reference. The changes are
scaled by the actual thermal variation (with a 1e-6 K numerical floor), never by
absolute Kelvin temperature. Small heating errors cannot hide behind a large
absolute temperature. Kelvin is used inside the fourth-power radiation law;
negative Celsius results are valid. Source, coefficient, environment, generation
and material edits invalidate the affected studies and their exports.
`max_temperature` requirements may use negative Celsius values down to absolute
zero. Other requirement limits remain positive. Pydantic and generated JSON
Schema encode the same conditional limit rule; independent Draft2020-12 checks
cover both sides of this boundary.

The study panel edits these inputs, records provenance, renders temperature and
reports input/rejected power. `ambient-thermal` is an explicit synthetic example;
it starts with assumptions unconfirmed. `tests/test_thermal_exchange.py` covers
analytical convection/radiation/combined/cold/internal/thermostat cases, curved
quadratic integration, reaction accounting, thermal expansion, overlap and
incomplete-step refusal. Three standalone scikit-fem comparisons independently
assemble Robin matrices, internal source vectors and nonlinear radiative
residuals/tangents. These are numerical checks on the supplied idealization;
they do not establish measured heat-transfer coefficients or electronics CFD.

The deck conventions follow the [CalculiX 2.23 manual](https://www.dhondt.de/ccx_2.23.htm.tar.bz2), including `*PLASTIC`, `*AMPLITUDE`, `*STATIC` and `*TIME POINTS`. Large deformation alone does not establish that a material model is valid at arbitrary large strain.

## Independent canonical load histories

A load can provide `history: [[time, multiplier], ...]`, overriding its case's
shared history. An empty load history inherits the shared curve. If any load has
an override and the case has no shared curve, every load must have an explicit
curve. All declared curves end at the same case time, contain at least two points,
start at zero time and have strictly increasing finite times. Their load `source`
covers both spatial values and schedule. Nonlinear static requires zero initial
multipliers; it does not infer prestress from a nonzero first value. With no
histories, its existing default zero-to-one ramp remains. Events require explicit
curves that cover their entire physical duration.

For example, these independent multipliers ramp normal preload, hold it through
a shear reversal, then return the applied shear to zero:

```json
{
  "normal": [[0, 0], [1, 1], [4, 1]],
  "shear": [[0, 0], [1, 0], [2, 1], [3, -1], [4, 0]]
}
```

These are illustrative curve values, not material/force recommendations. Each
curve goes on its corresponding sourced load record. Nonlinear-static time is a
rate-independent load parameter; event time is physical seconds. No physical rate
or rate-sensitive material law is inferred from a static curve.

The static adapter uses the union of all knots and ramps each complete equivalent
nodal force vector between endpoints. It retains explicit zero entries to clear
previous loads. Separate spatial bases preserve opposing loads whose unscaled
sum vanishes. Reaction subtraction, per-increment/body equilibrium and external
work use the actual time-resolved force vector. Independent forces also drive the
existing physical-inertia event adapter. Unsupported study families refuse these
histories rather than applying only their last value.

`load-path.json` records each source, resolved curve/origin, spatial nodal-force
hash, resultant and moment, plus time convention and force scale. Increment and
work histories expose per-load multipliers. Solver-printed times have fewer digits
than the deck; an unambiguous nearby declared knot is used only for evaluating
loads, with raw output/evaluation times retained separately. No extrapolation is
allowed and unresolved phase-time ambiguity refuses. Linear elastic problems
submitted through the nonlinear entry point may have no nonlinear convergence
log; only when contact, geometric nonlinearity and plasticity are all absent does
complete per-increment free-DOF RF/CLOAD equilibrium provide the residual instead.
Missing nonlinear convergence evidence still refuses a genuinely nonlinear model.

Independent tests include delayed opposite forces with cancelling reference sum,
full unloading, fractional printed endpoints, a free-flight triangular acceleration
pulse and a constant-normal-load guided friction cycle. In the latter reference,
bulk shear stiffness40000N/mm, tangential penalty400000N/mm and declared mu*N30N
predict0.17625Nmm dissipation and a residual -0.00075mm shear displacement after
100→-100→0N applied shear. Load-step refinement checks the full work trajectory and
external/internal/spring/dissipation balance. These synthetic references do not
calibrate friction or establish general contact stability. All surface-contact
results remain inconclusive pending tangent-stability qualification.

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
nonlinear/contact and other study families are not independently
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
- [x] Single-solid internal generation, prescribed convection/radiation, consistent
  heat accounting, rise-scaled convergence and independent thermal comparisons.
- [ ] Thermal assembly conductance, cavity/occlusion radiation, variable material
  and boundary laws, airflow/CHT and measured heat-transfer calibration.
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
