from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_vhost_d_api_template_keeps_mtls_optional_at_server_scope() -> None:
    content = _read("src/adaos/integrations/infra-inimatic/vhost.d/api.inimatic.com")
    assert "ssl_verify_client optional;" in content
    assert "ssl_verify_client off;" not in content


def test_legacy_vhost_api_template_does_not_toggle_mtls_per_location() -> None:
    content = _read("src/adaos/integrations/infra-inimatic/vhost/api.inimatic.com")
    assert "ssl_verify_client optional;" in content
    assert "ssl_verify_client off;" not in content


def test_vhost_d_media_timeout_does_not_preempt_backend_relay_cleanup() -> None:
    content = _read("src/adaos/integrations/infra-inimatic/vhost.d/api.inimatic.com")
    media_location = content.index("location ~ ^/hubs/[^/]+/media/.*$")
    generic_location = content.index("location /hubs/")
    media_block = content[media_location:generic_location]

    assert "proxy_read_timeout 130s;" in media_block
    assert "proxy_send_timeout 130s;" in media_block
