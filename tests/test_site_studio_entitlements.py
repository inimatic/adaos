from __future__ import annotations

from datetime import datetime, timedelta, timezone
import subprocess
import sys

import pytest

from adaos.services.root_mcp.model import RootMcpToolError
from adaos.services.site_studio_entitlements import SiteStudioEntitlementService


SCOPE_REFS = ("subject:subnet:owner-subnet",)


def test_entitlement_service_can_be_imported_before_root_mcp_package() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from adaos.services.site_studio_entitlements import "
                "SiteStudioEntitlementService; "
                "from adaos.services.root_mcp.model import RootMcpToolError; "
                "assert SiteStudioEntitlementService and RootMcpToolError"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _grant(service: SiteStudioEntitlementService, **overrides):
    request = {
        "scope_kind": "subject",
        "scope_id": "subnet:owner-subnet",
        "environment": "prod",
        "scenario_id": "site_studio",
        "skill_id": "site_studio_skill",
        "actor": "user:owner",
        "reason": "Enable the external beta for the owner subnet.",
        "features": ["authoring", "publication", "custom_domains"],
    }
    request.update(overrides)
    return service.grant(**request)


def _authorize(service: SiteStudioEntitlementService, **overrides):
    request = {
        "scope_refs": SCOPE_REFS,
        "environment": "prod",
        "scenario_id": "site_studio",
        "skill_id": "site_studio_skill",
        "feature": "publication",
        "operation": "prepare",
    }
    request.update(overrides)
    return service.authorize(**request)


def test_entitlement_is_separate_scoped_and_fail_closed(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)

    status = service.status(
        scope_refs=SCOPE_REFS,
        environment="prod",
        scenario_id="site_studio",
        skill_id="site_studio_skill",
    )
    assert status["status"] == "unavailable"
    assert status["features"]["publication"]["reason_code"] == "site_studio_entitlement_required"
    with pytest.raises(RootMcpToolError) as error:
        _authorize(service)
    assert error.value.code == "site_studio_entitlement_required"

    granted = _grant(service)
    assert granted["schema"] == "adaos.site_studio.service_entitlement.v1"
    assert granted["scope"] == {"kind": "subject", "id": "subnet:owner-subnet"}
    assert granted["receipt"]["action"] == "grant"
    assert "subscription" not in granted
    assert "permission" not in granted

    assert _authorize(service)["features"]["publication"]["status"] == "available"
    with pytest.raises(RootMcpToolError) as mismatch:
        _authorize(service, scope_refs=("subject:subnet:another",))
    assert mismatch.value.code == "site_studio_entitlement_required"
    with pytest.raises(RootMcpToolError) as wrong_skill:
        _authorize(service, skill_id="arbitrary_installed_skill")
    assert wrong_skill.value.code == "site_studio_entitlement_required"


def test_quota_is_metered_idempotently_and_reports_stable_exhaustion(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(service, limits={"prepare_operations_1h": 1})

    _authorize(
        service,
        consume=True,
        idempotency_key="request-1",
        resource_id="project-1",
        actor="skill:site_studio_skill",
    )
    repeated = _authorize(
        service,
        consume=True,
        idempotency_key="request-1",
        resource_id="project-1",
        actor="skill:site_studio_skill",
    )
    assert repeated["usage"]["prepare_operations_1h"]["remaining"] == 0
    with pytest.raises(RootMcpToolError) as error:
        _authorize(
            service,
            consume=True,
            idempotency_key="request-2",
            resource_id="project-1",
        )
    assert error.value.code == "site_studio_quota_exhausted"
    assert error.value.details["quota"] == "prepare_operations_1h"


def test_llm_polling_meters_one_step_even_with_new_invocation_ids(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(service, limits={"llm_requests_24h": 1})
    request = dict(feature="authoring", operation="llm.request", consume=True,
                   actor="skill:site_studio_skill", resource_id="chat-job-0")

    for poll in range(32):
        result = _authorize(service, **request, idempotency_key=f"poll-{poll}")
        assert result["usage"]["llm_requests_24h"]["used"] == 1
    assert len(service._read()["usage_events"]) == 1

    with pytest.raises(RootMcpToolError) as error:
        _authorize(service, **{**request, "resource_id": "chat-job-1"}, idempotency_key="next-step")
    assert error.value.code == "site_studio_quota_exhausted"
    assert error.value.details["used"] == error.value.details["limit"] == 1
    assert error.value.details["resets_at"] == result["usage"]["llm_requests_24h"]["resets_at"]


def test_llm_legacy_poll_overcount_is_reconciled_without_rewriting_audit(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(service, limits={"llm_requests_24h": 2})
    _authorize(service, feature="authoring", operation="llm.request", consume=True,
               actor="skill:site_studio_skill", resource_id="job-0", idempotency_key="first")
    registry = service._read()
    for poll in range(31):
        event = {**registry["usage_events"][0], "event_id": f"legacy-{poll}",
                 "idempotency_key": f"poll-{poll}"}
        registry["usage_events"].append(event)
    service._commit(registry)
    before = service.path.read_bytes()

    status = _authorize(service, feature="authoring", operation="status")
    assert status["usage"]["llm_requests_24h"] == {
        "used": 1, "limit": 2, "remaining": 1,
        "resets_at": status["usage"]["llm_requests_24h"]["resets_at"],
    }
    assert service.path.read_bytes() == before
    _authorize(service, feature="authoring", operation="llm.request", consume=True,
               actor="skill:site_studio_skill", resource_id="job-1", idempotency_key="next")
    after = service._read()
    assert after["usage_events"][:-1] == registry["usage_events"]
    assert len(after["usage_events"]) == 33


def test_llm_charge_window_starts_at_first_admission_not_last_poll(tmp_path, monkeypatch) -> None:
    from adaos.services import site_studio_entitlements as module

    now = datetime(2026, 11, 16, 19, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_now", lambda: now)
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(service)
    registry = service._read()
    entitlement = next(iter(registry["entitlements"]))
    base = {"entitlement_id": entitlement, "operation": "llm.request",
            "quota": "llm_requests_24h", "amount": 1, "actor": "skill:site_studio_skill"}
    registry["usage_events"] = [
        {**base, "event_id": "poll", "resource_id": "old-step", "created_at": (now - timedelta(hours=1)).isoformat()},
        {**base, "event_id": "first", "resource_id": "old-step", "created_at": (now - timedelta(hours=25)).isoformat()},
        {**base, "event_id": "new", "resource_id": "new-step", "created_at": (now - timedelta(hours=2)).isoformat()},
    ]
    usage = service._usage(registry, entitlement_id=entitlement, limits={"llm_requests_24h": 200})
    assert usage["llm_requests_24h"]["used"] == 1
    assert usage["llm_requests_24h"]["resets_at"] == (now + timedelta(hours=22)).isoformat()


def test_llm_identity_is_scoped_and_non_llm_operations_still_count_separately(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(service)
    common = dict(feature="authoring", operation="llm.request", consume=True, resource_id="same-resource")
    for actor in ("skill:site_studio_skill", "skill:another"):
        _authorize(service, **common, actor=actor, idempotency_key=actor)
    for operation in ("compile", "prepare"):
        for poll in range(2):
            _authorize(service, operation=operation, consume=True, resource_id="same-resource",
                       idempotency_key=f"{operation}-{poll}")
    result = _authorize(service, operation="status")
    assert result["usage"]["llm_requests_24h"]["used"] == 2
    assert result["usage"]["compile_operations_1h"]["used"] == 2
    assert result["usage"]["prepare_operations_1h"]["used"] == 2

    # A second entitlement cannot reuse another entitlement's step identity.
    _grant(service, scope_id="subnet:other-subnet")
    other = _authorize(service, **common, scope_refs=("subject:subnet:other-subnet",),
                       actor="skill:site_studio_skill", idempotency_key="other")
    assert other["usage"]["llm_requests_24h"]["used"] == 1


def test_unidentified_and_batched_llm_usage_is_not_coalesced(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(service)
    for poll in range(2):
        _authorize(service, feature="authoring", operation="llm.request", consume=True,
                   idempotency_key=f"unidentified-{poll}")
        _authorize(service, feature="authoring", operation="llm.request", consume=True,
                   resource_id="batch", amount=2, idempotency_key=f"batch-{poll}")
    assert _authorize(service, operation="status")["usage"]["llm_requests_24h"]["used"] == 6


def test_expiry_revocation_and_emergency_switch_are_enforced(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    _grant(
        service,
        scope_id="subnet:expired",
        effective_from=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        effective_until=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )
    with pytest.raises(RootMcpToolError) as expired_error:
        _authorize(service, scope_refs=("subject:subnet:expired",))
    assert expired_error.value.code == "site_studio_entitlement_expired"

    active = _grant(service)
    service.set_status(
        active["entitlement_id"],
        status="revoked",
        actor="user:owner",
        reason="End the beta access.",
        expected_revision=active["revision"],
    )
    with pytest.raises(RootMcpToolError) as revoked:
        _authorize(service)
    assert revoked.value.code == "site_studio_entitlement_revoked"

    switched = SiteStudioEntitlementService(state_dir=tmp_path / "switch")
    _grant(switched)
    switched.set_switch(
        "publication",
        status="suspended",
        reason_code="site_studio_publication_emergency_suspension",
        actor="user:operator",
        reason="Provider integrity incident.",
    )
    with pytest.raises(RootMcpToolError) as suspended:
        _authorize(switched)
    assert suspended.value.code == "site_studio_publication_emergency_suspension"


def test_receipts_are_chained_and_registry_projection_is_sanitized(tmp_path) -> None:
    service = SiteStudioEntitlementService(state_dir=tmp_path)
    granted = _grant(service)
    changed = service.set_status(
        granted["entitlement_id"],
        status="suspended",
        actor="user:owner",
        reason="Review requested.",
        expected_revision=1,
        request_id="request-status",
    )
    registry = service._read()

    assert len(registry["receipts"]) == 2
    assert registry["receipts"][1]["previous_receipt_digest"] == registry["receipts"][0]["receipt_digest"]
    assert changed["receipt"]["actor"] == "user:owner"
    rendered = str(service.list_entitlements()).lower()
    assert "cloudflare" not in rendered
    assert "firebase" not in rendered
    assert "regru" not in rendered
    assert "token" not in rendered
