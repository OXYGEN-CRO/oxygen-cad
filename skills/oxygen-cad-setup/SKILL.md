---
name: oxygen-cad-setup
description: Set up or diagnose a local Oxygen CAD checkout, choose the CLI project, and open its studio. Use for Oxygen CAD installation or runtime failures.
---

# Set up Oxygen CAD

Find the application checkout before running commands. In a plugin install it is two directories above this skill folder. A standalone installation can have a `runtime.json` beside this file with its `repository` path. Verify that the directory contains `o3d` and `pyproject.toml` naming `oxygen-cad`. If it has moved, find the current checkout with the user. Run the examples below from that checkout; quote paths containing spaces. The plugin distributes instructions; its install alone does not build the CAD runtime.

## Select the runtime and project

The supported runtime needs Git, Docker and Docker Compose. Check `docker info` and `docker compose version`; explain a missing prerequisite rather than repeatedly starting a failing runtime. On Apple Silicon the Linux AMD64 runtime uses Docker Desktop emulation. Native development is opt-in; read the checkout's README before using it.

Keep `O3D_PROJECTS_DIR` (absolute host parent directory) and `O3D_PROJECT_NAME` (child directory, `demo` by default) consistent in every CLI invocation and the studio. Inspect an existing project before changing it. Initialize only when creating a project:

```sh
./o3d init --json
./o3d doctor --json
./o3d inspect --json
./o3d context --json
```

`./o3d studio` starts a foreground Compose service. Run it in a managed terminal and read its startup output; the default URL is http://localhost:8787. `O3D_PORT` selects a different host port. It can create the demo if absent. Do not claim the studio opened or the runtime works until you have observed startup/doctor results.

The launcher's `./o3d --help` and command-specific `--help` describe the installed interface. For `O3D_NATIVE=1`, explicitly set `O3D_PROJECT_DIR` to the absolute project directory; the native launcher does not translate the Compose project selection variables. Never change another user's running project to test installation.

## Agent discovery

Plugin hosts load the bundled `skills/` directly. For a host without plugin support, install the same skills into its workspace using the checkout's host-side setup command:

```sh
./o3d setup --agent codex --workspace /absolute/workspace --dry-run --json
./o3d setup --agent codex --workspace /absolute/workspace --json
```

`--agent claude` targets `.claude/skills`; `codex` and `portable` target `.agents/skills`. A custom skills directory can be selected with `--target`. These commands run with Python 3 on the host, without Docker. Keep one discovery route per agent workspace to avoid duplicate skills. Do not install standalone copies if this plugin already supplies them.

## Diagnose from evidence

Read the failed command's JSON/stderr and `doctor` output. Separate a stopped Docker daemon, unavailable runtime image/tool, missing project, revision conflict, unsupported native command and bad geometry. Preserve source, assets and `.o3d/history`; do not remove caches/history as a generic repair. Use a disposable project for a smoke test. Report the command, selected project, actual outcome and remaining blocker.
