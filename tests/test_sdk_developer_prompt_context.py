from __future__ import annotations

from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor

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
    monkeypatch.setattr(
        projects, "_roots", lambda: (skills.resolve(), scenarios.resolve())
    )
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
    assert (project_root / "tz" / "base_tz.md").read_text(
        encoding="utf-8"
    ) == "# Builder specification"


def test_prompt_context_rejects_empty_addendum(project_root: Path) -> None:
    with pytest.raises(
        projects.DeveloperProjectError, match="addendum text is required"
    ):
        prompt_context.append_addendum("scenario", "builder", "  ")


def test_codex_preferences_are_separate_from_prototype_model(
    project_root: Path,
) -> None:
    prompt_context.set_preferences("scenario", "builder", llm_model="gpt-5")
    prompt_context.set_preferences(
        "scenario",
        "builder",
        codex_profile={"model": "gpt-5.4", "reasoning_effort": "low"},
    )
    loaded = prompt_context.get("scenario", "builder")
    assert loaded["builder_llm_model"] == "gpt-5"
    assert loaded["builder_codex_profile"]["model"] == "gpt-5.4"
    with pytest.raises(ValueError):
        prompt_context.set_preferences(
            "scenario", "builder", codex_profile={"model": ""}
        )
    assert (
        prompt_context.get("scenario", "builder")["builder_codex_profile"]
        == loaded["builder_codex_profile"]
    )


def test_prompt_context_uses_core_authority_not_stale_dev_mirror(project_root):
    from adaos.services.builder.workflow_authority import WorkflowAuthority
    from adaos.services.runtime_paths import current_state_dir

    prompt_context.set_preferences("scenario", "builder", llm_model="legacy-model")
    authority = WorkflowAuthority(current_state_dir())
    authority.adopt("scenario:builder", project_root / "prompt_state.json")
    state = authority._read(authority.resolve("scenario:builder"))
    state["builder_llm_model"] = "accepted-core-model"
    state["late_observation"] = {"paid": True}
    authority.write("scenario:builder", state)
    assert (
        prompt_context.get("scenario", "builder")["builder_llm_model"]
        == "accepted-core-model"
    )


def test_prompt_preferences_write_selected_core_without_losing_observations(
    project_root,
):
    from adaos.services.builder.workflow_authority import WorkflowAuthority
    from adaos.services.runtime_paths import current_state_dir

    prompt_context.set_preferences("scenario", "builder", llm_model="legacy-model")
    source = project_root / "prompt_state.json"
    original = source.read_bytes()
    authority = WorkflowAuthority(current_state_dir())
    authority.adopt("scenario:builder", source)
    state = authority._read(authority.resolve("scenario:builder"))
    state["late_observation"] = {"paid": True}
    authority.write("scenario:builder", state)
    prompt_context.set_preferences("scenario", "builder", llm_model="new-core-model")
    persisted = json.loads(
        authority.resolve("scenario:builder").read_text(encoding="utf-8")
    )
    assert persisted["builder_llm_model"] == "new-core-model"
    assert persisted["late_observation"] == {"paid": True}
    assert source.read_bytes() == original


@pytest.fixture
def adopted_context(project_root):
    from adaos.services.builder.workflow_authority import WorkflowAuthority
    from adaos.services.runtime_paths import current_state_dir

    prompt_context.set_preferences("scenario", "builder", llm_model="accepted-model")
    authority = WorkflowAuthority(current_state_dir())
    authority.adopt("scenario:builder", project_root / "prompt_state.json")
    return authority, project_root


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_adopted_prompt_context_never_falls_back_to_dev(adopted_context, failure):
    from adaos.services.builder.workflow_authority import WorkflowAuthorityRejected

    authority, root = adopted_context
    path = authority.resolve("scenario:builder")
    source_before = (root / "prompt_state.json").read_bytes()
    if failure == "missing":
        path.unlink()
    else:
        path.write_text("{corrupt", encoding="utf-8")
    for operation in (
        lambda: prompt_context.get("scenario", "builder"),
        lambda: prompt_context.set_preferences(
            "scenario", "builder", llm_model="replacement"
        ),
        lambda: prompt_context.save_base(
            "scenario", "builder", "Must not write source before refusal"
        ),
    ):
        with pytest.raises(WorkflowAuthorityRejected):
            operation()
    assert (root / "prompt_state.json").read_bytes() == source_before
    assert not (root / "tz" / "base_tz.md").exists()


def test_adopted_context_survives_entire_dev_directory_loss(adopted_context):
    authority, root = adopted_context
    root.rename(root.with_name("removed-dev-source"))
    assert (
        prompt_context.get("scenario", "builder")["builder_llm_model"]
        == "accepted-model"
    )
    prompt_context.set_preferences("scenario", "builder", llm_model="updated-model")
    assert (
        authority._read(authority.resolve("scenario:builder"))["builder_llm_model"]
        == "updated-model"
    )
    assert not root.exists()


def test_prompt_preferences_and_late_observation_serialize_without_whole_state_loss(
    adopted_context,
    monkeypatch,
):
    from adaos.services.builder.workflow import _LOCK

    authority, _ = adopted_context
    monkeypatch.setattr(
        prompt_context, "_authority", lambda *args: (authority, "scenario:builder")
    )

    def observation():
        with _LOCK:
            value = authority._read(authority.resolve("scenario:builder"))
            value["late_observation"] = {"paid": True}
            authority.write("scenario:builder", value)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(
                prompt_context.set_preferences,
                "scenario",
                "builder",
                llm_model="new-model",
            ),
            executor.submit(
                prompt_context.set_preferences,
                "scenario",
                "builder",
                llm_provider="openai.chatgpt",
            ),
            executor.submit(observation),
        ]
        for future in futures:
            future.result(timeout=30)
    result = prompt_context.get("scenario", "builder")
    assert result["builder_llm_model"] == "new-model"
    assert result["llm_provider"] == "openai.chatgpt"
    assert result["late_observation"] == {"paid": True}


def test_adopted_context_does_not_import_stale_base_text_from_dev(adopted_context):
    _, root = adopted_context
    (root / "tz").mkdir()
    (root / "tz" / "base_tz.md").write_text("Stale source text", encoding="utf-8")
    assert prompt_context.get("scenario", "builder")["base_tz"] == ""
