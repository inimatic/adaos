from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.domain.personalization_access import SubjectRef
from adaos.sdk import access as sdk_access
from adaos.services.policy.application import bind_application, clear_application
from adaos.services.policy.caller import CallerAccessDenied, verified_caller


def test_application_context_role_capability_and_policy_explanation(monkeypatch, tmp_path) -> None:
    context = {
        "application_id": "family_tasks",
        "release_digest": "sha256:" + "a" * 64,
        "permission_profile_digest": "sha256:" + "b" * 64,
        "subject_ref": "user:masha",
    }
    grant = SimpleNamespace(
        grant_id="appgrant.1",
        status="active",
        reviewed_permission_profile_digest=context["permission_profile_digest"],
        application_roles=("editor",),
        permission_ceiling=("workspace.read", "workspace.write"),
        explicit_denies=(),
    )
    release = SimpleNamespace(
        permission_profile=SimpleNamespace(digest=context["permission_profile_digest"]),
        application_roles=(
            SimpleNamespace(role_id="editor", grants=("task.read", "task.complete")),
        ),
    )

    class _Store:
        def list_application_access_grants(self, application_id, *, subject_ref):
            assert (application_id, subject_ref) == ("family_tasks", "user:masha")
            return [grant]

        def get_release(self, application_id, release_digest):
            assert application_id == "family_tasks"
            assert release_digest == context["release_digest"]
            return release

    monkeypatch.setattr(
        "adaos.services.applications.get_application_service",
        lambda _state: SimpleNamespace(store=_Store()),
    )
    monkeypatch.setattr(
        sdk_access,
        "require_ctx",
        lambda _operation: SimpleNamespace(
            authority_state_dir=tmp_path,
            paths=SimpleNamespace(state_dir=lambda: tmp_path),
        ),
    )

    bind_application(context)
    try:
        with verified_caller(SubjectRef("user", "masha"), None):
            assert sdk_access.actor() == {"kind": "user", "id": "masha"}
            assert sdk_access.current_user() == {"kind": "user", "id": "masha"}
            assert sdk_access.application() == context
            assert sdk_access.require_app_role("editor")["grant_id"] == "appgrant.1"
            assert sdk_access.require_app_capability("task.complete")["capability"] == (
                "task.complete"
            )
            assert sdk_access.policy.explain()["permission_ceiling"] == [
                "workspace.read",
                "workspace.write",
            ]
            with pytest.raises(CallerAccessDenied, match="application_role_missing"):
                sdk_access.require_app_role("owner")
            with pytest.raises(CallerAccessDenied, match="application_capability_missing"):
                sdk_access.require_app_capability("task.assign")
    finally:
        clear_application()
