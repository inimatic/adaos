from __future__ import annotations

import pytest

from adaos.sdk import data
from adaos.sdk.data import pending_actions


def test_legacy_sdk_cannot_forge_pending_action_response() -> None:
    with pytest.raises(PermissionError, match="requires_verified_ingress"):
        pending_actions.respond_pending_action(
            "pa.target",
            "approve",
            responder={"type": "system", "system_id": "forged"},
        )


def test_legacy_response_helper_is_hidden_from_sdk_discovery() -> None:
    assert "respond_pending_action" not in pending_actions.__all__
    assert "respond_pending_action" not in data.__all__
