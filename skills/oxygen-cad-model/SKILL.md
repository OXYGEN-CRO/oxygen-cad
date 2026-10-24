---
name: oxygen-cad-model
description: Create or edit Oxygen CAD project geometry through its JSON CLI, including native engine features, STEP references and studio selection context. Use for changes to an Oxygen CAD design.
---

# Edit an Oxygen CAD design

Locate the application checkout: the plugin root is two directories above this skill folder, or a standalone skill's `runtime.json` records `repository`. Verify `o3d` and the `oxygen-cad` pyproject. Run `./o3d` from that checkout. Keep `O3D_PROJECTS_DIR` and `O3D_PROJECT_NAME` matched to the user's studio; with native execution set the absolute `O3D_PROJECT_DIR` explicitly.

## Read the shared document

```sh
./o3d inspect --json
./o3d context --json
```

`inspect.project` is the complete editable document, with `revision`, parameters, features, references, requirements, material and study inputs. Other top-level fields are derived evidence; do not put them in a proposal. Read the selected project's `AGENTS.md` and `project.schema.json`. Inspect the existing features and selected face/point in context before interpreting a request such as “make this thicker.” Selection and previous build data can be stale.

Use millimeters for length, N for forces and MPa for stress. `$name` refers to a named parameter, not an arbitrary expression. Keep feature IDs and user-owned requirements intact. Locked parameters include their references inside nested native command arguments; replacing `$name` with a literal is not a valid workaround. Do not switch actor to `human` to bypass agent restrictions.

## Propose and apply

Write a complete proposed **project document** to a separate file in the selected project directory, for example `proposal.json`. Change only the requested design inputs and preserve unrelated edits, references, materials and study assumptions.

```sh
./o3d apply proposal.json --expected-revision N --actor agent --summary "Describe the actual design change" --json
./o3d build --json
./o3d check --json
./o3d render --json
./o3d inspect --json
```

Replace `N` with the revision just inspected. Never overwrite live `o3d.json`. On a conflict, re-inspect, reconcile the requested change against the latest document and create a new proposal. Do not keep submitting the stale file. A lock or user-owned requirement refusal needs a design that respects it or an explicit user decision.

In Docker mode, relative proposal/import paths resolve inside the selected project directory; other host files are not automatically mounted. An absolute host path is not a container path. In native mode, relative input files resolve from the checkout working directory, so use absolute paths. Derived artifacts returned by the CLI resolve under the selected host project's `.o3d/` directory.

## Choose supported geometry

Use typed profiles, extrusions, holes/arrays, fillets/chamfers and placement for the bounded common operations. For additional Rust modelling operations discover the installed interface:

```sh
./o3d engine commands --json
./o3d engine describe solid.shell
```

Only `project_commands` from the discovery result may be saved in a `type: "native"` feature. Read the project schema and command description before choosing argument fields. `engine stdio` is a raw developer session; it is not a persistent project edit path. Keep all design changes in the project and normal apply/build route. Never silently change the chosen geometry engine after an error.

Import a supplied STEP file using `import-step FILE --expected-revision N --json` after placing it in the mounted project directory. Imports are content-addressed reference geometry with a 50 MiB limit; a reference is not an editable native feature. Missing or ambiguous face references require explicit repair, not a guessed face index.

## Review the result

Read actual numeric dimensions, validity, check state and revision from the returned evidence. Open the PNG at the returned render artifact path when image inspection is available. The CLI render currently omits imported references; review those in the studio. If geometry or a check fails, report it and repair the actual cause. A plausible preview or a successful build does not establish fit, manufacturing readiness or structural strength. State what changed, the measured outcome and any untested assumptions.
