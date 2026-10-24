# Agent setup and plugin installation

The plugin packages the same instructions that ship with the checkout. It uses the local Oxygen CAD runtime and the user's existing coding agent. Installing instructions does not start a CAD server or build Docker images.

| Skill | Use |
| --- | --- |
| `oxygen-cad-setup` | Runtime setup, project selection, studio startup and diagnostics |
| `oxygen-cad-model` | Inspect selection, propose a full document, apply with a revision, rebuild and review |
| `oxygen-cad-verify` | Numeric geometry/fit evidence, specified static studies and current-revision export |

The descriptions support automatic discovery. Invoke a skill explicitly if your host supports it, or ask the agent to inspect/edit/check an Oxygen CAD project. There is no separate MCP server in this package; the skills use the existing JSON CLI.

## Choose one discovery route

**Claude Code plugin:** this repository includes a local marketplace and `.claude-plugin/plugin.json`. From the application checkout, register and install it following the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces):

```sh
claude plugin marketplace add .
claude plugin install oxygen-cad@oxygen-cad
```

Alternatively, start a test session with `claude --plugin-dir /absolute/path/to/oxygen-cad`. Restart or reload the host if needed, then check that it discovers the three skills. Keep the checkout present: this is a source-based runtime, not a hosted service.

**Codex plugin:** `.codex-plugin/plugin.json` declares `skills: "./skills/"`, following the [official package format](https://developers.openai.com/plugins/build/plugins). Use a host that supports local plugin registration to load this repository root. This change does not publish a directory listing or register anything in your account. When local plugin registration is unavailable, use the workspace installer below.

**Standalone skills:** run the setup launcher from your application checkout. Python 3 is the only dependency for installing the instructions:

```sh
./o3d setup --agent codex --workspace /absolute/path/to/workspace --dry-run --json
./o3d setup --agent codex --workspace /absolute/path/to/workspace --json
./o3d skills list --json
```

`codex` and `portable` target `<workspace>/.agents/skills`; `claude` targets `<workspace>/.claude/skills`. An explicit `--target /path/to/skills` overrides that location. No user-wide configuration is changed. The `setup` command is an alias for `skills install`. The result lists each skill and whether it was installed, updated or unchanged.

Each standalone skill receives a local `runtime.json` pointing at this checkout and a content-hash receipt. Rerunning setup updates unchanged managed installations to the current bundled contents, including the checkout path. Local modifications, unmanaged collisions and symbolic links cause a clear refusal before any skill is written. Other skills and workspace instructions remain intact. To keep personal edits, move that skill to a separately named folder before reinstalling. If you move the application checkout, rerun setup from the new location.

Choose the plugin or standalone discovery per agent workspace so the same skill is not loaded twice. The native developer bootstrap installs the portable skills into this checkout by default; set `O3D_INSTALL_AGENT_SKILLS=0` when using the plugin. The bootstrap still builds its normal development dependencies.

## Runtime and paths

After discovery, the setup skill checks Docker/Compose and runs the existing runtime doctor. Use the main README for runtime installation. `./o3d studio` uses the selected project under `O3D_PROJECTS_DIR` / `O3D_PROJECT_NAME`; use the same variables for CLI operations. Native execution instead needs `O3D_PROJECT_DIR` explicitly. Container-relative input paths and native host paths differ; the modelling skill explains both. Skill files never include credentials or user geometry.

## Maintainer checks

Run `pytest -q tests/test_agent_skills.py` in the CAD Python environment. It verifies manifest discovery, installation from another working directory with spaces in its path, dry-run behavior, repeat installation, managed updates, preservation of local edits/unrelated skills and symlink refusals. It does not prove discovery in a signed-in agent app. Inspect the host's skill list after installing a plugin.

Keep the skills, CLI and project schema consistent as commands change. The Docker runtime and source-mode desktop staging include the bundle and host installer so packaging does not lose the instructions. Prebuilt desktop runtimes inherit those files from Docker. This does not install an agent plugin automatically from the desktop app.

## Design reference

[earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad/tree/2f793659fb7bab537e2f00836e06448fc4087cb2) was reviewed for its pattern of task-focused skills, conditional reference loading and plugin distribution. Oxygen's skill prose, installer and workflows were written for its own typed document, locks, revision service and verification states. No upstream skill text or implementation was copied, and Oxygen does not depend on cadgen or advertise its drawing, fabrication or printer integrations.
