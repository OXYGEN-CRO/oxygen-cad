# Revision-linked releases

`o3d export` packages the saved project revision and available geometry. `report.json`
and `release-manifest.json` describe the geometry kinds and the bodies covered by
each aggregate artifact. A release does not turn an open surface or mesh into an
exact solid.

- Aggregate STEP/BREP files appear only when the current build provides them.
  Body-specific exact STEP files remain associated with their stable scene IDs.
- The aggregate STL covers the body IDs declared by the build. Each native body
  also retains its original triangle data in `geometry/body-*/mesh.json`; mesh
  bodies receive a separately readable STL without changing their native indices.
- `scene.json`, `build.json` and body descriptions in `report.json` use paths inside
  the release. Original source assets appear under `assets/`.
- `part.solidcraft`, when available, and `o3d.json` preserve native and host source.
  `project.oxygen.zip` contains the saved editable project, retained history, assets
  and build/study results. Restore the complete release through the project import
  flow; the outer manifest and nested project are checksum checked.
- The active current study retains its field, decks, geometry, region map and
  study package under `analysis/`; other current named studies use
  `analyses/<id>/`. Nested nonlinear increment paths resolve inside the ZIP.
  `report.json` records every study's state; stale studies have no numerical
  package in the release.

Whole-project solid checks run only when every scene body is an exact solid.
Surface, mesh and mixed releases retain independent build diagnostics and list
unevaluated requirements. Their aggregate volume and mass remain `null`;
`geometry.solid_volume_mm3`, when present, is only the independently reread
exact-solid subtotal. Bounds and dimensions include every scene body, including
meshes. Open surfaces have area, without an invented enclosed volume.

The preview rasterizes visible native scene triangles, including surfaces and
meshes. It is a display artifact, not dimensional or manufacturing verification.

Release construction uses a private staging directory and fixed ZIP metadata.
Unchanged inputs produce identical ZIP bytes. Publication checks that the saved
project still matches the requested snapshot and atomically replaces the ZIP;
a concurrent edit refuses publication and retains the previous complete release.
