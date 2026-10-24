# Oxygen CAD

**A lean, local CAD IDE for coding agents and the people working with them.**

An editable text project. A JSON CLI. An interactive browser studio. Your coding agent and your manual edits work on the same parametric model.

The workflow is familiar from code-driven creative tools such as Hyperframes and Remotion: author files, preview the result, inspect it, revise, and export. Here the output is exact CAD geometry, with measurements and engineering checks alongside the preview.

![Oxygen CAD studio](docs/studio.png)

The studio uses [Oxygen Glass](docs/UI.md): a dark interface with frosted panels, rounded controls and floating tools around the real CAD viewport. The same design extends to engineering studies, history and source editing, with an adaptive layout on smaller screens.

## Quickstart

Requires Git and Docker with Compose. Linux x86_64 is tested. Apple Silicon uses the same amd64 images through Docker Desktop emulation; physical Mac validation is pending. The first image build downloads the CAD and analysis dependencies.

```sh
git clone https://github.com/OXYGEN-CRO/oxygen-cad.git
cd oxygen-cad
./o3d studio
```

Open **http://localhost:8787**. A camera mounting bracket is created automatically. Click **Build model** if geometry has not yet been generated. Select a feature, edit its dimensions, save, and rebuild. The default view keeps the model and feature tree visible; selecting a feature or face opens its properties. **Verify** opens fit and stress tools. The **⋯** menu contains parameters and requirements, source, history, STEP import and agent commands. Save and rebuild controls appear when needed.

From another terminal in the repository:

```sh
./o3d doctor --json
./o3d inspect --json
./o3d context --json
./o3d check --json
./o3d render --json
./o3d export --json
```

The default project is `projects/demo/`. No account, built-in chat, or hosted backend is required. Model execution happens locally; your external coding agent retains its own network/data settings.

## Work with a coding agent

Open this repository in Claude Code, Codex, or another coding agent. Ask it to follow `AGENTS.md` and the generated instructions in `projects/demo/AGENTS.md`.

For example:

> Inspect the camera bracket. Keep the mounting-hole diameter locked. Increase the plate length by 8 mm, rebuild it, check the measured dimensions, and render a preview for review.

The agent reads `o3d.json`, writes a proposal file, and applies it with an expected revision:

```sh
# Write the complete proposed document to projects/demo/proposal.json.
# The CLI runs in that project directory inside the container.
./o3d apply proposal.json --expected-revision 1 --actor agent \
  --summary "Extend the camera bracket"
./o3d build --json
./o3d check --json
./o3d render --json
```

Use the revision returned by `inspect`; `1` is only the initial example. A stale revision is rejected. User-locked parameters are protected. Browser selection is available through `context`, including the selected face, point, active feature, and geometry revision state. `inspect` returns measured geometry and face metadata without flooding the agent with triangle buffers.

Do not overwrite the live `o3d.json` directly. Apply proposal files through the CLI so revision checks, locks, and history remain effective.

## What works

- **Native feature editing:** rectangle/circle/polygon profiles, construction planes, additive/subtractive extrusions, through/blind holes, rectangular hole arrays, fillets, chamfers, and placements.
- **One shared model:** GUI, JSON source editor, CLI, parameter references, locks, immutable revision snapshots, undo/redo, and restore.
- **Reference geometry:** content-addressed STEP imports with editable placement and visibility.
- **Review:** orbit/zoom, face selection, measurements between surface points, section view, exact dimensions, agent selection context, and PNG rendering.
- **Verification:** solid validity, connectedness, dimensions, mass with supplied density, reference interference/clearance, and an independent exported-mesh audit.
- **Structural studies:** real CalculiX linear-static analysis with isolated Netgen meshing, editable materials/forces/supports, mesh refinement, reaction-balance checks, and stress/displacement overlays.
- **Handoff:** STEP, STL, project source, BOM CSV, and HTML/JSON engineering report in a revision-linked ZIP.
- **Local operation:** background jobs, cancellation, worker timeouts, recoverable job records, and explicit stale-result handling.

The initial modeling domain is brackets and mounts. Arbitrary executable Python projects, general constrained sketching, moving mechanisms, contact analysis, PCB/CAM, ordering, and live multiplayer are outside this release.

## Project format

The authoritative document is `o3d.json`. Geometry, meshes, solver results, and export bundles are derived artifacts under `.o3d/`. Imported STEP files live in `assets/` and are identified by their content hashes.

```json
{
  "name": "Sensor mounting plate",
  "parameters": {
    "thickness": {"value": 4, "unit": "mm", "locked": true}
  },
  "features": [
    {"id": "outline", "type": "profile", "width": 40, "height": 25},
    {"id": "plate", "type": "extrude", "profile": "outline", "distance": "$thickness"},
    {"id": "mount", "type": "hole", "diameter": 3.2, "center": [0, 0, 0]}
  ]
}
```

Dimensions use millimeters. Parameter references use `$name`; arbitrary expressions are not executed. The schema version is `1`. Native geometry references use semantic roles such as `part:xmin` when unique; other selections use strict signatures. Missing or ambiguous references require explicit repair.

See [the JSON Schema](docs/project.schema.json), [architecture](docs/ARCHITECTURE.md), and the [bracket](examples/bracket.json) and [numerical cantilever](examples/cantilever.json) examples.

## Static analysis

Define material elastic properties and their source, select fully fixed planar faces, select separate planar force regions, and enter total force vectors in newtons. Review the small-deflection, linear-elastic and static assumptions before running.

The solver uses mm, N, and MPa with quadratic tetrahedra and consistent distributed-load integration. Results retain the geometry, material, loads, mesh history, logs, and input revision. Changing analysis inputs invalidates affected results. A completed solve is distinct from passing an engineering requirement. Peak stresses at fixtures or sharp corners may be singular; the results do not certify physical strength.

The cantilever example supplies numerical benchmark inputs, not a production material certificate. In local verification, its 0.1589 mm displacement agrees with the analytical 0.1600 mm within 1%.

## Multiple projects

```sh
O3D_PROJECT_NAME=sensor ./o3d init
O3D_PROJECT_NAME=sensor O3D_PORT=8788 ./o3d studio
O3D_PROJECT_NAME=sensor ./o3d inspect --json
```

`O3D_PROJECTS_DIR` changes the host project directory. `O3D_PROJECT_NAME` selects a child project. Keep those settings consistent between the studio and CLI. The launcher supplies your UID/GID for project-file ownership.

## Development and tests

The supported reproducible runtime is the Docker image. It pins separate CAD and meshing Python environments because their OCCT versions differ.

```sh
docker build -t oxygen-cad .
docker run --rm oxygen-cad pytest -q
cd frontend
npm ci
npx playwright install chromium
# Point at a disposable running studio:
O3D_BROWSER_URL=http://127.0.0.1:8787 npm test
```

Browser tests edit their target project. Use a disposable project, not your working design.

For native Linux development, `scripts/bootstrap-native.sh` prepares the Python environments and frontend. Install the solver/runtime dependencies using the Dockerfile as the reference. `O3D_NATIVE=1 ./o3d ...` uses the local environment. With native setup available, `npm test` starts a disposable test server automatically.

Run `python scripts/generate_types.py` from the CAD environment after schema changes. This regenerates the frontend document types and checked-in JSON Schema.

The automated suite includes ten parts undergoing twenty alternating agent/human edits each, STEP round trips, lock/conflict behavior, history, reference integrity, load conservation, actual numerical analysis, and browser editing/export tests. See [validation status](docs/VALIDATION.md) for the exact evidence and remaining platform checks.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Reproducible geometric examples and measurements are especially useful. New feature types must be editable through both the project/CLI interface and the studio, and must participate in verification and invalidation.

## License

Oxygen CAD's original code is [MIT licensed](LICENSE). CAD, meshing, solver, UI, and font dependencies retain their own licenses; see [THIRD_PARTY.md](THIRD_PARTY.md). The Dockerfile fetches unmodified solver packages from Debian with pinned checksums.
