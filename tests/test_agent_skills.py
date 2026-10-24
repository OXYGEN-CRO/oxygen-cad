"""Exercise discovery, host setup and preservation of existing agent files."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agent_skills", ROOT / "scripts/agent_skills.py")
skills = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skills)


def test_plugin_manifests_discover_the_same_skills():
    discovered = []
    for host in (".claude-plugin", ".codex-plugin"):
        manifest = json.loads((ROOT / host / "plugin.json").read_text())
        discovered.append({p.parent.name for p in (ROOT / manifest["skills"]).glob("*/SKILL.md")})
    assert discovered[0] == discovered[1] == set(skills.bundled())
    marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert marketplace["plugins"][0]["source"] == "./"


@pytest.mark.parametrize("agent,folder", [("codex", ".agents"), ("claude", ".claude"), ("portable", ".agents")])
def test_host_setup_works_without_cad_or_docker(tmp_path, agent, folder):
    workspace = tmp_path / "a workspace with spaces"
    command = ["sh", str(ROOT / "o3d"), "setup", "--agent", agent, "--workspace", str(workspace), "--json"]
    preview = json.loads(subprocess.check_output(command + ["--dry-run"], cwd=tmp_path))
    assert preview["dry_run"] and not workspace.exists()
    result = json.loads(subprocess.check_output(command, cwd=tmp_path))
    assert Path(result["target"]) == workspace / folder / "skills"
    for item in result["skills"]:
        installed = Path(item["path"])
        assert (installed / "SKILL.md").is_file()
        assert json.loads((installed / "runtime.json").read_text())["repository"] == str(ROOT)
    again = json.loads(subprocess.check_output(command, cwd=tmp_path))
    assert all(item["action"] == "unchanged" for item in again["skills"])


def test_modified_or_unmanaged_skill_prevents_partial_update(tmp_path):
    target = tmp_path / "skills"
    other = target / "other-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("User's other skill")
    last = sorted(skills.bundled())[-1]
    conflict = target / last
    conflict.mkdir()
    (conflict / "SKILL.md").write_text("User's existing skill")
    with pytest.raises(ValueError, match="unmanaged"):
        skills.install(target)
    assert {p.name for p in target.iterdir()} == {"other-skill", last}
    assert (conflict / "SKILL.md").read_text() == "User's existing skill"
    (conflict / "SKILL.md").unlink()
    conflict.rmdir()
    skills.install(target)
    (conflict / "SKILL.md").write_text("Local edits")
    with pytest.raises(ValueError, match="modified"):
        skills.install(target)
    assert (conflict / "SKILL.md").read_text() == "Local edits"
    assert (other / "SKILL.md").read_text() == "User's other skill"


def test_update_owned_skill_and_reject_symlinks(tmp_path, monkeypatch):
    target = tmp_path / "skills"
    skills.install(target)
    updated = skills.bundled()
    name = next(iter(updated))
    updated[name]["SKILL.md"] += b"\nNew version\n"
    monkeypatch.setattr(skills, "bundled", lambda: updated)
    result = skills.install(target)
    assert result["skills"][0]["action"] == "update"
    assert (target / name / "SKILL.md").read_bytes() == updated[name]["SKILL.md"]
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        skills.install(linked)
    (target / name / "unowned-link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        skills.install(target)
