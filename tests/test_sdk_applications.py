from __future__ import annotations

import inspect

from adaos.sdk import applications
from adaos.sdk.core.exporter import export


class _StubService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def plan_operation(self, *args, **kwargs):
        self.calls.append(("plan_operation", args, kwargs))
        return _Record({"operation_id": "appop.1", "plan_digest": "sha256:" + "a" * 64})

    def apply_operation(self, *args, **kwargs):
        self.calls.append(("apply_operation", args, kwargs))
        return _Record({"operation_id": args[0], "status": "succeeded"})


class _Record:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return dict(self.payload)


def test_sdk_application_mutations_forward_complete_review_context(monkeypatch) -> None:
    stub = _StubService()
    monkeypatch.setattr(applications, "_service", lambda: stub)

    plan = applications.plan_install(
        "app_recipes",
        release_digest="sha256:" + "b" * 64,
        expected_revision=3,
        actor_ref="skill:applications",
        subnet_ref="subnet:sn_home",
        idempotency_key="install-4",
    )
    result = applications.apply_operation(
        "appop.1",
        plan_digest=plan["plan_digest"],
        idempotency_key="install-4",
    )

    assert result["status"] == "succeeded"
    assert stub.calls[0] == (
        "plan_operation",
        ("app_recipes", "install"),
        {
            "release_digest": "sha256:" + "b" * 64,
            "expected_revision": 3,
            "actor_ref": "skill:applications",
            "subnet_ref": "subnet:sn_home",
            "idempotency_key": "install-4",
            "data_policy": "retain",
        },
    )
    assert stub.calls[1][0] == "apply_operation"


def test_sdk_application_surface_has_no_raw_path_or_process_parameters() -> None:
    forbidden = {"path", "filesystem_path", "command", "process", "git_credentials", "registry_path"}
    for name in applications.__all__:
        function = getattr(applications, name)
        assert forbidden.isdisjoint(inspect.signature(function).parameters), name


def test_application_sdk_is_discoverable_for_builder_context() -> None:
    metadata = export(level="std", query="application install prerelease", limit=40)
    names = {item["name"] for item in metadata["tools"]}

    assert "adaos.sdk.applications.plan_install" in names
    assert "adaos.sdk.applications.plan_update_track" in names
