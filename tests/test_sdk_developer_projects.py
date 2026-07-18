from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from adaos.sdk.developer import projects


@pytest.fixture
def dev_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    scenarios.mkdir()
    monkeypatch.setattr(projects, "_roots", lambda: (skills.resolve(), scenarios.resolve()))
    return skills, scenarios


def test_local_project_discovery_and_bounded_file_access(dev_roots) -> None:
    _skills, scenarios = dev_roots
    root = scenarios / "builder"
    root.mkdir()
    (root / "scenario.yaml").write_text(
        "id: builder\ntitle: Builder\nversion: 0.1.0\ndepends: [builder_control_skill]\n",
        encoding="utf-8",
    )
    (root / "builder_memory.md").write_text("initial", encoding="utf-8")

    listed = projects.list_projects(kind="scenario")
    described = projects.describe("scenario", "builder")
    before = projects.read_file("scenario", "builder", "builder_memory.md")
    written = projects.write_file("scenario", "builder", "builder_memory.md", "updated")
    after = projects.read_file("scenario", "builder", "builder_memory.md")

    assert listed[0]["id"] == "builder"
    assert listed[0]["depends"] == ["builder_control_skill"]
    assert described["title"] == "Builder"
    assert before["content"] == "initial"
    assert written["size_bytes"] == 7
    assert after["content"] == "updated"


def test_project_files_block_escape_managed_state_and_binary(dev_roots) -> None:
    _skills, scenarios = dev_roots
    (scenarios / "builder").mkdir()

    with pytest.raises(projects.DeveloperProjectError, match="outside project root"):
        projects.write_file("scenario", "builder", "../outside.md", "bad")
    with pytest.raises(projects.DeveloperProjectError, match="managed_state_file"):
        projects.write_file("scenario", "builder", "prompt_state.json", "{}")
    with pytest.raises(projects.DeveloperProjectError, match="unsupported_file_type"):
        projects.write_file("scenario", "builder", "asset.png", "bad")


def test_read_file_reports_truncation(dev_roots) -> None:
    _skills, scenarios = dev_roots
    root = scenarios / "builder"
    root.mkdir()
    (root / "notes.md").write_text("abcdefgh", encoding="utf-8")

    result = projects.read_file("scenario", "builder", "notes.md", max_bytes=4)

    assert result["content"] == "abcd"
    assert result["truncated"] is True
    assert result["editable"] is False


@dataclass
class _Result:
    name: str
    path: Path
    commit: str | None = None


class _DeveloperService:
    def create_scenario(self, name, template=None):
        return _Result(name=name, path=Path(f"/dev/scenarios/{name}"))

    def push_scenario(self, name, *, message=None, metadata=None):
        return _Result(name=name, path=Path(f"/dev/scenarios/{name}"), commit="abc123")

    def update_scenario(self, name):
        return _Result(name=name, path=Path(f"/dev/scenarios/{name}"), commit="def456")

    def publish_scenario(self, name, **_kwargs):
        return _Result(name=name, path=Path(f"/workspace/scenarios/{name}"))


def test_lifecycle_results_are_plain_json_values(monkeypatch) -> None:
    monkeypatch.setattr(projects, "_service", lambda: _DeveloperService())

    created = projects.create("scenario", "builder")
    pushed = projects.push("scenario", "builder", message="checkpoint")
    updated = projects.update("scenario", "builder")
    published = projects.publish("scenario", "builder", dry_run=True)

    assert created["name"] == "builder"
    assert Path(created["path"]).parts[-3:] == ("dev", "scenarios", "builder")
    assert created["commit"] is None
    assert pushed["commit"] == "abc123"
    assert updated["commit"] == "def456"
    assert Path(published["path"]).parts[-3:] == ("workspace", "scenarios", "builder")
