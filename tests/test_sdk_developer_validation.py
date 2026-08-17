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
