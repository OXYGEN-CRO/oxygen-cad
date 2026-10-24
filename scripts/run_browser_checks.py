#!/usr/bin/env python3
"""Run every browser spec against a fresh, owned disposable project."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def available_port(selected: int | None) -> int:
    if selected == 8787 or (selected is not None and not 1 <= selected <= 65535):
        raise ValueError("Browser fixtures require a port from 1 to 65535 other than 8787")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", selected or 0))
        port = listener.getsockname()[1]
    return available_port(None) if port == 8787 else port


def request_json(base: str, path: str, data: dict | None = None) -> dict:
    request = urllib.request.Request(
        base + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def prepare_fixture(base: str, alive, timeout: int, template: Path | None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive():
            raise RuntimeError("Disposable fixture exited before becoming ready")
        try:
            current = request_json(base, "/api/project")
            break
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    else:
        raise TimeoutError("Disposable fixture did not become ready")
    if template is not None:
        project = json.loads(template.read_text())
        project["revision"] = current["revision"]
        request_json(base, "/api/commands", {
            "expected_revision": current["revision"], "actor": "human", "action": "replace",
            "project": project, "summary": "Seed disposable browser acceptance fixture",
        })
    job = request_json(base, "/api/jobs/build", {})
    while time.monotonic() < deadline:
        state = request_json(base, "/api/jobs/" + job["id"])
        if state["state"] == "succeeded":
            return
        if state["state"] in ("failed", "cancelled"):
            raise RuntimeError("Fixture build failed: " + state.get("message", str(state)))
        time.sleep(0.25)
    raise TimeoutError("Disposable fixture build timed out")


@contextmanager
def fixture(args, directory: Path, env: dict[str, str], template: Path | None, platform: bool):
    port = available_port(args.port)
    base = f"http://127.0.0.1:{port}"
    process = None
    name = "oxygen-browser-" + uuid.uuid4().hex[:12]
    docker = shlex.split(args.docker_command)
    with (directory / "fixture.log").open("w") as log:
        try:
            if args.mode == "docker":
                container_port = 9371 if platform else 8787
                platform_command = ["python", "tests/platform_browser_server.py"] if platform else []
                platform_env = ["-e", "O3D_PLATFORM_TEST_HOST=0.0.0.0", "-e", "O3D_PLATFORM_TEST_PORT=9371"] if platform else []
                subprocess.run(
                    docker + ["run", "--rm", "-d", "--name", name,
                              "-p", f"127.0.0.1:{port}:{container_port}", "--tmpfs", "/projects:rw,nosuid,nodev"]
                    + platform_env + [args.image] + platform_command,
                    check=True, stdout=log, stderr=subprocess.STDOUT,
                )
                alive = lambda: subprocess.run(
                    docker + ["inspect", "--format", "{{.State.Running}}", name],
                    capture_output=True, text=True,
                ).stdout.strip() == "true"
            else:
                fixture_env = env | {"O3D_TEST_PORT": str(port), "O3D_PLATFORM_TEST_PORT": str(port),
                                     "O3D_PLATFORM_TEST_HOST": "127.0.0.1"}
                server = ROOT / ("tests/platform_browser_server.py" if platform else "scripts/test_server.py")
                process = subprocess.Popen(
                    [str(args.python), str(server)],
                    cwd=ROOT, env=fixture_env, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                alive = lambda: process.poll() is None
            prepare_fixture(base, alive, args.fixture_timeout, template)
            yield base
        finally:
            if args.mode == "docker":
                subprocess.run(docker + ["logs", name], stdout=log, stderr=subprocess.STDOUT)
                subprocess.run(docker + ["rm", "-f", name], stdout=log, stderr=subprocess.STDOUT)
            elif process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def run_command(command: list[str], env: dict[str, str], log_path: Path) -> int:
    print("$ " + shlex.join(command), flush=True)
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command, cwd=FRONTEND, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            return process.wait()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def main() -> int:
    specs = sorted(path.relative_to(FRONTEND / "tests").as_posix()
                   for path in (FRONTEND / "tests").rglob("*.spec.ts")) + ["manufacturing"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("native", "docker"), default="native")
    parser.add_argument("--spec", action="append", choices=specs, help="Repeat to select specs; default: all")
    parser.add_argument("--list", action="store_true", help="List discovered suites without starting a fixture")
    parser.add_argument("--image", default="oxygen-cad", help="Runtime image for Docker mode")
    parser.add_argument("--docker-command", default="docker", help="For example: 'sudo -n docker'")
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument("--port", type=int, help="Reserve this unused loopback port; default: select a free port")
    parser.add_argument("--fixture-timeout", type=int, default=180)
    parser.add_argument("--output", type=Path, default=FRONTEND / "test-results/isolated")
    args = parser.parse_args()
    if args.list:
        print("\n".join(specs))
        return 0
    if os.environ.get("O3D_BROWSER_URL"):
        parser.error("Unset O3D_BROWSER_URL: this runner must create and own every fixture")
    if args.port == 8787 or (args.port is not None and not 1 <= args.port <= 65535):
        parser.error("Select an unused port from 1 to 65535 other than the user's studio port 8787")
    if args.mode == "native" and not args.python.is_file():
        parser.error(f"Native fixture interpreter not found: {args.python}")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")]))
    env["O3D_FRONTEND_DIST"] = str(FRONTEND / "dist")
    env["O3D_NATIVE"] = "1"
    env["O3D_NATIVE_SCENE_TEST"] = "1"
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for spec in args.spec or specs:
            started = time.monotonic()
            directory = args.output / spec.removesuffix(".spec.ts").replace("/", "-")
            directory.mkdir(parents=True, exist_ok=True)
            print(f"\n=== Fresh disposable fixture: {spec} ===", flush=True)
            result = {"spec": spec, "mode": args.mode, "status": "failed"}
            try:
                template = ROOT / "examples/assemblies/slider.json" if spec == "assemblies.spec.ts" else None
                platform = spec == "platform-lifecycle.spec.ts"
                with fixture(args, directory, env, template, platform) as base:
                    result["fixture_url"] = base
                    browser_env = env | {
                        "O3D_BROWSER_URL": base,
                        "O3D_BROWSER_DISPOSABLE": "1",
                        "O3D_BROWSER_EVIDENCE": str(directory / "artifacts"),
                        "O3D_TEST_PORT": base.rsplit(":", 1)[1],
                        "O3D_PLATFORM_BROWSER_FIXTURE": "1" if platform else "",
                    }
                    if args.mode == "docker":
                        # Some specs generate native source files outside the API.
                        # Execute the exact image engine; mount only their working
                        # directory so generated file.save paths remain readable.
                        engine = directory / "oxygen-engine"
                        engine.write_text(
                            "#!/bin/sh\nexec " + shlex.join(shlex.split(args.docker_command))
                            + ' run --rm -i --entrypoint /usr/local/bin/oxygen-engine'
                            + ' --mount "type=bind,src=$PWD,dst=$PWD" --workdir "$PWD" '
                            + shlex.quote(args.image) + ' "$@"\n'
                        )
                        engine.chmod(0o755)
                        browser_env["O3D_ENGINE_BINARY"] = str(engine)
                    if spec == "manufacturing":
                        command = ["node", str(ROOT / "tests/browser_manufacturing.cjs")]
                    else:
                        fixture_ui = spec.startswith("modeling/") or spec == "simulation-domain.spec.ts"
                        config = "tests/modeling/playwright.config.ts" if fixture_ui else "playwright.config.ts"
                        command = ["npx", "--no-install", "playwright", "test", "--config", config,
                                   spec, "--output", str(directory / "artifacts"), "--reporter=list"]
                    result["exit_code"] = run_command(command, browser_env, directory / "browser.log")
                    result["status"] = "passed" if result["exit_code"] == 0 else "failed"
            except (OSError, subprocess.SubprocessError, RuntimeError, TimeoutError) as error:
                result["error"] = str(error)
                print(f"FAILED {spec}: {error}\nSee {directory / 'fixture.log'}", flush=True)
            result["seconds"] = round(time.monotonic() - started, 2)
            results.append(result)
    finally:
        (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    failures = [result["spec"] for result in results if result["status"] != "passed"]
    print(f"\n{len(results) - len(failures)}/{len(results)} suites passed. Evidence: {args.output}")
    if failures:
        print("Failed: " + ", ".join(failures))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
