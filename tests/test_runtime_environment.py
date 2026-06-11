from __future__ import annotations

from types import SimpleNamespace

import yaml

from adaos.services.runtime_environment import normalize_env_type, runtime_environment_payload
from adaos.services.root.client import RootHttpClient
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


def test_settings_from_sources_uses_node_root_base_url(tmp_path, monkeypatch):
    base_dir = tmp_path / "base"
    base_dir.mkdir(exist_ok=True)
    (base_dir / "node.yaml").write_text(
        yaml.safe_dump(
            {
                "subnet_id": "sn_test",
                "root": {"base_url": "https://ru.api.inimatic.com"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"ADAOS_BASE_DIR={base_dir}\n", encoding="utf-8")
    monkeypatch.delenv("ADAOS_API_BASE", raising=False)

    settings = Settings.from_sources(str(env_file))

    assert settings.api_base == "https://ru.api.inimatic.com"


def test_settings_from_sources_env_api_base_overrides_node_root_base_url(tmp_path, monkeypatch):
    base_dir = tmp_path / "base"
    base_dir.mkdir(exist_ok=True)
    (base_dir / "node.yaml").write_text(
        yaml.safe_dump(
            {
                "subnet_id": "sn_test",
                "root": {"base_url": "https://ru.api.inimatic.com"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"ADAOS_BASE_DIR={base_dir}\nADAOS_API_BASE=https://custom-root.example\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ADAOS_API_BASE", raising=False)

    settings = Settings.from_sources(str(env_file))

    assert settings.api_base == "https://custom-root.example"


def test_root_http_client_from_settings_prefers_root_settings_base_url():
    settings = SimpleNamespace(
        api_base="https://api.inimatic.com",
        root_settings=SimpleNamespace(base_url="https://ru.api.inimatic.com"),
    )

    client = RootHttpClient.from_settings(settings)

    assert client.base_url == "https://ru.api.inimatic.com"


def test_runtime_environment_normalizes_unknown_values_to_prod():
    assert normalize_env_type("staging") == "prod"
