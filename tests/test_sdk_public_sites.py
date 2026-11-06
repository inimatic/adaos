from __future__ import annotations

import pytest

from adaos.sdk import public_sites


class _Skill:
    name = "site_studio_skill"


class _SkillContext:
    @staticmethod
    def get():
        return _Skill()


class _Context:
    skill_ctx = _SkillContext()


def _bind_current_skill(monkeypatch) -> None:
    monkeypatch.setattr(public_sites, "require_ctx", lambda: _Context())
    monkeypatch.setattr(
        public_sites.access,
        "invocation",
        lambda: {"request_id": "request-1", "webspace_id": "desktop"},
    )


def test_public_site_sdk_uses_owner_profile_and_exact_prepare_identity(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)
    calls = []

    def call(tool_id, **kwargs):
        calls.append((tool_id, kwargs))
        return {
            "ok": True,
            "response": {
                "ok": True,
                "result": {
                    "publication": {
                        "status": "candidate_ready",
                        "can_publish": True,
                    }
                },
            },
        }

    monkeypatch.setattr(public_sites, "call_local_root_mcp_tool", call)

    result = public_sites.prepare_publication(
        scenario_id="site_studio",
        skill_id="site_studio_skill",
        webspace_id="desktop",
        project_id="adaos-site",
        expected_revision_id="draft-000117",
        expected_source_digest="sha256:" + "a" * 64,
    )

    assert result == {"status": "candidate_ready", "can_publish": True}
    assert calls[0][0] == "public_sites.prepare"
    assert calls[0][1]["capability_profile"] == "PublicSitesOperator"
    assert calls[0][1]["actor"] == "skill:site_studio_skill"
    assert calls[0][1]["timeout_seconds"] == 300.0
    assert calls[0][1]["arguments"]["expected_revision_id"] == "draft-000117"
    assert calls[0][1]["arguments"]["webspace_id"] == "desktop"


def test_public_site_sdk_rejects_failed_root_envelope(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)
    monkeypatch.setattr(
        public_sites,
        "call_local_root_mcp_tool",
        lambda *args, **kwargs: {
            "ok": True,
            "response": {
                "ok": False,
                "error": {"message": "publication candidate is stale"},
            },
        },
    )

    try:
        public_sites.publication_status(
            scenario_id="site_studio",
            skill_id="site_studio_skill",
            webspace_id="desktop",
            project_id="adaos-site",
        )
    except RuntimeError as error:
        assert str(error) == "publication candidate is stale"
    else:  # pragma: no cover
        raise AssertionError("failed Root envelope must not be accepted")


def test_public_site_sdk_rejects_another_skill_identity(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)

    with pytest.raises(PermissionError, match="current skill"):
        public_sites.publication_status(
            scenario_id="site_studio",
            skill_id="another_skill",
            webspace_id="desktop",
            project_id="adaos-site",
        )


def test_public_site_sdk_rejects_another_webspace_identity(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)

    with pytest.raises(PermissionError, match="addressed webspace"):
        public_sites.publication_status(
            scenario_id="site_studio",
            skill_id="site_studio_skill",
            webspace_id="another-space",
            project_id="adaos-site",
        )


def test_public_site_sdk_requests_domain_through_the_domain_plane(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)
    calls = []

    def call(tool_id, **kwargs):
        calls.append((tool_id, kwargs))
        return {
            "ok": True,
            "response": {
                "ok": True,
                "result": {"domain": {"hostname": "site.example", "status": "pending"}},
            },
        }

    monkeypatch.setattr(public_sites, "call_local_root_mcp_tool", call)

    result = public_sites.request_domain(
        scenario_id="site_studio",
        skill_id="site_studio_skill",
        webspace_id="desktop",
        project_id="adaos-site",
        hostname="site.example",
    )

    assert result == {"hostname": "site.example", "status": "pending"}
    assert calls[0][0] == "public_sites.domains.request"
    assert calls[0][1]["capability_profile"] == "PublicSitesOperator"
    assert calls[0][1]["arguments"]["hostname"] == "site.example"


def test_public_site_sdk_reads_separate_service_entitlement(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)
    calls = []

    def call(tool_id, **kwargs):
        calls.append((tool_id, kwargs))
        return {
            "ok": True,
            "response": {
                "ok": True,
                "result": {
                    "entitlement": {
                        "status": "limited",
                        "features": {"publication": {"status": "suspended"}},
                    }
                },
            },
        }

    monkeypatch.setattr(public_sites, "call_local_root_mcp_tool", call)

    result = public_sites.entitlement_status(
        scenario_id="site_studio",
        skill_id="site_studio_skill",
        webspace_id="desktop",
    )

    assert result["status"] == "limited"
    assert calls[0][0] == "public_sites.entitlement.status"
    assert "project_id" not in calls[0][1]["arguments"]


def test_public_site_sdk_exposes_reviewed_upgrade_and_rollback_tools(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)
    calls = []

    def call(tool_id, **kwargs):
        calls.append((tool_id, kwargs))
        result_key = "upgrade" if "upgrade" in tool_id else "publication"
        return {
            "ok": True,
            "response": {
                "ok": True,
                "result": {result_key: {"operation_id": "retained"}},
            },
        }

    monkeypatch.setattr(public_sites, "call_local_root_mcp_tool", call)
    common = {
        "scenario_id": "site_studio",
        "skill_id": "site_studio_skill",
        "webspace_id": "desktop",
        "project_id": "adaos-site",
    }
    prepared = public_sites.prepare_component_upgrade(**common)
    decided = public_sites.decide_component_upgrade(
        **common,
        review_id="psupgrade_" + "a" * 32,
        plan_digest="sha256:" + "b" * 64,
        decision="approve",
        confirmed=True,
    )
    rollback = public_sites.prepare_rollback(**common)

    assert prepared == decided == rollback == {"operation_id": "retained"}
    assert [item[0] for item in calls] == [
        "public_sites.components.upgrade.prepare",
        "public_sites.components.upgrade.decide",
        "public_sites.rollback.prepare",
    ]
    assert calls[1][1]["arguments"]["decision"] == "approve"
    assert calls[2][1]["timeout_seconds"] == 300.0


def test_public_site_sdk_authoring_admission_is_separate_and_metered(monkeypatch) -> None:
    _bind_current_skill(monkeypatch)
    calls = []

    def call(tool_id, **kwargs):
        calls.append((tool_id, kwargs))
        return {
            "ok": True,
            "response": {
                "ok": True,
                "result": {
                    "admission": {
                        "status": "available",
                        "usage": {"llm_requests_24h": 3},
                    }
                },
            },
        }

    monkeypatch.setattr(public_sites, "call_local_root_mcp_tool", call)
    result = public_sites.authorize_authoring(
        scenario_id="site_studio",
        skill_id="site_studio_skill",
        webspace_id="desktop",
        project_id="adaos-site",
        operation="llm.request",
        amount=1,
    )

    assert result["usage"]["llm_requests_24h"] == 3
    assert calls[0][0] == "public_sites.authoring.authorize"
    assert calls[0][1]["arguments"]["operation"] == "llm.request"
    assert calls[0][1]["arguments"]["amount"] == 1
