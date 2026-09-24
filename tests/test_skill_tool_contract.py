from __future__ import annotations

import json
from pathlib import Path

from adaos.services.skill.tool_contract import declared_tool_contract


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
    }
