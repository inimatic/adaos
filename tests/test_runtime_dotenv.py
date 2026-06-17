from __future__ import annotations

from adaos.services import runtime_dotenv


def test_runtime_dotenv_allows_realtime_sidecar_keys() -> None:
    assert runtime_dotenv._is_runtime_key("ADAOS_REALTIME_REMOTE_WS_URL")
    assert runtime_dotenv._is_runtime_key("ADAOS_REALTIME_ALLOW_API_FALLBACK")

