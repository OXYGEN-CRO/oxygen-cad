---
name: oxygen-cad-drawing
description: Generate a nominal overall-dimension orthographic PDF from an Oxygen CAD STEP export. Use for a basic dimensioned part sheet; advanced drawings need a separate tool.
---

# Drawing

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Use the bundled helper for front/top/right orthographic views with kernel-derived hidden edges and measured overall dimensions. It needs build123d and ReportLab: in the CAD environment install the checkout with the optional `.[skills]` extra, or install this folder's `requirements.txt`. Keep CAD and mesher environments separate.

```sh
python scripts/drawing.py /absolute/export/part.step --out /absolute/output/part-r7.pdf --title "Sensor bracket" --revision 7
```

Use the revision in the actual export report, not the current studio's unrelated revision. The helper places three views on A3, records source SHA256 and a nominal scale, refuses an existing output and writes only a complete PDF. `--note` adds short supplied notes; it does not change geometry or create tolerances.

This is a basic part sheet: overall nominal dimensions only. There are no automatic hole callouts, sections/details, datum/GD&T annotations or inferred tolerances. For a shop drawing needing those, use an installed drawing tool and its measured geometry rather than inventing dimensions. Get tolerances, fits, material and finishes from actual design requirements. Keep original geometry and the source report.

Open/render the PDF and inspect view orientation, hidden edges, text overlap, dimension extents, units, scale, revision and omissions before delivery. A generated file without visual inspection is not a reviewed drawing. Review the notes for unsupported requests and describe any required manual drawing work.

Library reference: https://build123d.readthedocs.io/en/latest/import_export.html. PDF generation is a standalone helper, not part of `o3d export` or the studio drawing UI.
