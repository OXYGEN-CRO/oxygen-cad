# Agent setup and plugin installation

The plugin packages the same instructions that ship with the checkout. It uses the local Oxygen CAD runtime and the user's existing coding agent. Installing instructions does not start a CAD server or build Docker images.

| Skill | Workflow and boundary |
| --- | --- |
| `oxygen-cad-setup` | Runtime/project selection, studio and diagnostics |
| `oxygen-cad-model` | Revision-checked editing, native-command discovery, briefs and repair |
| `oxygen-cad-verify` | Current geometry/available-study evidence and standard export |
| `oxygen-cad-parts` | Native catalog search/licensed import first; optional direct API helper with checksum/provenance |
| `oxygen-cad-drawing` | Basic A3 orthographic PDF with hidden edges and nominal overall dimensions |
| `oxygen-cad-dxf` | Planar STEP cross-section DXF and limited entity/contour inspection; no unfolding |
| `oxygen-cad-dfam` | Mesh dimensions/topology, sampled normal chords and six orientation screens |
| `oxygen-cad-dfm` | CNC/sheet/mold evidence review, exact STEP facts and sampled draft/pull screening |
| `oxygen-cad-sendcutsend` | Supplier-specific review against current material/service requirements; no upload/order |
| `oxygen-cad-urdf` | External XML authoring, limited tree/resource/joint/inertia lint and target-consumer checks |
| `oxygen-cad-srdf` | Explicit-URDF group/state/topology lint; MoveIt planning validation remains separate |
| `oxygen-cad-sdf` | Local frame/resource/inertia lint and optional/required installed `gz sdf --check` |
| `oxygen-cad-slice` | Orca preset inheritance resolution, dry-run argv and opt-in local slicing |
| `oxygen-cad-bambu` | Platform-specific vendor-app handoff plan/optional opener; no printer control |

The descriptions support automatic discovery. Invoke a skill explicitly if your host supports it, or ask the agent to inspect/edit/check an Oxygen CAD project. There is no separate MCP server in this package; the skills use the existing JSON CLI.

## Choose one discovery route

**Claude Code plugin:** this repository includes a local marketplace and `.claude-plugin/plugin.json`. From the application checkout, register and install it following the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces):

```sh
claude plugin marketplace add .
claude plugin install oxygen-cad@oxygen-cad
```

Alternatively, start a test session with `claude --plugin-dir /absolute/path/to/oxygen-cad`. Restart or reload the host if needed, then check that it discovers the 14 skills. Keep the checkout present: this is a source-based runtime, not a hosted service.

**Codex plugin:** `.codex-plugin/plugin.json` declares `skills: "./skills/"`, following the [official package format](https://developers.openai.com/plugins/build/plugins). Use a host that supports local plugin registration to load this repository root. This change does not publish a directory listing or register anything in your account. When local plugin registration is unavailable, use the workspace installer below.

**Standalone skills:** run the setup launcher from your application checkout. Python 3 is the only dependency for installing the instructions:

```sh
./o3d setup --agent codex --workspace /absolute/path/to/workspace --dry-run --json
./o3d setup --agent codex --workspace /absolute/path/to/workspace --json
./o3d skills list --json
```

`codex` and `portable` target `<workspace>/.agents/skills`; `claude` targets `<workspace>/.claude/skills`. An explicit `--target /path/to/skills` overrides that location. No user-wide configuration is changed. The `setup` command is an alias for `skills install`. The result lists each skill and whether it was installed, updated or unchanged. Python bytecode/cache files are excluded from distribution and managed-content comparisons, so running a helper does not make a clean installation look locally modified.

Each standalone skill receives a local `runtime.json` pointing at this checkout and a content-hash receipt. Rerunning setup updates unchanged managed installations to the current bundled contents, including the checkout path. Local modifications, unmanaged collisions and symbolic links cause a clear refusal before any skill is written. Other skills and workspace instructions remain intact. To keep personal edits, move that skill to a separately named folder before reinstalling. If you move the application checkout, rerun setup from the new location.

Choose the plugin or standalone discovery per agent workspace so the same skill is not loaded twice. The native developer bootstrap installs the portable skills into this checkout by default; set `O3D_INSTALL_AGENT_SKILLS=0` when using the plugin. The bootstrap still builds its normal development dependencies.

## Runtime and paths

After discovery, the setup skill checks Docker/Compose and runs the existing runtime doctor. Use the main README for runtime installation. `./o3d studio` uses the selected project under `O3D_PROJECTS_DIR` / `O3D_PROJECT_NAME`; use the same variables for CLI operations. Native execution instead needs `O3D_PROJECT_DIR` explicitly. Container-relative input paths and native host paths differ; the modelling skill explains both. Skill files never include credentials or user geometry.

## Dependencies and standalone helpers

All eleven newly covered domains have original workflow instructions and focused references. Geometry/drawing/measurement helpers use the CAD environment's build123d, ezdxf, numpy and trimesh. ReportLab is an additional optional package: `python -m pip install -e '.[skills]'` from the checkout installs it with the application's dependency constraints. Native bootstrap enables the extra; Docker adds `skill-requirements.lock`. The instruction installer does not import or install those dependencies.

Run helpers from their own skill directory with the appropriate interpreter. Each skill documents exact examples and output limits. Keep the full bundle installed: SRDF/SDF helpers reuse the packaged URDF XML/numeric routines, and molding screening reuses the packaged mesh ray helper. Paths are resolved relative to those installed files, not a hardcoded checkout.

External OrcaSlicer, Bambu applications, ROS/MoveIt, Gazebo/libsdformat and supplier/catalog services are not installed automatically. Instructions require checking actual consumer versions and retain missing/unperformed checks. Helper lint `ok` refers only to its stated coverage; it does not establish simulation, tool accessibility or manufacturing certification. Basic PDF sheets omit hole callouts, sections and GD&T; DXF export is a horizontal section, not a bent sheet's flat pattern. Additional workflows do not silently extend the standard export ZIP or native studio schema.

Slicer and Bambu helpers default to plans. `--run` requests local slicing and `--open` requests the vendor application opener; neither helper supplies print/start commands. Windows handoff uses the system opener without interpolating a file path into a shell command.

## Maintainer checks

Run `pytest -q tests/test_agent_skills.py tests/test_skill_workflows.py` in the CAD Python environment with the skills extra. It verifies manifest discovery, installation from another working directory with spaces in its path, dry-run behavior, repeat installation, managed updates, preservation of local edits/unrelated skills and symlink refusals. The workflow suite exercises real STEP/PDF/DXF/mesh artifacts, a local HTTP catalog fixture and malformed robot/preset failure cases. Tests of an argv/URL plan do not establish real slicer/vendor-app integration. It does not prove discovery in a signed-in agent app. Inspect the host's skill list after installing a plugin.

Keep the skills, CLI and project schema consistent as commands change. The Docker runtime and source-mode desktop staging include the bundle and host installer so packaging does not lose the instructions. Prebuilt desktop runtimes inherit those files from Docker. This does not install an agent plugin automatically from the desktop app.

## Design reference

[earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad/tree/2f793659fb7bab537e2f00836e06448fc4087cb2) was reviewed for its pattern of task-focused skills, conditional reference loading and plugin distribution. Oxygen's skill prose, installer and workflows were written for its own typed document, locks, revision service and verification states. No upstream skill text or helper implementation was copied. Oxygen does not depend on cadgen. Expanded workflows use independently written helpers and documented external tools; their limits and validation evidence are recorded separately from upstream capabilities.
