---
name: oxygen-cad-dxf
description: Create a planar DXF cross-section from Oxygen STEP, or inspect DXF units, contour endpoints, duplicate entities and layers before fabrication.
---

# Planar DXF profiles

## Runtime and input files

These are workflows over Oxygen exports or external tools. They do not add native studio commands. Use the selected project's current export; inspect its revision/report first. Commands below run from this skill directory with an interpreter containing the stated dependencies. Quote absolute paths. A standalone install carries `runtime.json` pointing to the application checkout; plugin skills live below that checkout's `skills/`. Never run a helper on an old export and label it current.

Use the CAD Python environment (build123d and ezdxf). Select the actual cutting plane from design intent. A cross-section is not a projected silhouette or a sheet-metal flat pattern.

```sh
python scripts/profiles.py section /absolute/part.step --z 2 --out /absolute/panel.dxf
python scripts/profiles.py inspect /absolute/panel.dxf
```

The section helper intersects the solid with the supplied horizontal plane, relocates its geometry to XY and writes a millimetre CUT layer, preserving exact circles/arcs where supported by the exporter. It refuses an empty intersection or an existing output. The inspection helper reports actual `$INSUNITS`, entity types, declared-unit bounds, simple duplicate/degenerate entities and non-degree-two endpoints. It reports unsupported entities instead of certifying them.

Before treating a finding as a cut error, identify which layers are cuts, bends and markings. Open engravings and bend lines have different intent from closed cut loops. Confirm drawing scale and planes; the helper does not rescale an inch/unitless file. Endpoint coordinates and dimensions stay in declared units; interpret them using `insunits`. Review curvature and verify required feature dimensions with ezdxf or exact source topology.

The helper does not fully check partial overlaps, self-intersections, minimum webs/holes, nesting, kerf, bend allowance or supplier limits. Never describe a section through a bent bracket as its unfolded manufacturing blank. For sheet unfolding/compensation use a verified external sheet-metal/CAM tool with actual material/tooling data.

Inspect the DXF in an available viewer, or render it with installed ezdxf tooling, and report checks performed and coverage gaps. This helper is separate from the standard release ZIP; deliver the new DXF with its source revision and selected section plane.
