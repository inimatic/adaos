from __future__ import annotations

import json
from pathlib import Path

from adaos.services.skill.tool_contract import (
    declared_skill_webui_owner,
    declared_tool_contract,
)


def test_declared_tool_contract_reads_one_runtime_snapshot(tmp_path: Path) -> None:
    manifest_path = tmp_path / "resolved.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "tools": {
                    "archive": {
                        "side_effects": "external_write",
                        "approval_scope": {"arguments": ["message_id"]},
                        "permissions": {
                            "required": ["providers.google.gmail"],
                            "optional": ["workspace.read"],
                        },
                        "application_access": {
                            "permission": "providers.google.gmail",
                            "capability": "mail.manage",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    class _Manager:
        calls = 0

        def dev_runtime_status(self, _name: str) -> dict[str, object]:
            self.calls += 1
            return {"resolved_manifest": str(manifest_path)}

    manager = _Manager()
    contract = declared_tool_contract(
        manager,
        skill_name="mail_provider",
        public_tool="archive",
        dev=True,
    )

    assert manager.calls == 1
    assert contract == {
        "side_effects": "external_write",
        "approval_scope": {"arguments": ["message_id"]},
        "permissions": ("providers.google.gmail", "workspace.read"),
        "application_access": {
            "capability": "mail.manage",
            "permission": "providers.google.gmail",
        },
        "permissions_source": "resolved_manifest",
    }


def test_immutable_trial_contract_reuses_content_addressed_manifest(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "resolved.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "webui_owner": "application",
                "tools": {
                    "read": {
                        "side_effects": "read_only",
                        "permissions": {"required": ["workspace.read"]},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    class _Manager:
        calls = 0
        _adaos_immutable_trial_authority = (
            str(tmp_path),
            "sha256:release",
            "sha256:component",
        )

        def runtime_status(self, _name: str) -> dict[str, object]:
            self.calls += 1
            return {"resolved_manifest": str(manifest_path)}

    manager = _Manager()
    first = declared_tool_contract(
        manager,
        skill_name="management",
        public_tool="read",
        dev=False,
    )
    second = declared_tool_contract(
        manager,
        skill_name="management",
        public_tool="read",
        dev=False,
    )
    owner = declared_skill_webui_owner(
        manager,
        skill_name="management",
        dev=False,
    )

    assert first == second
    assert owner == "application"
    assert manager.calls == 1


def test_legacy_runtime_effect_recovers_only_standard_workspace_capability(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "resolved.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "tools": {
                    "refresh": {
                        "side_effects": "local_write",
                        "permissions": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    class _Manager:
        def runtime_status(self, _name: str) -> dict[str, object]:
            return {"resolved_manifest": str(manifest_path)}

    contract = declared_tool_contract(
        _Manager(),
        skill_name="subscription_status_skill",
        public_tool="refresh",
        dev=False,
    )

    assert contract["permissions"] == ("workspace.write",)
    assert contract["permissions_source"] == "legacy_side_effects"


def test_current_runtime_empty_capabilities_remain_fail_closed(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "resolved.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "capabilities": [],
                "tools": {
                    "refresh": {
                        "side_effects": "local_write",
                        "permissions": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    class _Manager:
        def runtime_status(self, _name: str) -> dict[str, object]:
            return {"resolved_manifest": str(manifest_path)}

    contract = declared_tool_contract(
        _Manager(),
        skill_name="subscription_status_skill",
        public_tool="refresh",
        dev=False,
    )

    assert contract["permissions"] == ()
    assert contract["permissions_source"] == "undeclared"


def test_shared_owner_recovers_from_immutable_legacy_slot_source(
    tmp_path: Path,
) -> None:
    slot = tmp_path / "runtime" / "mail_provider" / "v1" / "slots" / "A"
    source = slot / "src" / "skills" / "mail_provider"
    source.mkdir(parents=True)
    (source / "skill.yaml").write_text(
        "name: mail_provider\nwebui_owner: shared\n",
        encoding="utf-8",
    )
    manifest_path = slot / "resolved.manifest.json"
    manifest_path.write_text(
        json.dumps({"name": "mail_provider", "source": str(source)}),
        encoding="utf-8",
    )

    class _Manager:
        def runtime_status(self, _name: str) -> dict[str, object]:
            return {"resolved_manifest": str(manifest_path)}

    assert (
        declared_skill_webui_owner(
            _Manager(), skill_name="mail_provider", dev=False
        )
        == "shared"
    )


def test_shared_owner_does_not_fall_back_outside_selected_runtime_slot(
    tmp_path: Path,
) -> None:
    slot = tmp_path / "runtime" / "mail_provider" / "v1" / "slots" / "A"
    slot.mkdir(parents=True)
    mutable_source = tmp_path / "workspace" / "skills" / "mail_provider"
    mutable_source.mkdir(parents=True)
    (mutable_source / "skill.yaml").write_text(
        "name: mail_provider\nwebui_owner: shared\n",
        encoding="utf-8",
    )
    manifest_path = slot / "resolved.manifest.json"
    manifest_path.write_text(
        json.dumps({"name": "mail_provider", "source": str(mutable_source)}),
        encoding="utf-8",
    )

    class _Manager:
        def runtime_status(self, _name: str) -> dict[str, object]:
            return {"resolved_manifest": str(manifest_path)}

    assert (
        declared_skill_webui_owner(
            _Manager(), skill_name="mail_provider", dev=False
        )
        == ""
    )
