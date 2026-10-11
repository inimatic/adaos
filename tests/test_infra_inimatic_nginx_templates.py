from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INFRA_ROOT = REPO_ROOT / "src" / "adaos" / "integrations" / "infra-inimatic"

if not (INFRA_ROOT / "vhost.d" / "api.inimatic.com").is_file():
    pytest.skip(
        "private infra-inimatic submodule is not available in this checkout",
        allow_module_level=True,
    )


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


@pytest.mark.parametrize("host", ["mcp.api.inimatic.com", "mcp.ru.api.inimatic.com"])
def test_native_mcp_proxy_overwrites_all_client_certificate_headers(host: str) -> None:
    content = (INFRA_ROOT / "vhost.d" / (host + "_location")).read_text(encoding="utf-8")
    for name, variable in (
        ("X-SSL-Client-Verify", "$ssl_client_verify"),
        ("X-Client-Subject", "$ssl_client_s_dn"),
        ("X-Client-Issuer", "$ssl_client_i_dn"),
        ("X-Client-Cert", "$ssl_client_escaped_cert"),
    ):
        assert f"proxy_set_header {name} {variable};" in content
    assert "$http_x_" not in content
    assert "proxy_buffering off;" in content


@pytest.mark.parametrize("host", ["mcp.api.inimatic.com", "mcp.ru.api.inimatic.com"])
def test_native_mcp_proxy_preserves_bearer_and_verified_mtls(host: str) -> None:
    content = (INFRA_ROOT / "vhost.d" / host).read_text(encoding="utf-8")
    assert "ssl_verify_client optional_no_ca;" in content
    assert "ssl_client_certificate /etc/nginx/certs/adaos_ca.pem;" in content
    assert "ssl_verify_client off;" not in content
