"""Build an explicit desktop resource set, excluding projects, caches and credentials."""
from pathlib import Path
import hashlib
import json
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "desktop/resources/runtime"


def stage():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    archive = os.environ.get("O3D_DESKTOP_RUNTIME_ARCHIVE")
    image = os.environ.get("O3D_DESKTOP_RUNTIME_IMAGE")
    if archive:
        if not image or not image.startswith("sha256:") or len(image) != 71:
            raise ValueError("A Docker image ID is required with the runtime archive")
        shutil.copy2(archive, DEST / "runtime.tar.gz")
        value = {"mode":"image","image":image,"platform":"linux/amd64"}
    else:
        files = ["Dockerfile", ".dockerignore", "requirements.lock", "mesh-requirements.lock", "pyproject.toml", "o3d"]
        directories = ["src", "examples", "tests", "third_party/solidcraft", "engine", "frontend", "skills", ".claude-plugin", ".codex-plugin"]
        for name in files:
            shutil.copy2(ROOT/name, DEST/name)
        for name in directories:
            shutil.copytree(ROOT/name, DEST/name, ignore=shutil.ignore_patterns("target", "node_modules", "dist", "test-results", "__pycache__", ".git", ".pytest_cache", ".o3d"))
        (DEST / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/agent_skills.py", DEST / "scripts/agent_skills.py")
        digest = hashlib.sha256()
        for path in sorted(DEST.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(DEST)).encode())
                digest.update(path.read_bytes())
        value = {"mode":"source","image":"oxygen-cad-desktop:"+digest.hexdigest()[:16],"platform":"linux/amd64"}
    (DEST/"runtime.json").write_text(json.dumps(value,indent=2)+"\n")
    print("Desktop resources staged:",value["mode"])


if __name__ == "__main__":
    stage()
