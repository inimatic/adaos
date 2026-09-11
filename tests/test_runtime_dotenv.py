from __future__ import annotations

import pytest

from adaos.services import runtime_dotenv


def test_runtime_dotenv_allows_realtime_sidecar_keys() -> None:
    assert runtime_dotenv._is_runtime_key("ADAOS_REALTIME_REMOTE_WS_URL")
    assert runtime_dotenv._is_runtime_key("ADAOS_REALTIME_ALLOW_API_FALLBACK")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [True, False])
def test_cli_dotenv_writer_separates_appended_settings(tmp_path, newline, final_newline) -> None:
    from adaos.apps.cli.app import _write_env_var
    from dotenv import dotenv_values

    path = tmp_path / ".env"
    original = newline.join(["# local settings", "ENV_TYPE=dev"])
    path.write_bytes((original + (newline if final_newline else "")).encode("utf-8"))
    _write_env_var("ADAOS_LANG", "en", path)
    _write_env_var("ADAOS_PROFILE", "default", path)
    _write_env_var("ADAOS_LANG", "ru", path)
    assert dotenv_values(path) == {"ENV_TYPE": "dev", "ADAOS_LANG": "ru", "ADAOS_PROFILE": "default"}
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4

