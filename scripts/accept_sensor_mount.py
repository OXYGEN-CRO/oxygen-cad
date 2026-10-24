"""Run the real catalog → sensor carrier → studio → verified handoff acceptance case.

Uses a new output directory and an existing configured CAD/catalog runtime.
It never points browser tests at a user project and never sends printer commands.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(output: Path, catalog: str, port: int):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Acceptance output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"; logs.mkdir()
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), O3D_FRONTEND_DIST=str(ROOT / "frontend/dist"))
    project = output / "project"
    evidence = {"hostname": socket.gethostname(), "project": str(project), "runtime": "existing configured native runtime; fresh project and skill installation",
                "independent_agent_discovery_tested": False, "steps": [], "port": port}
    def execute(name, command, expected=0):
        process = subprocess.run([str(v) for v in command], env=env, cwd=ROOT, text=True, capture_output=True, timeout=600)
        (logs / (name + ".stdout")).write_text(process.stdout)
        (logs / (name + ".stderr")).write_text(process.stderr)
        if process.returncode != expected:
            raise RuntimeError(f"{name} exited {process.returncode}: {process.stderr[-2000:]}")
        evidence["steps"].append({"step": name, "returncode": process.returncode})
        return json.loads(process.stdout or process.stderr)
    def cli(name, *args, expected=0, root=project):
        return execute(name, [sys.executable, "-m", "o3d.cli", "--project", root, *args], expected)
    try:
        installed = execute("install-skills", ["sh", ROOT / "o3d", "setup", "--agent", "portable", "--workspace", output / "agent-workspace", "--json"])
        assert len(installed["skills"]) >= 14
        catalog_result = execute("search-part", [catalog, "search", "BNO055", "--downloadable", "--limit", "5"])
        selected = next(row for row in catalog_result["items"] if row["name"] == "BNO055 Stemma")
        part = execute("get-part", [catalog, "get", selected["id"]])
        asset = next(row for row in part["assets"] if row["format"] == "step" and row["license"] == "MIT")
        source = execute("fetch-asset", [catalog, "fetch-asset", asset["id"], output / "supplier-source"])
        import build123d as bd
        shape = bd.import_step(source["path"])
        pcb = next(s for s in shape.solids() if abs(s.bounding_box().size.X - 25.4) < 1e-6 and abs(s.bounding_box().size.Y - 20.32) < 1e-6 and abs(s.bounding_box().size.Z - 1.57) < 1e-6)
        holes = sorted({tuple(round(v, 5) for v in edge.arc_center) for edge in pcb.edges()
                        if edge.geom_type == bd.GeomType.CIRCLE and abs(edge.radius - 1.25) < 1e-6 and abs(edge.arc_center.Z) < 1e-6})
        assert holes == [(2.54, 2.54, 0.0), (2.54, 17.78, 0.0), (22.86, 2.54, 0.0), (22.86, 17.78, 0.0)]
        evidence["sensor"] = {"part_id": selected["id"], "asset_id": asset["id"], "sha256": source["sha256"], "license": source["license"],
                              "pcb_dimensions_mm": list(pcb.bounding_box().size), "mount_holes_mm": holes, "hole_diameter_mm": 2.5}
        initial = cli("init", "init", "--template", ROOT / "examples/sensor-mount.json", "--json")
        imported = execute("import-part", [catalog, "import-part", asset["id"], project, str(initial["revision"])])
        state = cli("inspect-initial", "inspect", "--json")
        proposal = state["project"]
        proposal["name"] = "BNO055 acceptance " + output.name
        proposal["references"][0]["placement"]["translation"] = [-12.7, -10.16, 6]
        (project / "proposal.json").write_text(json.dumps(proposal))
        applied = cli("place-sensor", "apply", project / "proposal.json", "--expected-revision", str(proposal["revision"]), "--actor", "agent", "--summary", "Place sourced sensor at measured mounting pattern", "--json")
        # Carry the actual source notice and source record with the imported asset.
        local_asset = project / applied["references"][0]["file"]
        for suffix in (".LICENSE.txt", ".catalog.json"):
            shutil.copy2(source["path"] + suffix, str(local_asset) + suffix)
        # A normal agent cannot remove the locked hole constraint or apply an old revision.
        rejected = cli("reject-stale-proposal", "apply", project / "proposal.json", "--expected-revision", str(proposal["revision"]), "--actor", "agent", "--json", expected=1)
        assert "Revision conflict" in rejected["error"]
        locked = json.loads(json.dumps(applied)); locked["parameters"]["sensor_hole_diameter"]["value"] = 4
        (project / "locked-proposal.json").write_text(json.dumps(locked))
        rejected = cli("reject-locked-change", "apply", project / "locked-proposal.json", "--expected-revision", str(applied["revision"]), "--actor", "agent", "--json", expected=1)
        assert "locked" in rejected["error"]
        build = cli("build-initial", "build", "--json")
        checks = cli("check-initial", "check", "--json"); assert checks["state"] == "passed"
        simulation = cli("simulate-initial", "simulate", "--json"); assert simulation["state"] == "passed"
        initial_handoff = cli("handoff-initial", "handoff", "create", "--section-z", "1.5", "--json")
        assert initial_handoff["state"] == "current"
        # Assert a port is free; never attach this acceptance test to somebody's server.
        with socket.socket() as probe: probe.bind(("127.0.0.1", port))
        server_log = (logs / "studio.log").open("w")
        server = subprocess.Popen([sys.executable, "-m", "o3d.cli", "--project", str(project), "studio", "--port", str(port)], cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(120):
                if server.poll() is not None: raise RuntimeError("Disposable studio exited; inspect studio.log")
                try:
                    urllib.request.urlopen(url + "/api/project", timeout=1).close(); break
                except OSError: time.sleep(.25)
            else: raise RuntimeError("Disposable studio failed to start")
            browser_env = dict(env, O3D_BROWSER_URL=url, O3D_ACCEPTANCE_NAME=applied["name"], O3D_ACCEPTANCE_OUTPUT=str(output))
            process = subprocess.run(["npx", "playwright", "test", "tests/sensor-mount.spec.ts"], cwd=ROOT / "frontend", env=browser_env, text=True, capture_output=True, timeout=180)
            (logs / "browser.stdout").write_text(process.stdout); (logs / "browser.stderr").write_text(process.stderr)
            if process.returncode: raise RuntimeError("Browser acceptance failed; inspect browser logs and traces")
            evidence["steps"].append({"step": "real-studio-browser-edit-check-context", "returncode": 0})
        finally:
            server.terminate(); server.wait(timeout=15); server_log.close()
        stale = cli("handoff-stale", "handoff", "status", "--json", expected=2)
        assert stale["state"] == "stale" and "project_changed" in stale["reasons"]
        changed = cli("inspect-after-browser", "inspect", "--json")
        assert changed["project"]["parameters"]["sensor_hole_diameter"]["value"] == 2.7
        assert abs(changed["build"]["dimensions_mm"][0] - 46) < 1e-4
        assert changed["analysis_state"] == "stale"
        final_sim = cli("simulate-edited", "simulate", "--json"); assert final_sim["state"] == "passed"
        final = cli("handoff-final", "handoff", "create", "--section-z", "1.5", "--json")
        assert final["state"] == "current"
        cli("old-handoff-stays-stale", "handoff", "status", "--manifest", initial_handoff["manifest"], "--json", expected=2)
        reopened = output / "reopened"; reopened.mkdir()
        with zipfile.ZipFile(project / ".o3d" / final["artifact"]) as package:
            for member in package.namelist():
                if not (reopened / member).resolve().is_relative_to(reopened): raise ValueError("Unsafe archive member")
            package.extractall(reopened)
        reopened_state = cli("reopen-inspect", "inspect", "--json", root=reopened)
        assert reopened_state["project"] == changed["project"]
        rebuilt = cli("reopen-build", "build", "--json", root=reopened)
        assert abs(rebuilt["dimensions_mm"][0] - 46) < 1e-4
        fresh = cli("reopen-handoff", "handoff", "status", "--manifest", "manifest.json", "--json", root=reopened)
        assert fresh["state"] == "current"
        # Viewable PDF proof, without relying on a PDF filename or screenshot alone.
        import pypdfium2
        pdf = pypdfium2.PdfDocument(reopened / "derivatives/drawing.pdf")
        page = pdf[0]; page.render(scale=1.25).to_pil().save(output / "drawing-review.png")
        text = page.get_textpage().get_text_range()
        assert "46.000 mm" in text and "32.000 mm" in text
        evidence.update(state="passed", initial_dimensions_mm=build["dimensions_mm"], final_dimensions_mm=rebuilt["dimensions_mm"],
                        locked_hole_diameter_mm=2.7, stale_detection=stale, handoff=final,
                        reference_interference_mm3=list(checks["references"].values())[0]["interference_mm3"],
                        initial_simulation=simulation, final_simulation=final_sim,
                        unverified=["independent agent skill selection", "cold runtime installation", "physical fit/strength", "slicing or printer operation"])
        (output / "acceptance.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
        print(json.dumps({"state": "passed", "output": str(output), "project": str(project), "handoff": str(project / ".o3d" / final["artifact"]),
                          "dimensions_mm": rebuilt["dimensions_mm"], "steps": len(evidence["steps"])}, indent=2))
    except Exception as error:
        evidence.update(state="failed", error=str(error))
        (output / "acceptance.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog-cli", default="o3d-parts")
    parser.add_argument("--port", type=int, default=19766)
    args = parser.parse_args()
    run(args.output.resolve(), args.catalog_cli, args.port)
