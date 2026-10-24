"""Content-addressed local object store; replace with S3 behind this interface."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class Objects:
    def __init__(self, root: Path | None = None):
        self.root = (root or Path(os.environ.get("O3D_CATALOG_DATA", Path.home() / ".local/share/oxygen-parts"))).resolve()

    def put(self, data: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(data).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing object checksum mismatch")
        if not path.exists():
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".object-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return digest, key

    def read(self, key: str) -> bytes:
        import re
        if not re.fullmatch(r"sha256/[0-9a-f]{2}/[0-9a-f]{64}", key):
            raise ValueError("Invalid object key")
        data = (self.root / key).read_bytes()
        if hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[1]:
            raise ValueError("Object checksum mismatch")
        return data
