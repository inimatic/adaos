from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain.personalization_access import SubjectRef, UserProfile
from adaos.services.agent_context import get_ctx
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.skill.validation import SkillValidationService
from adaos.services.user.profile import UserProfileService


OWNER = SubjectRef("user", "owner")
MASHA = SubjectRef("user", "masha")


class _FakeKV:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value: object) -> None:
        self._data[key] = value


def _ctx(owner_id: str = "owner") -> SimpleNamespace:
    return SimpleNamespace(
        kv=_FakeKV(),
        bus=object(),
        settings=SimpleNamespace(owner_id=owner_id, subnet_id="family-subnet"),
    )


def _write_skill(root: Path, *, manifest_extra: list[str]) -> Path:
    skill_dir = root / "demo_skill"
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: demo_skill",
                "version: 0.1.0",
                "description: demo",
                "tools: []",
                *(manifest_extra or []),
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "handlers" / "main.py").write_text("", encoding="utf-8")
    return skill_dir


def test_phase8_user_private_profile_content_denies_owner_but_allows_metadata(tmp_path: Path) -> None:
    service = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"), owner=OWNER)
    service.put_profile(
        UserProfile(
            user_id="masha",
            display_name="Masha",
            settings={"favorite_color": "blue"},
        ),
        actor=MASHA,
    )

    classification = service.classify_data_zone("profile", subject=MASHA)
    assert classification["zone"] == "user_private"
    assert classification["rules"]["content_visible_to_owner_ui"] is False

    decision = service.require_user_private_content_access(
        actor=MASHA,
        action="profile.read.self",
        subject=MASHA,
        resource="user.profile",
    )
    assert decision.decision == "allow"

    with pytest.raises(PermissionError, match="private_content_subject_mismatch"):
        service.require_user_private_content_access(
            actor=OWNER,
            action="profile.read.self",
            subject=MASHA,
            resource="user.profile",
        )

    metadata = service.profile_metadata("masha", actor=OWNER)
    assert metadata["metadata_only"] is True
    assert metadata["content_visible"] is False
    assert metadata["display_name"] == "Masha"
    assert "settings" not in metadata
    assert service.list_audit(subject=MASHA, event_type="policy.deny")


def test_phase8_user_profile_service_denies_cross_user_profile_reads(tmp_path: Path) -> None:
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"), owner=OWNER)
    fake_ctx = _ctx(owner_id="owner")
    fake_ctx.kv.set("users/masha/settings", {"display_name": "Masha", "favorite_color": "blue"})
    service = UserProfileService(fake_ctx, access=access)

    with pytest.raises(PermissionError, match="private_content_subject_mismatch"):
        service.get_profile("masha")

    profile = service.get_profile("masha", actor=MASHA)
    assert profile.display_name == "Masha"
    assert profile.settings["favorite_color"] == "blue"


def test_phase9_skill_manifest_validates_personalization_permissions(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        manifest_extra=[
            "personalization:",
            "  uses: [profile, preferences, memory]",
            "  required_permissions:",
            "    - profile.read.self",
            "    - preferences.read.self",
            "    - memory.write.self",
            "  optional_permissions:",
            "    - tools.invoke.browser_automation",
            "  role_variants:",
            "    member:",
            "      memory: self",
        ],
    )

    report = SkillValidationService(get_ctx()).validate_path(skill_dir, install_mode=True)

    assert report.ok is True
    assert not [issue for issue in report.issues if issue.code.startswith("personalization.")]
    assert not [issue for issue in report.issues if issue.code.startswith("permissions.")]


def test_phase9_skill_manifest_rejects_unknown_or_missing_policy_permissions(tmp_path: Path) -> None:
    unknown = _write_skill(
        tmp_path / "unknown",
        manifest_extra=[
            "personalization:",
            "  uses: [profile]",
            "  required_permissions:",
            "    - profile.read.everyone",
        ],
    )
    missing = _write_skill(
        tmp_path / "missing",
        manifest_extra=[
            "personalization:",
            "  uses: [memory]",
            "  required_permissions:",
            "    - profile.read.self",
        ],
    )

    unknown_report = SkillValidationService(get_ctx()).validate_path(unknown, install_mode=True)
    missing_report = SkillValidationService(get_ctx()).validate_path(missing, install_mode=True)

    assert "permissions.capability.unknown" in {issue.code for issue in unknown_report.issues}
    assert "personalization.permissions_missing" in {issue.code for issue in missing_report.issues}
    assert unknown_report.ok is False
    assert missing_report.ok is False
