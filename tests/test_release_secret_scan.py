from __future__ import annotations

import json

import pytest

from adaos.services.release_secret_scan import (
    ReleaseSecretScanError,
    assert_release_tree_secret_free,
    scan_release_bytes,
)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("firebase_api_key", "firebase_api_key=AIza" + "A" * 35),
        ("assigned_secret", "CLOUDFLARE_API_TOKEN=" + "cf" * 24),
        ("aws_access_key", "AWS_ACCESS_KEY_ID=AKIA" + "A1" * 8),
        ("assigned_secret", "REG_S3_SECRET=" + "rS3-" * 12),
        ("root_token", "ADAOS_ROOT_OWNER_TOKEN=mcp_" + "a" * 48),
    ],
)
def test_provider_and_root_secrets_are_detected_without_value_disclosure(
    kind: str, payload: str
) -> None:
    findings = scan_release_bytes("fixture.env", payload.encode())

    assert kind in {item.kind for item in findings}
    assert payload.split("=", 1)[-1] not in str([item.to_dict() for item in findings])
    assert all(item.value_digest.startswith("sha256:") for item in findings)


def test_json_secret_assignment_is_detected_but_safe_references_are_admitted() -> None:
    leaked = json.dumps({"configuration": {"client_secret": "AbCdEf0123456789-secret"}})
    safe = json.dumps(
        {
            "configuration": {
                "client_secret": "credential:" + "a" * 32,
                "token": "${SITE_TOKEN}",
                "digest": "sha256:" + "b" * 64,
            }
        }
    )

    assert {item.kind for item in scan_release_bytes("state.json", leaked.encode())} == {
        "assigned_secret"
    }
    assert scan_release_bytes("state.json", safe.encode()) == ()


def test_release_tree_scan_fails_closed_with_bounded_sanitized_findings(tmp_path) -> None:
    (tmp_path / "routing-descriptor.json").write_text(
        json.dumps({"root_token": "mcp_" + "b" * 48}), encoding="utf-8"
    )

    with pytest.raises(ReleaseSecretScanError) as error:
        assert_release_tree_secret_free(tmp_path)

    assert error.value.findings[0].path == "routing-descriptor.json"
    assert "mcp_" not in str(error.value)
