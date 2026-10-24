---
name: oxygen-cad-dfm
description: Review Oxygen CAD parts for CNC, sheet-metal or injection-molding constraints using actual geometry, sourced process rules and explicit measurement limits.
---

# Process manufacturing review

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Choose the actual fabrication process and material before judging geometry. Read only its section in [process review](references/process-review.md). Fetch the selected supplier's current specifications; generic recommendations do not establish a shop's capabilities.

Use the CAD environment for exact STEP facts:

```sh
python scripts/part_facts.py /absolute/part.step
```

The output contains solid validity/count, dimensions, volume/area, face classes and circular-edge radii. A circular edge is not automatically a hole or a tool-accessible fillet. Tool access, minimum walls, bend allowance and tolerance capability are unmeasured.

For molding screening on an explicitly mm-scaled mesh:

```sh
python scripts/mold_review.py /absolute/part.stl --pull z --min-draft-deg 2
```

The draft threshold is supplied design/tooling input, not a default limit. This helper uses the bundled oxygen-cad-dfam ray helper, so keep the full bundle installed. It samples side-face slopes and pull occlusion candidates. The projected bounding rectangle is an envelope, not projected silhouette area. It does not establish undercuts, mold split, clamp force, resin shrinkage or fill behavior. Report those limitations with any tooling recommendation.

Findings should identify artifact/revision, feature, measurement method/value, applicable source/rule and suggested action. Use pass/fail only for a measured, applicable comparison; qualitative access/bend concerns remain review items. If no valid measurement exists, say unverified. A completed checklist is not complete process simulation.

For requested changes, keep user requirements/locks and apply a separate full proposal through Oxygen's revision-checked CLI, then regenerate and repeat the affected measurements. A review alone does not authorize uploading, ordering or changing geometry.
