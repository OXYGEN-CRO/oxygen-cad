"""The actual shipped bracket, production App/API and read-only STEP verification."""
import os
from pathlib import Path
import tempfile

import uvicorn

from creation_server import creation_app
from o3d.model import Project


if __name__ == "__main__":
    port = int(os.environ.get("O3D_TEST_PORT", "8975"))
    if port == 8787:
        raise RuntimeError("The user's live studio is not a browser fixture")
    root = Path(__file__).resolve().parents[2]
    project = Project.model_validate_json((root / "examples/bracket.json").read_text())
    with tempfile.TemporaryDirectory(prefix="oxygen-existing-part-") as folder:
        uvicorn.run(creation_app(folder, project),
                    host=os.environ.get("O3D_TEST_HOST", "127.0.0.1"), port=port)
