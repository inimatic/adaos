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
