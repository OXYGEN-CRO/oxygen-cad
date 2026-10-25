"""Bounded polling metadata, separate from full saved geometry and native scenes.

The sidecar follows the exact build-file identity. Old caches are migrated once;
normal builds publish the sidecar while the full result is already in memory.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .recovery import write_json

SUMMARY_VERSION = 1
SUMMARY_FIELDS = (
    "key", "revision", "volume_mm3", "dimensions_mm", "bounds", "center_of_mass",
    "exact_solid_volume_mm3", "artifacts", "artifact_scope",
)


def _identity(path: Path):
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino}


def _path(source: Path):
    return source.with_name(source.stem + "-state.json")


def compact_build(result: dict) -> dict:
    """Do not retain per-triangle descriptors, even without their vertex arrays."""
    metadata = {name: result[name] for name in SUMMARY_FIELDS if name in result}
    metadata.update(faces=[], references=[], edges=[], geometry_payload="native_scene")
    engine = result.get("engine") or {}
    metadata["engine"] = {name: engine[name] for name in
                          ("engine", "language", "protocol", "upstream_commit") if name in engine}
    bodies = (result.get("scene") or {}).get("bodies", [])
    metadata["scene_summary"] = {"body_count": len(bodies),
                                 "geometry_kinds": dict(Counter(b.get("geometry_kind", "unknown") for b in bodies))}
    return metadata


def publish_build_summary(source: Path, result: dict, *, expected_identity=None):
    """Call only after publishing the full source build; source remains untouched."""
    identity = _identity(source)
    if expected_identity is not None and identity != expected_identity:
        raise ValueError("Build changed before publishing polling metadata; refresh again")
    summary = {"version": SUMMARY_VERSION, "source": identity, "build": compact_build(result)}
    write_json(_path(source), summary)
    return summary["build"]


def load_build_summary(source: Path, *, expected_key: str | None = None):
    """Use a stat-checked summary; migrate a historical full cache at most once.

    This is a derived cache, not an integrity boundary against filesystem owners.
    A changed/torn full build cannot borrow a summary from earlier file bytes.
    """
    if not source.is_file():
        return None
    identity = _identity(source)
    sidecar = _path(source)
    try:
        if sidecar.stat().st_size <= 1024 * 1024:
            cached = json.loads(sidecar.read_text())
            metadata = cached.get("build")
            if (cached.get("version") == SUMMARY_VERSION and cached.get("source") == identity
                    and isinstance(metadata, dict)
                    and (expected_key is None or metadata.get("key") == expected_key)):
                return metadata
    except (FileNotFoundError, ValueError, OSError):
        pass
    result = json.loads(source.read_text())
    if _identity(source) != identity:
        raise ValueError("Build changed while reading polling metadata; refresh again")
    if expected_key is not None and result.get("key") != expected_key:
        raise ValueError("Build geometry key does not match its cache directory")
    return publish_build_summary(source, result, expected_identity=identity)
