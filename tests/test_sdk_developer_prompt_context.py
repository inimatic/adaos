from __future__ import annotations

from pathlib import Path

import pytest

from adaos.sdk.developer import projects, prompt_context


@pytest.fixture
def project_root(monkeypatch, tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    scenarios.mkdir()
    root = scenarios / "builder"
    root.mkdir()
    (root / "scenario.yaml").write_text("id: builder\n", encoding="utf-8")
    monkeypatch.setattr(projects, "_roots", lambda: (skills.resolve(), scenarios.resolve()))
    return root


def test_prompt_context_round_trip(project_root: Path) -> None:
    saved = prompt_context.save_base("scenario", "builder", "# Builder specification")
    appended = prompt_context.append_addendum(
        "scenario",
        "builder",
        "Keep the 029 layout.",
        iteration_ref="revision-030",
    )
    selected = prompt_context.set_preferences(
        "scenario",
        "builder",
        llm_model="gpt-5",
        llm_provider="openai",
        llm_profile={"id": "gpt-5", "provider": "openai"},
        workflow_state="prototype",
        archived=True,
    )
    loaded = prompt_context.get("scenario", "builder")

    assert saved["base_tz"] == "# Builder specification"
    assert appended["tz_addenda"][0]["iteration_ref"] == "revision-030"
    assert selected["archived"] is True
    assert loaded["builder_llm_model"] == "gpt-5"
    assert loaded["workflow_state"] == "prototype"
    assert loaded["tz_addenda"][0]["text"] == "Keep the 029 layout."
    assert (project_root / "tz" / "base_tz.md").read_text(encoding="utf-8") == "# Builder specification"


def test_prompt_context_rejects_empty_addendum(project_root: Path) -> None:
    with pytest.raises(projects.DeveloperProjectError, match="addendum text is required"):
        prompt_context.append_addendum("scenario", "builder", "  ")
