# Engineering brief and requirement traceability

`Project.engineering_brief` is optional. Older project documents continue to load
with `null`. Brief schema version 1 records a task, explicit explanatory inputs,
and links to existing requirements, parameters and studies. It does not create
another requirements model or configure loads, materials or a solver.

`examples/engineering-brief.json` is a complete benchmark project with a brief.
The material and load are numerical test inputs copied from the cantilever
fixture. They are not production material specifications or inferred operating
loads. Unknown payload, budget and interface quantities remain empty.

Each input has an ID, category, description, optional finite quantity, unit and
provenance. Every numeric quantity requires units and provenance. An optional
parameter reference must exist, use the same unit and agree with its value;
there is no implicit unit conversion. Textual assumptions can have provenance
without a numeric quantity. Unknown linked quantities and unknown global
assumptions prevent a passing traceability result.

Each link names an existing requirement ID and optional parameter/input IDs.
Geometry requirements are global. A simulation link must select a study
explicitly or use the requirement's existing study scope. Missing, deleted,
ambiguous or mismatched references remain visible for repair. Brief input/link
IDs and reference lists must be unique; duplicated project requirement IDs are
reported as ambiguous instead of selecting an arbitrary requirement.

For canonical native documents, a retained host parameter is only usable when
its unique native target still exists and its value/unit agree. Explicit legacy
aliases, including renamed parameters, are checked against the native timeline.
Deleted targets, divergent mirrors and ambiguous aliases cannot validate or
rebind. Native-only parameters have no inferred host mirror. This bounded
adapter resolves explicit numeric/unit literals; general native expressions
remain unresolved until an explicit expression binding adapter is available.
Simulation links refer to the project's saved default/named studies, not the
separate Simulate workspace's study/case requirement records.

## Native evidence and explicit binding

Run the existing native geometry check or simulation first. Binding records
identities of those actual results, never a user-entered pass flag or copied
measurement. Save the bound brief through the ordinary revision-checked Store
command. The save itself does not invalidate evidence: changing only the brief
does not change geometry or simulation keys.

The identity includes the complete native evidence hash, geometry signature,
material signature, source requirement signature, selected study signature and
brief input signature. The last includes task, all explanatory inputs, linked
parameter metadata and the link's intent/references. Renaming the brief title
alone preserves the binding. Changed inputs, geometry, material, limits, study
settings or result identities make the existing binding stale. A new native
run requires review and rebinding even when it uses the same engineering inputs.

Evaluation consumes native requirement findings, including measured value,
current limit, geometry/check keys and study identity. A passing simulation
also requires convergence and a passing native result. Missing, disabled,
unmeasured, nonfinite, failed or inconclusive evidence cannot certify a pass.
Measured failures remain failures. Other links within an overall failed native
result remain unknown. Bind skips unknown, disabled and stale evidence, and
refuses broken references. No generated receipt is evidence of physical safety.

Canonical mass checks use a fresh native measurement for every exact solid body,
the body's explicit material assignment, and that native material's density.
Each body's volume is checked against its independent exact STEP solid before
the body masses are summed. The check records body/definition identities, material,
density, volume, mass, native document identity and native measurement identity.
The selected density comes from the pinned native material library or the saved
native user-material record; it is not a material certificate or calibration.

Every canonical body needs an explicit usable material. The kernel's unassigned
`Default` material has a display density, which cannot serve as engineering input.
Missing assignments or incomplete/invalid body measurements produce unknown mass
and a `needs_input` mass requirement check. Canonical checks never substitute
the unrelated legacy `Project.material` density, including for mixed-material
assemblies. Pre-fix canonical mass check receipts without this per-body evidence
basis cannot bind or pass; rerun checks. Legacy feature documents continue to use
their explicit project material density.

Binding is an explicit association between intent and measured requirements.
Only explicit parameter bindings check numerical input agreement. Descriptive
load, payload or interface text is not automatically translated or checked
against solver forces or geometry; the person binding must review that match.
Unlinked unknown inputs remain visible but do not invalidate independent links.
A passing report means all configured links have current evidence, not that a
complete hardware specification, physical validation or supplier approval exists.

## Python contract

```python
from pathlib import Path
from o3d.brief import load_brief, validate_brief, evaluate, bind_evidence
from o3d.model import Command

brief = load_brief(Path("brief.json"))  # Standalone brief, not a project document.
validation = validate_brief(store.load(), brief)
report = evaluate(store, brief)          # Optional draft; read-only.

project = store.load()
expected_revision = project.revision
project.engineering_brief = bind_evidence(store, brief)
store.apply(Command(expected_revision=expected_revision, actor="human",
                    project=project, summary="Bind engineering brief evidence"))
report = evaluate(store)
```

`load_brief` accepts a model, dict, JSON text or explicit `Path`; schema errors
raise Pydantic `ValidationError`. `validate_brief` returns `{valid, issues}`,
where issues include `code`, `message`, `link_id` and `input_id`.
`bind_evidence` returns a validated `EngineeringBrief` without writing it.

`evaluate` (alias `evaluate_brief`) returns schema version, project revision,
aggregate `state`, validation and `links`. Each row includes ID, requirement ID,
study ID, `status`, reason, measured value/unit and evidence identity. Row status
is `passed`, `failed`, `unknown` or `stale`. Aggregate state prioritizes failed,
then stale, then unknown; a missing brief is `not_configured`. No links is unknown.

## Controlled React editor

`BriefPanel` accepts `project`, `data: EngineeringBrief | null`,
`result?: BriefResult | null`, `onChange`, optional `onEvaluate`/`onBind`, and
`disabled`. It edits task, inputs, units, provenance and requirement references,
and displays per-link evidence status and validation issues. The host owns
saving, revision conflicts, requests and pending state. The component has no
backend URLs or independent persistence path. Evaluate/bind results describe
the saved project revision; hosts must prevent binding unsaved drafts or
explicitly submit and validate the draft before binding.
