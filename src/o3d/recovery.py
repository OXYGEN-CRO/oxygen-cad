"""Durable local transactions; the journal is the commit point.

Once a journal is fsynced, reopening completes it. Before that point the previous
revision is authoritative. This protocol assumes a local filesystem with atomic
rename and working fsync; network filesystems are not a supported database.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def checksum(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value):
    atomic_bytes(path, canonical(value))


def commit(root: Path, record: dict, fault=None):
    cache = root / ".o3d"
    journal = cache / "transaction.json"
    write_json(journal, {"schema_version": 1, "sha256": checksum(record), "record": record})
    if fault:
        fault("journal")
    replay(root, fault)


def replay(root: Path, fault=None) -> bool:
    """Replay an authenticated-by-checksum local journal. Caller holds Store lock."""
    cache = root / ".o3d"
    journal = cache / "transaction.json"
    if not journal.exists():
        return False
    value = json.loads(journal.read_text())
    record = value.get("record")
    if value.get("schema_version") != 1 or not isinstance(record, dict) or checksum(record) != value.get("sha256"):
        raise ValueError("Save journal is corrupt; preserved at .o3d/transaction.json. Recover from a verified backup.")
    from .model import Project
    project = Project.model_validate(record["project"])
    snapshot = cache / "history" / f"{project.revision:08d}.json"
    if snapshot.exists() and checksum(json.loads(snapshot.read_text())) != checksum(record["project"]):
        raise ValueError(f"Immutable revision {project.revision} differs from save journal; original files retained")
    write_json(snapshot, record["project"])
    if fault:
        fault("snapshot")
    write_json(root / "o3d.json", record["project"])
    if fault:
        fault("source")
    atomic_bytes(cache / "history.jsonl", b"".join((json.dumps(entry, sort_keys=True) + "\n").encode() for entry in record["history"]))
    if fault:
        fault("history")
    write_json(cache / "undo.json", record["undo"])
    if fault:
        fault("undo")
    journal.unlink()
    sync_directory(cache)
    return True
