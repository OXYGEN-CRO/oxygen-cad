# Apple Silicon desktop preview

The Tauri application is a Rust launcher around the existing Oxygen React studio. It starts a local Docker runtime and opens the studio inside a native app window. It uses no server account, SSH tunnel or remote CAD service.

## Install a test build

1. Install and start Docker Desktop on the Mac.
2. Download `Oxygen-CAD-Apple-Silicon.zip` from a tested preview under [Releases](https://github.com/OXYGEN-CRO/oxygen-cad/releases). Public previews are published after the main-branch backend, browser and Mac packaging checks all pass. Each includes its source/build receipt and SHA256 checksum.
3. Unzip once. Move **Oxygen CAD.app** to Applications and open it. For an older build available only under [Actions artifacts](https://github.com/OXYGEN-CRO/oxygen-cad/actions/workflows/ci.yml), first unzip the artifact wrapper to reach the app ZIP.
4. The first launch imports its bundled runtime into Docker. Following launches reuse it. Keep Docker Desktop running while using Oxygen.

Test builds are ad-hoc signed, not notarized. macOS may require **System Settings → Privacy & Security → Open Anyway** the first time. No signing account or certificate is needed to build the development preview.

Projects live under `~/Library/Application Support/io.oxygen.cad/projects`. Closing the app stops its own container; project files persist. The runtime listens only on a dynamically allocated loopback port. The desktop uses the same UI, revision checks, locks and solver pipeline as the browser version.

The initial runtime is Linux AMD64, translated by Docker Desktop on Apple Silicon. This retains the tested, pinned OCP/Netgen/CalculiX stack, whose mesher wheels do not currently cover Linux ARM64. The window is native ARM64. A fully native standalone macOS solver/runtime is separate packaging work; an Apple Silicon application build does not prove physical Mac/Safari usability or simulation performance.

## Build from source

Install Rust 1.95, Node 22, Python 3 and the [Tauri platform prerequisites](https://v2.tauri.app/start/prerequisites/), then:

```sh
npm ci --prefix desktop
cd desktop
npm run build -- --target aarch64-apple-darwin --bundles app
```

A source build includes a scoped backend source context and builds its Docker image on first launch. CI instead embeds an already-built compressed Docker runtime, avoiding a compiler/setup step on the test computer. `scripts/stage_desktop.py` is the only resource-staging path and excludes projects, credentials, virtual environments, node_modules and build caches.

For a development window against an already-running local studio:

```sh
O3D_DESKTOP_URL=http://127.0.0.1:8787 npm run dev
```

This development override accepts loopback HTTP only. Production packages start their own local runtime. Source and bundled-runtime manifests select a pinned image rather than silently attaching to another session's project.
