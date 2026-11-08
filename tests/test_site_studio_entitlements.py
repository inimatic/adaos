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
