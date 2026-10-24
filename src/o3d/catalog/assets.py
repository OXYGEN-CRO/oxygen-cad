from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .db import Catalog
from .http import Fetcher


def fetch_asset(catalog: Catalog, asset_id: str, destination: Path, fetcher: Fetcher | None = None):
    asset = catalog.conn.execute("SELECT * FROM assets WHERE id=%s AND active", (asset_id,)).fetchone()
    if not asset:
        raise KeyError(asset_id)
    catalog.require_stored(catalog.get(str(asset["part_id"]))["provider_id"])
    if not asset["download_allowed"] or asset["license"] == "unknown" or not asset["license_url"]:
        raise ValueError("This asset's download/reuse permission has not been established")
    parsed = urlsplit(asset["url"])
    # Current connectors only permit pinned geometry from these open repositories.
    repositories = {"adafruit/Adafruit_CAD_Parts", "earthtojake/step.parts"}
    path = parsed.path.removeprefix("/media") if parsed.hostname == "media.githubusercontent.com" else parsed.path
    bits = path.strip("/").split("/")
    if (parsed.hostname not in {"raw.githubusercontent.com", "media.githubusercontent.com"}
        or len(bits) < 4 or "/".join(bits[:2]) not in repositories or not re.fullmatch(r"[a-f0-9]{40}", bits[2])):
        raise ValueError("Asset is not a pinned file in an approved source repository")
    name = Path(unquote(parsed.path)).name
    # Stable ID prefix avoids collisions between different models with the same filename.
    output = destination.resolve() / f"{asset_id}-{name}"
    owned = fetcher is None
    fetcher = fetcher or Fetcher()
    try:
        if asset["object_key"]:
            data = catalog.objects.read(asset["object_key"])
        else:
            data = fetcher.get(asset["url"], allowed_hosts={"raw.githubusercontent.com", "media.githubusercontent.com"}, max_bytes=50 * 1024 * 1024)
        digest = hashlib.sha256(data).hexdigest()
        if asset["sha256"] and digest != asset["sha256"]:
            raise ValueError("Downloaded CAD checksum does not match the source")
        if asset["format"] in {"step", "stp"} and not data.lstrip().startswith(b"ISO-10303-21;"):
            raise ValueError("Source is not a STEP exchange file (possibly a Git LFS pointer)")
        license_data = fetcher.get(asset["license_url"], allowed_hosts={"raw.githubusercontent.com"}, max_bytes=200_000)
        if asset["license"] == "MIT" and b"Permission is hereby granted" not in license_data:
            raise ValueError("Source no longer supplies the expected MIT license")
        _, key = catalog.objects.put(data)
        license_digest, license_key = catalog.objects.put(license_data)
        catalog.conn.execute("UPDATE assets SET object_key=%s,sha256=%s,byte_size=%s,downloaded_at=now() WHERE id=%s",
            (key, digest, len(data), asset_id))
        destination.mkdir(parents=True, exist_ok=True)
        # Never overwrite an existing differing local asset or a symlink.
        if output.exists() or output.is_symlink():
            if output.is_symlink() or output.read_bytes() != data:
                raise ValueError("Destination already contains a different file")
        else:
            with output.open("xb") as stream:
                stream.write(data)
        license_path = output.with_name(output.name + ".LICENSE.txt")
        if license_path.is_symlink():
            raise ValueError("License destination must not be a symlink")
        license_path.write_bytes(license_data)
        provenance = {"part_id": str(asset["part_id"]), "asset_id": asset_id, "url": asset["url"],
            "sha256": digest, "license": asset["license"], "license_url": asset["license_url"],
            "license_sha256": license_digest, "license_object_key": license_key,
            "attribution": asset["attribution"], "scope": asset["scope"], "path": str(output)}
        sidecar = output.with_name(output.name + ".catalog.json")
        if sidecar.is_symlink():
            raise ValueError("Provenance destination must not be a symlink")
        sidecar.write_text(json.dumps(provenance, indent=2) + "\n")
        return provenance
    finally:
        if owned:
            fetcher.close()


def import_part(catalog: Catalog, asset_id: str, project: Path, expected_revision: int):
    from ..store import Store, write_json
    store = Store(project)
    asset = catalog.conn.execute("SELECT * FROM assets WHERE id=%s AND active", (asset_id,)).fetchone()
    if not asset or asset["format"] not in {"step", "stp"}:
        raise ValueError("Project reference import requires a STEP asset")
    provenance = fetch_asset(catalog, asset_id, store.cache / "catalog-assets")
    part = catalog.get(str(asset["part_id"]))
    state = store.import_step(Path(provenance["path"]).read_bytes(), Path(provenance["path"]).name, expected_revision)
    reference = state.references[-1]
    write_json(store.cache / "catalog-links" / f"{reference.id}.json", {**provenance,
        "reference_id": reference.id, "revision": state.revision, "kind": part["procurement"],
        "compatibility": "unverified"})
    return {"project_revision": state.revision, "reference_id": reference.id, "provenance": provenance,
        "compatibility": "unverified"}
