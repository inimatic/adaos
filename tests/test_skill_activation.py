from __future__ import annotations

from pathlib import Path

from adaos.services.skill.activation import (
    allows_background_refresh,
    load_skill_activation_policy,
    load_skill_stream_receiver_patterns,
    stream_receiver_event_admission,
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


def test_load_stream_receivers_includes_nested_yjs_data_sources(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "subnet_env"
    skill_dir.mkdir(parents=True)
    (skill_dir / "webui.json").write_text(
        """
{
  "webio": {"receivers": {"subnet_env.events": {}}},
  "widgets": [
    {"dataSource": {"kind": "y", "path": "data/subnet_env/summary"}},
    {"dataSource": {"kind": "y", "path": "data/nodes/$node_id/subnet_env/overview"}}
  ]
}
""".strip(),
        encoding="utf-8",
    )

    assert load_skill_stream_receiver_patterns(tmp_path / "skills", "subnet_env") == (
        "subnet_env.events",
        "subnet_env.summary",
        "subnet_env.overview",
    )


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


def test_load_skill_stream_receiver_patterns_reads_webui_and_data_routes(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "webui.json").write_text(
        '{"webio":{"receivers":{"demo.notes":{},"demo.metrics":{}}}}',
        encoding="utf-8",
    )
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: demo_skill",
                "data_routes:",
                "- route: stream",
                "  receiver: demo.details.*",
                "- route: yjs",
                "  projection_slot: demo.summary",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert load_skill_stream_receiver_patterns(skills_root, "demo_skill") == (
        "demo.notes",
        "demo.metrics",
        "demo.details.*",
        "demo.summary",
    )


def test_subscription_event_admission_allows_webio_control_subscription_changed(monkeypatch) -> None:
    from types import SimpleNamespace

    from adaos.domain.workspace_manifest import SkillActivationPolicy, SkillActivationWhen
    import adaos.services.skill.activation as activation_module

    policy = SkillActivationPolicy(
        mode="lazy",
        background_refresh=False,
        when=SkillActivationWhen(
            scenarios_active=("web_desktop",),
            client_presence=True,
            webspace_scope="active",
        ),
    )
    evt = SimpleNamespace(
        type="webio.yjs.subscription.changed",
        payload={
            "webspace_id": "desktop",
            "slot": "infrastate.summary",
            "subscription_id": "sub-1",
        },
    )

    monkeypatch.setattr(activation_module, "_current_scenario_for_webspace", lambda _webspace_id: "web_desktop")

    admitted = subscription_event_admission(policy, evt, "webio.yjs.subscription.changed")
    assert admitted["allowed"] is True
    assert admitted["snapshot_request"] is False
    assert admitted["ui_control_request"] is True


def test_subscription_event_admission_allows_ui_control_when_scenario_is_not_loaded(monkeypatch) -> None:
    from types import SimpleNamespace

    from adaos.domain.workspace_manifest import SkillActivationPolicy, SkillActivationWhen
    import adaos.services.skill.activation as activation_module

    policy = SkillActivationPolicy(
        mode="lazy",
        background_refresh=False,
        when=SkillActivationWhen(
            scenarios_active=("web_desktop",),
            client_presence=True,
            webspace_scope="active",
        ),
    )
    evt = SimpleNamespace(
        type="webio.yjs.snapshot.requested",
        payload={
            "webspace_id": "desktop",
            "slot": "infrastate.summary",
        },
    )

    monkeypatch.setattr(activation_module, "_current_scenario_for_webspace", lambda _webspace_id: None)

    admitted = subscription_event_admission(policy, evt, "webio.yjs.snapshot.requested")
    assert admitted["allowed"] is True
    assert admitted["snapshot_request"] is True
    assert admitted["ui_control_request"] is True
    assert admitted["scenario_assumed_from_ui_control"] is True


def test_subscription_event_admission_allows_action_control_when_scenario_is_not_loaded(monkeypatch) -> None:
    from types import SimpleNamespace

    from adaos.domain.workspace_manifest import SkillActivationPolicy, SkillActivationWhen
    import adaos.services.skill.activation as activation_module

    policy = SkillActivationPolicy(
        mode="lazy",
        background_refresh=False,
        when=SkillActivationWhen(
            scenarios_active=("web_desktop",),
            client_presence=True,
            webspace_scope="active",
        ),
    )
    evt = SimpleNamespace(
        type="infrastate.action",
        payload={
            "id": "scenario_hard_pull",
            "name": "web_desktop",
            "request_id": "req-1",
            "webspace_id": "desktop",
        },
    )

    monkeypatch.setattr(activation_module, "_current_scenario_for_webspace", lambda _webspace_id: None)

    admitted = subscription_event_admission(policy, evt, "infrastate.action")
    assert admitted["allowed"] is True
    assert admitted["snapshot_request"] is False
    assert admitted["ui_control_request"] is True
    assert admitted["scenario_assumed_from_ui_control"] is True


def test_stream_receiver_event_admission_rejects_foreign_receiver() -> None:
    from types import SimpleNamespace

    foreign_evt = SimpleNamespace(
        type="webio.stream.snapshot.requested",
        payload={"receiver": "browsers.summary"},
    )
    own_evt = SimpleNamespace(
        type="webio.stream.snapshot.requested",
        payload={"receiver": "demo.details.42"},
    )

    denied = stream_receiver_event_admission(("demo.notes", "demo.details.*"), foreign_evt, "webio.stream.snapshot.requested")
    admitted = stream_receiver_event_admission(("demo.notes", "demo.details.*"), own_evt, "webio.stream.snapshot.requested")

    assert denied["allowed"] is False
    assert denied["reason"] == "stream_receiver_not_declared"
    assert admitted["allowed"] is True
    assert admitted["matched_pattern"] == "demo.details.*"
