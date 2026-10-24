# Oxygen CAD — contributor and CAD-agent instructions

Oxygen CAD is an open-source, local CAD IDE. It shares one typed engineering document between a browser studio and a JSON CLI.

## When designing a part

- The default live project is `projects/demo/o3d.json`; project-specific instructions and schema are generated beside it.
- Run `./o3d inspect --json` and `./o3d context --json` before editing.
- Write the complete proposed document to `projects/demo/proposal.json`. Apply it using `./o3d apply proposal.json --expected-revision N --actor agent --summary '...'`.
- The launcher resolves relative input files in the selected project directory. `O3D_PROJECT_NAME` and `O3D_PROJECTS_DIR` select another project.
- Never overwrite the live project file. Respect locks and revision conflicts; re-inspect and reconcile changes.
- Rebuild, check numeric measurements, render, and inspect the preview. A plausible image does not establish dimensional correctness.
- Millimeters are the native length unit. Parameter references use `$name`.
- Never invent material properties, forces, support conditions, or passing results. Missing physics inputs remain missing.
- Repair lost/ambiguous references explicitly. Do not replace them with arbitrary face indices.

## When changing the application

- Python source is under `src/o3d/`; React/Three.js source is under `frontend/src/`.
- `model.py` defines the public schema. Run `scripts/generate_types.py` after changes.
- All UI and CLI edits use the same Store command path. Preserve revision checks, parameter locks, immutable snapshots, and result invalidation.
- CAD and Netgen must remain in separate processes/environments; their OCCT versions differ.
- Run backend tests and the frontend build. For UI changes, run Playwright against a disposable project.
- Tests must verify measured behavior: geometry, preserved requirements, exported files, equilibrium, convergence, and real browser outcomes.
- Keep source code separate from user projects. Never commit `.o3d/`, imported private STEP assets, credentials, or generated user geometry.
- Be explicit about validation limits. Linux tests do not prove macOS compatibility or physical fit.

## Commands

```sh
docker build -t oxygen-cad .
docker run --rm oxygen-cad pytest -q
cd frontend && npm ci && npm run build
```

Native development uses `.venv/bin/python`, `.mesh-venv/bin/python`, and `O3D_NATIVE=1`. See README for setup and browser tests.

## Native geometry and desktop

- The default geometry engine is the in-repository Rust core, built with `cargo build --release --locked --manifest-path engine/Cargo.toml` (Rust 1.95). Source provenance and imported-file hashes are in `third_party/solidcraft/UPSTREAM.json`; keep upstream namespaces and licence notices.
- Update the native toolchain marker in `geometry_key` when changing geometry semantics or pinned kernel dependencies. Preserve explicit errors; never silently fall back to OCCT.
- Native project features describe modelling commands only. Preserve the command allowlist, filesystem restrictions and recursive user-owned parameter bindings.
- `desktop/` is a Tauri launcher for the same React UI and local Docker runtime. `scripts/stage_desktop.py` is the only resource staging path; never bundle user projects, virtual environments, credentials or build caches.
- Use `O3D_TEST_PORT` for an isolated browser fixture; never point acceptance tests at a user's project. Test an actual native-command edit as well as the original editing and study flows.
