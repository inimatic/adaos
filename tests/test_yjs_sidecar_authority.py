from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from adaos.services.yjs.sidecar_authority import (
    SidecarYjsAuthorityError,
    assert_python_yjs_write_authority,
)


class _Response:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_python_authority_guard_ignores_non_canary(monkeypatch):
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES", "go-yjs-canary")
    with patch(
        "adaos.services.yjs.sidecar_authority.urllib.request.urlopen",
        side_effect=AssertionError("non-canary must not query the sidecar"),
    ):
        await assert_python_yjs_write_authority("desktop")


@pytest.mark.asyncio
async def test_python_authority_guard_allows_stable_python_owner(monkeypatch):
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES", "go-yjs-canary")
    monkeypatch.setenv("ADAOS_TOKEN", "authority-token")
    response = _Response(
        {
            "record": {
                "webspace_id": "go-yjs-canary",
                "owner": "python",
                "state": "stable",
                "generation": 1,
            }
        }
    )
    with patch(
        "adaos.services.yjs.sidecar_authority.urllib.request.urlopen",
        return_value=response,
    ):
        await assert_python_yjs_write_authority("go-yjs-canary")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "state"),
    (("python", "preparing"), ("sidecar", "stable"), ("sidecar", "preparing")),
)
async def test_python_authority_guard_fails_closed_after_prepare(
    monkeypatch,
    owner: str,
    state: str,
):
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES", "go-yjs-canary")
    monkeypatch.setenv("ADAOS_TOKEN", "authority-token")
    response = _Response(
        {
            "record": {
                "webspace_id": "go-yjs-canary",
                "owner": owner,
                "state": state,
                "generation": 2,
            }
        }
    )
    with patch(
        "adaos.services.yjs.sidecar_authority.urllib.request.urlopen",
        return_value=response,
    ), pytest.raises(SidecarYjsAuthorityError, match="python_yjs_write_fenced"):
        await assert_python_yjs_write_authority("go-yjs-canary")


@pytest.mark.asyncio
async def test_python_authority_guard_fails_closed_when_sidecar_is_unavailable(
    monkeypatch,
):
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES", "go-yjs-canary")
    monkeypatch.setenv("ADAOS_TOKEN", "authority-token")
    with patch(
        "adaos.services.yjs.sidecar_authority.urllib.request.urlopen",
        side_effect=OSError("offline"),
    ), pytest.raises(SidecarYjsAuthorityError, match="sidecar_authority_unavailable"):
        await assert_python_yjs_write_authority("go-yjs-canary")
