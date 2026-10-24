# Native Oxygen catalog

Read the current checkout's `docs/CATALOG.md` using the checkout path in runtime.json, or the plugin root. `python -m o3d.catalog --help` is the current CLI contract. This optional service uses PostgreSQL/pgvector and can be run independently of the CAD runtime.

Search/status/get are read-only. Query actual part identity, sourced numeric specs and CAD availability; semantic similarity cannot replace a missing torque/interface requirement. The native search supports `--filters` with its typed Search JSON for exact/numeric/facet constraints. `--downloadable` distinguishes permitted assets from merely discoverable references; it does not establish manufacturing fit or stock.

Use `get PART_UUID` to inspect variants/assets before selecting an ASSET_UUID. `fetch-asset ASSET_UUID /absolute/destination` writes the licensed file plus attribution/source sidecars. `import-part ASSET_UUID /absolute/project N` imports through Store's existing revision check and writes catalog-link provenance; N is positional in the inspected release. Use fresh inspect state, reconcile conflicts and rebuild afterward. A future CLI can change: follow installed help rather than adding a guessed `--expected-revision` option.

Keep the full CAD runtime for import; the lightweight catalog service is for discovery/storage. Preserve sidecars and original sources in any editable handoff. Source scope, physical dimensions, stock and live prices retain their actual verified/unknown status. Do not reclassify a catalog hit as a tested assembly interface.

The direct public-API helper is an alternative explicit acquisition path, not a way around catalog download refusals. No embeddings/ingestion/startup side effects are implied by an ordinary search request.
