---
name: oxygen-cad-dfam
description: Measure an Oxygen CAD mesh for an additive-manufacturing review, including sampled normal chords, overhang areas and six build orientations.
---

# Additive manufacturing review

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Use the CAD environment's numpy/trimesh. Work on the exact STL exported from the selected revision; STEP is not a triangle mesh. Confirm the mesh is in millimetres before supplying `--units mm`. STL carries no reliable unit declaration; no silent rescaling.

```sh
python scripts/mesh_review.py /absolute/part.stl --units mm --overhang-deg 45 --samples 128
```

The example angle is a measurement setting, not a process recommendation. Select it from the intended process/material/machine rules. Read [process review](references/process-review.md) when choosing rules. The helper measures topology/dimensions and samples inward-normal chords using deterministic local ray intersections without an rtree dependency. It ranks six axis-aligned build directions by downward over-limit area and reports build height. Faces at the build plate are excluded from suspended overhang area.

Normal chords are a screening proxy, not a proven minimum wall thickness everywhere. A missing/missed sample remains missing, not zero. An open/inconsistently oriented mesh has no valid thickness/volume claim. Limits on face count and sample count bound the helper's runtime. It does not compute support volume, powder escape, shrinkage or physical strength.

Compare trustworthy measured facts with the selected process's current supplier/material guidance. Name the actual JSON field, units, applicable source and threshold for each finding. Mark unmeasured questions unverified. Powder processes need powder-escape review rather than blanket support-angle rules. A suggested orientation trades support risk against build height and anisotropy; remeasure an actual transformed artifact before claiming a redesign works.

Return actionable changes with their evidence and location where available. Modify the original project through the revision service only when redesign is requested, then export and remeasure. Printability screening does not certify a print or start a printer.
