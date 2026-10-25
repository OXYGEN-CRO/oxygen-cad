# Engineering workflow increment

Open **Project tools → Engineering workflow** in the studio. Brief, Interfaces,
Purchasing and Manufacturing share the same CLI/API service contracts. Existing
CAD editing and studies continue through their original paths.

## Engineering brief

Create a brief, enter explicit sourced inputs, and link existing requirements,
parameters and saved studies. Save using the normal revision-protected command.
Evaluate the links, then bind current available check evidence. Missing, disabled,
changed or inconclusive evidence remains unknown or stale. Binding captures
traceability; it does not validate a descriptive load against physical reality.
Project default/named-study evidence is supported. Separate Simulation workspace
study/case records require their own adapter and are not inferred from success badges.
Canonical material masses use native body materials; unknown assignments cannot
pass a mass requirement. Canonical parameter refs must resolve live native targets;
unresolved native-only expressions remain explicit.
An edit removes old displayed results until reevaluation.

```sh
./o3d brief save brief.json --expected-revision 1 --json
./o3d brief inspect --json
./o3d brief bind --expected-revision 2 --json
```

Use the actual inspected revision. `examples/engineering-brief.json` is a complete
analytical project, not a certified production design. The studio's example loader
loads only its brief while retaining existing joint requirements and component
contracts; links absent from the current project remain unresolved.

## Interfaces and adapters

Supply exact component identities, source hashes, coordinate frames, bolt patterns
and tolerances. Evaluate scoped mechanical registration, or generate an editable
canonical native adapter proposal for the supported planar patterns. Overall physical/electrical
compatibility remains unknown. Generation does not replace the current project.
Download the proposal and initialize a separate project or explicitly review/apply
it through the normal revision path. The example uses synthetic dimensions.

```sh
./o3d compatibility evaluate compare.json --json
./o3d compatibility adapter examples/component-interfaces.json --output adapter.json --json
```

## Purchasing

Evaluate purchased/custom/unknown BOM lines with explicit supplied offer snapshots.
Prices are decimal strings; incomplete lines stay outside complete totals. Currency,
MOQ, region, stock and expiry remain separate checks. Alternative eligibility needs
complete explicit hard constraints. Offers are transient inputs, not persisted
catalog metadata or live provider checks. The example uses an explicit historical
snapshot clock; its report identifies that replay rather than current availability.

```sh
./o3d procurement evaluate examples/procurement-bom.json --json
```

## Supplier handoff

Attach the source text for selected supplier rules, then supply the profile and job
request. Supplied evidence is not automatically vendor verified. Preflight reads
actual current geometry and an existing revision-bound handoff. It checks only
explicit sourced constraints; missing or expired evidence remains unresolved.
Prepare a manual request package, inspect its findings, and obtain supplier review.
The derivative handoff supports one exact solid and preserves its full canonical
release dependencies. Multibody, surface and mesh workflows are refused with an
actionable canonical-release path; they are not flattened into a supplier part.
No upload, quote fetch, order, payment or printer control occurs.

```sh
./o3d supplier source supplier-rules.txt --json
./o3d supplier evaluate request.json --json
./o3d supplier package request.json --expected-revision 3 --json
./o3d supplier status status-input.json --json
```

`request.json` contains `profile` and `request`. Use the capture path/hash returned
by `supplier source`; retain the actual source URL and capture/expiry data. The
profile's source does not become a supplier certification. The request references
an existing `o3d handoff create` manifest and selected files inside it. Read
[SUPPLIER-HANDOFF.md](SUPPLIER-HANDOFF.md) for the schema and response-recording
contract. Manual packages and recorded responses are invalidated by changes to
project, request, profile or artifact identities.

## Boundaries

This is a bounded engineering increment. It does not establish complete robot
requirements, full assembly compatibility, live shopping/quoting, comprehensive
DFM, physical fit/strength or broad Fusion parity. Source examples are illustrations,
not new supplier facts. The studio keeps procurement drafts in memory; reload
clears them. Only explicit brief saving/binding mutates the authoritative project.
