from __future__ import annotations

from types import SimpleNamespace

from adaos.sdk.developer import validation


def test_developer_validation_requires_narrow_capability_and_calls_service(monkeypatch) -> None:
    ctx = SimpleNamespace()
    admitted: list[str] = []
    calls: list[tuple[object, str, bool, bool, bool]] = []
    monkeypatch.setattr(validation, "require_ctx", lambda _operation: ctx)
    monkeypatch.setattr(
        validation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )

    def fake_validate(context, project_id, *, strict, probe_tools, run_packaged_tests):
        calls.append((context, project_id, strict, probe_tools, run_packaged_tests))
        return {"ok": True, "digest": "sha256:" + "1" * 64}

    monkeypatch.setattr(
        "adaos.services.developer_project_validation.validate_dev_skill",
        fake_validate,
    )

    result = validation.validate_skill("candidate", run_tests=False)

    assert result["ok"] is True
    assert admitted == ["builder.project_validation"]
    assert calls == [(ctx, "candidate", True, True, False)]


def test_developer_invocation_reuses_the_same_narrow_capability(monkeypatch) -> None:
    ctx = SimpleNamespace()
    admitted: list[str] = []
    monkeypatch.setattr(validation, "require_ctx", lambda _operation: ctx)
    monkeypatch.setattr(
        validation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )
    monkeypatch.setattr(
        "adaos.services.developer_project_validation.activate_dev_skill",
        lambda context, project_id: {"ok": context is ctx, "project_ref": f"skill:{project_id}"},
    )
    monkeypatch.setattr(
        "adaos.services.developer_project_validation.invoke_dev_skill",
        lambda context, project_id, operation_id, arguments, timeout=None: {
            "ok": context is ctx,
            "project_id": project_id,
            "operation_id": operation_id,
            "arguments": arguments,
            "timeout": timeout,
        },
    )

    activated = validation.activate_skill("candidate")
    invoked = validation.invoke_skill("candidate", "smoke", {"seed": 17}, timeout=30)

    assert activated["ok"] is True
    assert invoked["operation_id"] == "smoke"
    assert admitted == ["builder.project_validation", "builder.project_validation"]
