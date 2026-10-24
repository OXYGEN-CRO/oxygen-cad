"""Install the bundled agent instructions without loading the CAD runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ".oxygen-cad-install.json"


def files_at(directory: Path) -> dict[str, bytes]:
    if directory.is_symlink():
        raise ValueError(f"Refusing symlink skill directory: {directory}")
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Refusing symlink inside skill: {path}")
        if path.is_file() and path.name != RECEIPT:
            result[path.relative_to(directory).as_posix()] = path.read_bytes()
    return result


def hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}


def bundled() -> dict[str, dict[str, bytes]]:
    result = {}
    runtime = (json.dumps({"repository": str(ROOT), "launcher": str(ROOT / "o3d")}, indent=2) + "\n").encode()
    for path in sorted((ROOT / "skills").iterdir()):
        if path.is_dir() and (path / "SKILL.md").is_file():
            result[path.name] = files_at(path) | {"runtime.json": runtime}
    if not result:
        raise ValueError("No bundled skills found")
    return result


def install(target: Path, dry_run: bool = False) -> dict:
    target = target.expanduser().absolute()
    # Do not follow agent-directory links into an unexpected installation.
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ValueError(f"Refusing symlink target ancestor: {path}")
    sources = bundled()
    plans = []
    for name, files in sources.items():
        destination = target / name
        if destination == ROOT / "skills" / name:
            raise ValueError("The bundled skills are already present; choose an agent skills directory")
        action = "install"
        if destination.exists() or destination.is_symlink():
            if not destination.is_dir() or destination.is_symlink():
                raise ValueError(f"Refusing existing non-directory or symlink: {destination}")
            receipt = destination / RECEIPT
            if not receipt.is_file() or receipt.is_symlink():
                raise ValueError(f"Refusing to overwrite unmanaged skill: {destination}")
            current = hashes(files_at(destination))
            managed = json.loads(receipt.read_text())
            if managed.get("package") != "oxygen-cad" or managed.get("files") != current:
                raise ValueError(f"Locally modified skill; preserve it before updating: {destination}")
            action = "unchanged" if current == hashes(files) else "update"
        plans.append({"name": name, "path": str(destination), "action": action})

    # Validate every destination before writing any skill.
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            if plan["action"] == "unchanged":
                continue
            destination = Path(plan["path"])
            files = sources[plan["name"]]
            with tempfile.TemporaryDirectory(prefix=".oxygen-cad-", dir=target) as temporary:
                staging, backup = Path(temporary) / "skill", Path(temporary) / "previous"
                staging.mkdir()
                for name, data in files.items():
                    output = staging / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
                (staging / RECEIPT).write_text(json.dumps({"package": "oxygen-cad", "files": hashes(files)}, indent=2) + "\n")
                if destination.exists():
                    os.replace(destination, backup)
                try:
                    os.replace(staging, destination)
                except OSError:
                    if backup.exists():
                        os.replace(backup, destination)
                    raise
    return {"package": "oxygen-cad", "repository": str(ROOT), "target": str(target), "dry_run": dry_run, "skills": plans}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List skills bundled with this checkout")
    listing.add_argument("--json", action="store_true")
    installing = commands.add_parser("install", help="Install or update workspace-local agent skills")
    installing.add_argument("--agent", choices=("codex", "claude", "portable"), default="portable")
    installing.add_argument("--workspace", type=Path, default=ROOT)
    installing.add_argument("--target", type=Path, help="Explicit skills directory (overrides workspace/agent)")
    installing.add_argument("--dry-run", action="store_true")
    installing.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            value = {"package": "oxygen-cad", "skills": list(bundled())}
        else:
            folder = ".claude" if args.agent == "claude" else ".agents"
            value = install(args.target or args.workspace / folder / "skills", args.dry_run)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
