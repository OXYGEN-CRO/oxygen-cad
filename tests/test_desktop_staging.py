import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("stage_desktop", Path(__file__).resolve().parents[1] / "scripts/stage_desktop.py")
if not Path(SPEC.origin).is_file():
    pytest.skip("Desktop staging is a host release-tool test; staging script is not installed in the runtime image", allow_module_level=True)
staging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(staging)


def test_staging_closure_tracks_new_source_without_local_data(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text('FROM python\nCOPY src/ src/\nCOPY frontend/package*.json ./\nCOPY --from=engine /engine /engine\n')
    (tmp_path / ".dockerignore").write_text("projects\n")
    (tmp_path / "src/o3d/domains").mkdir(parents=True)
    (tmp_path / "src/o3d/domains/new.py").write_text("PUBLIC = True\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text("{}")
    (tmp_path / "projects/private").mkdir(parents=True)
    (tmp_path / "projects/private/secret.step").write_text("private")
    tracked = ["Dockerfile", ".dockerignore", "src/o3d/domains/new.py", "frontend/package.json", "projects/private/secret.step"]
    monkeypatch.setattr(staging, "tracked_files", lambda root: tracked)
    monkeypatch.setattr(staging, "ROOT", tmp_path)
    monkeypatch.setattr(staging, "DEST", tmp_path / "desktop/resources/runtime")
    result = staging.stage()
    assert "src/o3d/domains/new.py" in result["files"]
    assert not (staging.DEST / "projects").exists()
    first = result["image"]
    assert staging.stage()["image"] == first
    (tmp_path / "src/o3d/domains/new.py").write_text("PUBLIC = False\n")
    assert staging.stage()["image"] != first


def test_missing_copy_input_retains_prior_runtime(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM python\nCOPY missing/ missing/\n")
    dest = tmp_path / "resources/runtime"
    dest.mkdir(parents=True)
    (dest / "previous").write_text("usable")
    monkeypatch.setattr(staging, "tracked_files", lambda root: ["Dockerfile"])
    monkeypatch.setattr(staging, "ROOT", tmp_path)
    monkeypatch.setattr(staging, "DEST", dest)
    with pytest.raises(ValueError, match="no tracked source"):
        staging.stage()
    assert (dest / "previous").read_text() == "usable"


def test_staging_refuses_tracked_credentials_and_symlinks(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM python\nCOPY src/ src/\n")
    (tmp_path / ".dockerignore").write_text("")
    (tmp_path / "src").mkdir()
    secret = tmp_path / "src/.env.local"
    secret.write_text("SECRET=hidden")
    monkeypatch.setattr(staging, "tracked_files", lambda root: ["Dockerfile", ".dockerignore", "src/.env.local"])
    with pytest.raises(ValueError, match="credential"):
        staging.source_files(tmp_path)
    secret.unlink()
    (tmp_path / "src/public.py").symlink_to(tmp_path / "Dockerfile")
    monkeypatch.setattr(staging, "tracked_files", lambda root: ["Dockerfile", ".dockerignore", "src/public.py"])
    with pytest.raises(ValueError, match="Symbolic"):
        staging.source_files(tmp_path)
