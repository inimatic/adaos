import pytest

from adaos.services.builder.model_controls import reasoning_for_model, supports_sampling


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna"])
def test_gpt6_preserves_requested_high_and_omits_sampling(model):
    assert reasoning_for_model(model, "high") == {"effort": "high"}
    assert not supports_sampling(model)


@pytest.mark.parametrize("effort", ["none", "minimal", "typo"])
def test_astra_rejects_unsupported_effort_instead_of_provider_default(effort):
    with pytest.raises(ValueError):
        reasoning_for_model("gpt-6-astra", effort)


def test_legacy_and_nonreasoning_model_controls_are_preserved():
    assert reasoning_for_model("gpt-5", "minimal") == {"effort": "minimal"}
    assert reasoning_for_model("gpt-4.1", "high") is None
    assert reasoning_for_model("gpt-5-pro", "high") is None
    assert supports_sampling("gpt-4.1")
