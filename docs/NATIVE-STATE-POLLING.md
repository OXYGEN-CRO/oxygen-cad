# Native assembly state polling

A canonical document can have a small editable source and a much larger derived
build: tessellation, per-face descriptors and independent geometry evidence.
`GET /api/state` uses a separate build-metadata cache for canonical projects
whose viewport is supplied by `GET /api/native/scene`. This metadata contains
the original geometry key/revision, measurements, artifact paths and geometry
kind counts, without any face/edge descriptors or mesh arrays. The actual
native scene and complete source remain available, unchanged.

Build publication writes `build-state.json` and `last-build-state.json` after
their complete geometry files. Each sidecar records the full file's size,
mtime, ctime and inode; a changed or damaged source/sidecar cannot reuse earlier
metadata. Older builds migrate lazily once. For a large existing build this
first read can use substantial memory; an offline call to
`o3d.build_summary.load_build_summary(path)` can prewarm it before opening the
studio. Repeated polls then read only the small metadata. These are derived
caches, not a security boundary against filesystem owners.

When the current and previous builds have the same geometry key, the compact
state omits redundant `last_build`. After a real geometry edit, `build` becomes
null and the old summary remains `last_build`, with `geometry_state: stale`.
Changing a source revision alone does not fabricate a new geometry result.

Legacy projects keep their existing full geometry payload. Canonical projects
with legacy STEP-reference overlays or saved legacy fixed-face study markers
also retain full payloads, preserving those consumers until their geometry is
explicitly served by the native scene path. This first fix does not reduce
those fallback payloads or the necessary editable source size.

The default Python `Store.state()` and CLI consumers retain complete build
results. Native scene and full build/artifact APIs are not replaced with
simplified geometry. Native-scene and state HTTP handlers use `JSONResponse`
for their already JSON-native data, avoiding FastAPI's additional recursive
conversion of every triangle or native document value.

Actual large-model observations belong in the trial evidence rather than
software guarantees. Polling improvements alone do not establish that a
browser can render arbitrarily many independently drawn faces; viewport draw
batching has its own acceptance gate.
