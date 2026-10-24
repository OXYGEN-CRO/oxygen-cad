# Drawing workspace

Drawing intent lives in `Project.domains.drawings`, schema version 1. Documents
contain reusable sheet templates, units, first/third-angle projection, a title
block, revision history, views, attached annotations and production tables.
Positions, geometry and tolerances use millimetres internally; inch display is a
conversion, never a geometry rescale. Each publication retains a source revision,
the complete source geometry key, the issuing revision and per-file hashes.

`drawings.validate()` is lightweight. Geometry jobs import `drawing_worker` only
when called. The independent OCCT worker reads the current exact BRep, applies
hidden-line removal, cuts true halfspaces for sections, hatches section loops
with an even/odd rule that leaves cavities empty, and clips detail linework to a
circle. Curves are discretized with a maximum requested model deflection of
0.02 mm. This is vector drawing geometry; no raster screenshot is exported.
Native bodies are resolved by explicit per-body STEP artifact mappings, never
by a traversal index. Mesh-only sources are refused.

Aligned projected views currently use a front parent with top/bottom/right/left
or isometric targets. Section views are normal to their cutting plane, whose
edge must appear in the parent view; the camera-facing half is removed. This
keeps the cut surface visible. Principal-plane sections are supported; oblique,
offset and broken sections need further work.

## Supported operations

| Operation | Input | Result |
|---|---|---|
| `inspect` | optional `document` ID | current/stale/unresolved associations, repair details and BOM rows |
| `refresh` | optional `document` ID | proposed `data`; only documents with resolved references receive the current source key |
| `repair` | document/sheet/annotation IDs, reference index and explicit replacement reference | validated proposed `data`; no nearest-face fallback |
| `references` | no additional input | named native face/edge attachments scoped to stable body IDs |
| `template` | template ID, new document ID/title | new document with current source identity |
| `preview` | optional document ID | SVG/DXF sheets and resolved measurement metadata |
| `publish` | optional document ID | multi-sheet embedded-font PDF, SVG/DXF sheets and ZIP with reopening intent/manifest |
| `reopen` | extracted `document.json` as `data` | validated drawing-domain data |
| `import_pmi` | immutable STEP reference ID | source-hashed AP242 entity/dependency inventory and explicit omissions |
| `attach_imported_pmi` | source reference/entity IDs and explicitly authored annotation/target | proposed source-linked PMI; records that mapping was supplied explicitly |
| `inspect_pmi`, `export_mbd` | no additional input | attached 3D annotations, annotation planes and supported size inspection limits; JSON artifact for export |
| `refresh_pmi` | no additional input | proposed annotation sources updated only where references resolve |
| `import_mbd` | exported annotation JSON as `data` | proposed annotation data; rejects incompatible source geometry or duplicate IDs |

Proposals are returned without mutating the project; the graphical client saves
them through the shared revision-checked domain transaction. Other callers must
do the same. Canonical native documents are authoritative. Frozen legacy hole
and parameter fields are therefore refused as drawing references after native
migration. The user must explicitly select a current exact face/edge. Missing
or ambiguous references, stale source keys, incorrect historical source claims,
missing BOM keys and clipped output fail publication. Existing issued files are
reused only after their hashes pass.

Named native attachments query the actual source face/edge identity, then require
a unique independent exact match containing its geometry point. Analytic plane
and cylinder support is checked explicitly, including cylinder radius and axis.
STEP may merge co-surface native patches; a unique validated analytic support is
accepted without relying on a traversal index. Other geometry also requires a
bounded area/length match. The native acceptance fixture changes a cylinder from
10 to 16 mm diameter through its timeline and verifies the updated drawing value.
Missing, ambiguous and unsupported topology still requires explicit repair.

## Dimensions and symbols

Supported attached annotations include projected linear/aligned/angular,
radius/diameter, center marks/lines, explicit hole/thread notes, datums, a bounded
feature-control-frame vocabulary, surface texture marks, fillet/square/V weld
marks, edge notes and BOM balloons. Numeric results come from resolved exact
geometry or an explicit reference. Symmetric, deviation, limit and ISO H7/g6 fit
display are available; the implemented fit table covers nominal sizes greater
than 3 through 30 mm. H7/g6 reports separate hole and shaft limits. Thread
designations are explicit inputs; the application does not invent modeled thread
geometry. General notes may use explicit authored positions.

The supported GD&T vocabulary validates basic frame fields, datum existence,
form-tolerance datum restrictions and bounded material-condition usage. It does
not constitute a complete ISO GPS/ASME standards checker. A supplier fixture
still needs engineering review. Revision and hole tables recompute their values;
native parts lists use `assemblies.bom_snapshot` definition keys so row ordering
does not become balloon identity. Visibility is distinct from BOM suppression.

## Output and dependencies

SVG embeds an actual Unicode font. PDF embeds a Unicode TrueType subset, a CID
glyph mapping and a ToUnicode map so dimensions and symbols remain searchable.
DXF R2010 exports editable UTF-8 TEXT and vector LINE/CIRCLE entities, paper-space
millimetre coordinates, line weights and hidden/center linetypes. DXF does not
claim proprietary associative dimension/GD&T objects. DWG is unsupported.
DXF readers need a suitable installed Unicode font for symbol appearance; PDF
and SVG carry their embedded font. Tables reject overflowing cells and accept
explicit `column_widths_mm` so text cannot silently overlap a neighbouring cell.

The worker uses the existing build123d/OCCT geometry environment plus fonttools.
Set `O3D_DRAWING_FONT` to a packaged embeddable Unicode TTF containing the requested
symbols. Linux discovery prefers installed FreeSerif, then DejaVu Sans; missing
glyphs or fonts fail explicitly. FreeSerif's font exception permits document
embedding; platform packaging must include its original licence and font notice.
No font or third-party source is copied into this module.

## Evidence and limits

`tests/test_drawings.py` checks measured hole pitch/diameter/fit limits, section
volume and unfilled cavities, parent alignment/detail scale, explicit repair,
source invalidation, quoted STEP records, repeat-publication integrity, templates,
inch conversion and annotation-plane/inspection data. Independent ezdxf opens
the files and measures a 60 mm line. The optional pypdf test verifies paper size,
searchable diameter/GD&T text, embedded fonts and a measured vector edge. Install
`tests/requirements-drawings.txt` to execute that reader check.

`frontend/tests/drawings.spec.ts` exercises graphical authoring, preservation of
an unsaved edit during polling, controlled publication and saved-document reopen
against a disposable real application server. `examples/drawing_plate.py` creates
an inspectable ISO two-sheet and ASME inch fixture in a new output directory.
`tests/test_drawings_native.py` additionally exercises native timeline dimension
updates, rejection of a frozen legacy nominal after scale, and repeated-component
BOM/balloon reconciliation through the actual native engine and exact artifacts.

AP242 import currently inventories and preserves PMI source entities; it does
not automatically decode every tolerance or map its topology. Authored 3D PMI
round-trips through the declared Oxygen JSON format and supported size inspection
limits. A complete native 3D PMI viewport, AP242 semantic/graphical PMI export,
DWG output, general topology-changing reassociation, full standards checking,
and an independent supplier/Fusion entitlement review remain open work. These
limits are also recorded per audit row in `drawings.json`.
