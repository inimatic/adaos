from __future__ import annotations

from pathlib import Path

from adaos.services.skill.activation import (
    allows_background_refresh,
    load_skill_activation_policy,
    subscription_event_admission,
    subscription_strategy_for_policy,
)


def test_load_skill_activation_policy_uses_registry_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "infrascope_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: Infrascope",
                "version: '0.9.0'",
                "runtime:",
                "  activation:",
                "    mode: lazy",
                "    startup_allowed: false",
                "    background_refresh: false",
                "    when:",
                "      scenarios_active:",
                "        - infrascope",
                "      client_presence: true",
                "      webspace_scope: active",
                "",
            ]
        ),
        encoding="utf-8",
    )

    policy = load_skill_activation_policy(workspace, "infrascope_skill")

    assert policy is not None
    assert policy.mode == "lazy"
    assert policy.when.scenarios_active == ("infrascope",)
    assert policy.when.client_presence is True
    assert policy.when.webspace_scope == "active"
    assert subscription_strategy_for_policy(policy) == "early_cheap_handlers"


def test_allows_background_refresh_respects_policy_guards() -> None:
    workspace = Path(".")
    del workspace  # keep the test explicit about not using filesystem state

    from adaos.domain.workspace_manifest import SkillActivationPolicy, SkillActivationWhen

    policy = SkillActivationPolicy(
        mode="lazy",
        startup_allowed=False,
        background_refresh=True,
        when=SkillActivationWhen(
            scenarios_active=("infrascope",),
            client_presence=True,
            webspace_scope="active",
        ),
    )

    assert allows_background_refresh(policy, startup=True, scenario_active=True, client_present=True, webspace_is_target=True) is False
    assert allows_background_refresh(policy, startup=False, scenario_active=False, client_present=True, webspace_is_target=True) is False
    assert allows_background_refresh(policy, startup=False, scenario_active=True, client_present=False, webspace_is_target=True) is False
    assert allows_background_refresh(policy, startup=False, scenario_active=True, client_present=True, webspace_is_target=False) is False
    assert allows_background_refresh(policy, startup=False, scenario_active=True, client_present=True, webspace_is_target=True) is True


def test_subscription_event_admission_respects_active_scenario(monkeypatch) -> None:
    from types import SimpleNamespace

    from adaos.domain.workspace_manifest import SkillActivationPolicy, SkillActivationWhen
    import adaos.services.skill.activation as activation_module

    policy = SkillActivationPolicy(
        mode="lazy",
        background_refresh=False,
        when=SkillActivationWhen(
            scenarios_active=("new_face_vision_scenario",),
            client_presence=True,
            webspace_scope="active",
        ),
    )
    evt = SimpleNamespace(
        type="webio.stream.snapshot.requested",
        payload={"webspace_id": "desktop", "receiver": "new_face_vision.progress"},
    )

    monkeypatch.setattr(activation_module, "_current_scenario_for_webspace", lambda _webspace_id: "web_desktop")

    denied = subscription_event_admission(policy, evt, "webio.stream.snapshot.requested")
    assert denied["allowed"] is False
    assert denied["reason"] == "scenario_not_active"

    monkeypatch.setattr(
        activation_module,
        "_current_scenario_for_webspace",
        lambda _webspace_id: "new_face_vision_scenario",
    )

    admitted = subscription_event_admission(policy, evt, "webio.stream.snapshot.requested")
    assert admitted["allowed"] is True
    assert admitted["snapshot_request"] is True
