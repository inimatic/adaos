from __future__ import annotations

from adaos.services.runtime_environment import normalize_env_type, runtime_environment_payload
from adaos.services.settings import Settings


def test_runtime_environment_payload_uses_settings_env_type(tmp_path):
    settings = Settings(base_dir=tmp_path, env_type="dev", profile="local")

    assert runtime_environment_payload(settings) == {
        "envType": "dev",
        "mode": "dev",
        "debug": True,
        "source": "settings.env_type",
        "profile": "local",
    }


def test_settings_from_sources_preserves_env_type_from_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"ENV_TYPE=dev\nADAOS_BASE_DIR={tmp_path / 'base'}\n", encoding="utf-8")
    monkeypatch.delenv("ENV_TYPE", raising=False)

    settings = Settings.from_sources(str(env_file))

    assert settings.env_type == "dev"


def test_runtime_environment_normalizes_unknown_values_to_prod():
    assert normalize_env_type("staging") == "prod"
