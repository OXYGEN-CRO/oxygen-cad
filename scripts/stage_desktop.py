"""Stage the tracked Docker build closure, excluding local user data and caches.

Docker COPY inputs define the closure, so a new public runtime module/resource is
included without maintaining a second directory list. Only tracked source files
are eligible: commit source additions before building a desktop release.
"""
from pathlib import Path, PurePosixPath
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "desktop/resources/runtime"
FORBIDDEN = {"projects", ".o3d", ".git", ".venv", ".mesh-venv", ".tools", "node_modules", "target", "__pycache__", "test-results", ".pytest_cache"}


def tracked_files(root):
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return [name for name in result.stdout.decode().split("\0") if name]


def copy_inputs(dockerfile):
    # Join escaped newlines; Docker's JSON and shell COPY syntaxes are both supported.
    text = re.sub(r"\\\r?\n", " ", dockerfile)
    inputs = ["Dockerfile", ".dockerignore"]
    for line in text.splitlines():
        match = re.match(r"\s*COPY\s+(.+)", line, re.I)
        if not match:
            continue
        value = match.group(1).strip()
        if re.search(r"(?:^|\s)--from(?:=|\s)", value):
            continue
        value = re.sub(r"^--(?:chown|chmod|link|parents|exclude)(?:=\S+)?\s+", "", value)
        tokens = json.loads(value) if value.startswith("[") else shlex.split(value)
        inputs.extend(tokens[:-1])
    return inputs


def source_files(root):
    tracked = tracked_files(root)
    selected = set()
    for pattern in copy_inputs((root / "Dockerfile").read_text()):
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts or "$" in pattern or pattern in {".", "./"}:
            raise ValueError(f"Docker COPY input requires an explicit safe source: {pattern}")
        prefix = pattern.rstrip("/") + "/"
        matches = [name for name in tracked if name == pattern.rstrip("/") or name.startswith(prefix) or fnmatch.fnmatchcase(name, pattern)]
        if not matches:
            raise ValueError(f"Docker COPY input has no tracked source: {pattern}")
        selected.update(matches)
    for name in tracked:
        if name in {"LICENSE", "README.md"}:
            selected.add(name)
    for name in selected:
        path = root / name
        parts = PurePosixPath(name).parts
        if any(part in FORBIDDEN or part.startswith(".env") for part in parts):
            raise ValueError(f"User data/cache/credential path cannot enter runtime: {name}")
        cursor = root
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"Symbolic links cannot enter a runtime release: {name}")
        if not path.is_file():
            raise ValueError(f"Tracked runtime input is unavailable: {name}")
    return sorted(selected)


def file_metadata(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def stage():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".runtime-stage-", dir=DEST.parent))
    previous = DEST.with_name("runtime.previous")
    try:
        archive = os.environ.get("O3D_DESKTOP_RUNTIME_ARCHIVE")
        image = os.environ.get("O3D_DESKTOP_RUNTIME_IMAGE")
        if archive:
            if not image or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise ValueError("A complete Docker sha256 image ID is required with the runtime archive")
            source = Path(archive)
            if not source.is_file() or source.is_symlink():
                raise ValueError("Runtime image archive must be a regular file")
            shutil.copy2(source, temporary / "runtime.tar.gz")
            files = {"runtime.tar.gz": file_metadata(temporary / "runtime.tar.gz")}
            value = {"schema_version": 1, "mode": "image", "image": image, "platform": "linux/amd64", "files": files}
        else:
            files = {}
            for name in source_files(ROOT):
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
                files[name] = file_metadata(target)
            digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
            value = {"schema_version": 1, "mode": "source", "image": "oxygen-cad-desktop:" + digest[:24], "platform": "linux/amd64", "files": files}
        (temporary / "runtime.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        # Keep the previous staged runtime if validation/copy fails. Tauri only sees a complete tree.
        if previous.exists():
            shutil.rmtree(previous)
        if DEST.exists():
            DEST.rename(previous)
        temporary.rename(DEST)
        if previous.exists():
            shutil.rmtree(previous)
        print("Desktop resources staged:", value["mode"], len(files), "verified files")
        return value
    except BaseException:
        if previous.exists() and not DEST.exists():
            previous.rename(DEST)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


if __name__ == "__main__":
    stage()
