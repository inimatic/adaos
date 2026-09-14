from pathlib import Path
import json

import pytest

from adaos.services.codex_profiles import execution_profile_from_command, normalize_codex_profile
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker


def test_profile_retains_only_execution_choices():
    assert normalize_codex_profile({"model": "gpt-5.4", "reasoning_effort": "low", "token": "secret"}) == {
        "provider": "openai-codex-cli", "model": "gpt-5.4", "reasoning_effort": "low"
    }


@pytest.mark.parametrize("profile", [{}, {"model": "--unsafe"}, {"model": "x", "provider": "other"},
                                    {"model": "x", "reasoning_effort": "arbitrary"}])
def test_profile_rejects_ambiguous_settings(profile):
    with pytest.raises(ValueError):
        normalize_codex_profile(profile)


def test_execution_receipt_uses_actual_command_without_credentials(tmp_path: Path):
    command = ("codex", "exec", "--model", "gpt-5.4", "--config", 'model_reasoning_effort="low"',
               "--config", 'secret="credential"')
    LocalSkillFactoryWorker._record_codex_attempt(tmp_path, CodexRunResult(0, command=command), attempt=0)
    receipt = json.loads((tmp_path / "codex-execution-profile.json").read_text(encoding="utf-8"))
    assert receipt["model"] == "gpt-5.4"
    assert receipt["reasoning_effort"] == "low"
    assert "credential" not in json.dumps(receipt)
    assert execution_profile_from_command(("codex", "exec"))["model_source"] == "runner_default_unresolved"
