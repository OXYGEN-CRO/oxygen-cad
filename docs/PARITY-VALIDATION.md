# Parity validation

Browser specs mutate the engineering document. Run each spec against a new
project so a canonical native model created in one domain cannot contaminate
the next domain's fixture. The runner discovers every `frontend/tests/**/*.spec.ts`,
uses the dedicated modeling configuration for its canvas fixture, and also runs
`tests/browser_manufacturing.cjs`. It continues after failures and exits nonzero
if any suite fails.

## Reproducible runtime and backend checks

```sh
docker build -t oxygen-cad .
docker run --rm oxygen-cad sh -eu -c '
  python -m pip install --no-cache-dir -r tests/requirements-verification.lock
  python -c "import pypdf, gerbonara"
  pytest -q
'
```

The verification lock pins the independent PDF and Gerber/Excellon readers and
their transitive dependencies. Installation happens in the disposable test
container; the distributed desktop/runtime image does not gain these test tools.
Reader import failures stop verification before pytest can skip their tests.

## All browser suites in Docker

```sh
npm ci --prefix frontend
cd frontend
npx playwright install --with-deps chromium
npm run build
cd ..
python3 scripts/run_browser_checks.py --mode docker --image oxygen-cad
```

Every suite gets a uniquely named runtime container, an unpublished temporary
project filesystem, and a free loopback port. The runner waits for the API and a
successful build before launching the browser. Containers have no host project
mount and are removed after each suite, including failed suites. Modeling uses
its separate Vite UI fixture on port 5194, proxied to that suite's disposable API.
Assembly acceptance is seeded with `examples/assemblies/slider.json`. Specs that
generate native source files invoke the image's engine through a short-lived
helper container with their generated fixture working directory mounted. The
native-scene 10/1000 occurrence browser check is explicitly enabled in both modes.
The CI workflow runs these commands, preserves browser evidence, packages the
tested runtime, and retains the Apple Silicon build, ad-hoc signing, and archive
jobs. The independent parts-catalog workflow is unchanged.

## Native development

Prepare `.venv`, `.mesh-venv`, solver/manufacturing tools, Chromium, and the built
frontend as described in the native setup guide. Build the Rust engine or set
`O3D_ENGINE_BINARY` to the intended binary. Then run:

```sh
.venv/bin/python -m pip install -r tests/requirements-verification.lock
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q
python3 scripts/run_browser_checks.py --mode native
```

Native mode starts a new `scripts/test_server.py` process and temporary project
for each suite, with `PYTHONPATH` and frontend assets rooted in the current
checkout. No project data is read from a working design.

Useful targeted commands:

```sh
python3 scripts/run_browser_checks.py --list
python3 scripts/run_browser_checks.py --spec studio.spec.ts --spec modeling/modeling.spec.ts
python3 scripts/run_browser_checks.py --spec manufacturing
python3 scripts/run_browser_checks.py --mode docker --docker-command 'sudo -n docker' --image oxygen-cad
```

Do not set `O3D_BROWSER_URL` when using the runner: it creates its own targets and
rejects an inherited URL. `--port` optionally selects an unused loopback port;
8787 is always refused. Direct Playwright invocations with an external URL also
require `O3D_BROWSER_DISPOSABLE=1` and a loopback port other than 8787. Prefer the
runner for complete verification and per-spec isolation.

Evidence is saved under `frontend/test-results/isolated/`: a machine-readable
`summary.json`, each fixture's log, each suite's browser log, and Playwright or
manufacturing artifacts. Screenshots explicitly named by specs also remain in
`frontend/test-results/`. A passing Linux run establishes the measured test
behaviors; macOS application compatibility and physical manufacturing fit need
their separate checks.
