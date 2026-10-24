# Engineering workspaces and parity status

Oxygen keeps its React studio and uses the in-repository Rust geometry engine.
The workspace selector opens contextual editors; drawings and electronics use the
main editing area. Human edits and agent commands share the project revision,
history, parameter locks and editable native document.

This is a broad implementation milestone. Full Fusion parity is still incomplete.
Each ledger below records implemented behavior, executed evidence and remaining
work. An exposed command or a passing fixture does not establish a complete
industrial workflow. Core and interoperability rows overlap modeling scope;
their row counts must not be added into a parity percentage.

| Workspace | Working scope | Evidence and remaining work |
|---|---|---|
| Model | Graphical sketches, constraints, profiles, solid operations, parameters, history and topology | [Modeling](modeling.json), [native core](native-core.json) |
| Assembly | Components, instances, joints, bounded contact/motion, linked sources and BOMs | [Assembly](assemblies.json) |
| Drawing | Associative views, sections, dimensions, annotations and SVG/PDF/DXF publication | [Drawings](drawings.json), [workflow](DRAWINGS.md) |
| Manufacture | Bounded milling/turning, stock/post checks, nesting, sheet development, inspection and real FFF slicing | [Manufacturing](manufacturing.json) |
| Simulate | Sourced studies, mesh preparation, structural/thermal/modal/buckling/nonlinear/event paths and bounded optimization | [Simulation](simulation.json), [numerical scope](SIMULATION.md) |
| Electronics | Schematic/board source, ERC/DRC, real SPICE and KiCad manufacturing exports | [Electronics](electronics.json), [workflow](electronics.md) |
| Render | Saved scenes, CPU rendering, presentation controls and storyboard publication | [Visualization](visualization.json), [workflow](VISUALIZATION.md) |
| Import and repair | Immutable editable exchange, construction geometry, surface and mesh preparation | [Interoperability](interop.json) |
| Project | Project windows, recovery, complete portable archives, optional credentials, review and offline drafts | [Platform](platform.json), [workflow](PLATFORM.md) |

The existing [named engineering studies](../SIMULATION.md) and separate
[parts catalog](../CATALOG.md) remain available. Existing version-1 projects keep
their compatibility workflow. Once migrated, the native document is authoritative;
retained legacy features are provenance, and edits go through native operations.

## Agent entry points

```sh
./o3d inspect --json
./o3d context --json
./o3d workspace list --json
./o3d native inspect --json
./o3d engine describe solid.box
```

`native run` and `workspace save/run` require the current expected revision.
Domain jobs run against captured inputs; changed proposals publish through the
same Store boundary. Stale reports remain stale when reopened. User-owned locks
also apply to agent migration and whole-project replacement.

Project archives retain source dependencies and registered evidence. Geometry
releases describe the actual solid/surface/mesh coverage and preserve per-body
exports; unavailable exact geometry and physical quantities are explicit.
See [release format](../RELEASES.md), [desktop setup](../DESKTOP.md) and
[reproducible verification](../PARITY-VALIDATION.md).

Remaining work includes complete Form tooling, advanced assembly relations,
simultaneous-axis CAM, broader EDA exchange/routing, full cooling CFD, advanced
rendering, distributed sync and physical Mac/workflow qualification. The domain
ledgers are the detailed backlog; this milestone does not remove those targets.
