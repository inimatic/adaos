from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import service_ui
from adaos.services.skill.service_supervisor import _resolve_service_spec


class _UiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/provider":
            body = (
                f'<a href="http://127.0.0.1:{self.server.server_port}/provider/">redirect</a>'
            ).encode()
            self.send_response(308)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{self.server.server_port}/provider/",
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/provider/redirect":
            body = (
                f'<a href="http://127.0.0.1:{self.server.server_port}/provider/target">redirect</a>'
            ).encode()
            self.send_response(307)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{self.server.server_port}/provider/target",
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"<html><head><title>Provider UI</title></head><body>ready</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return None


class _Supervisor:
    def __init__(self, spec) -> None:  # noqa: ANN001
        self._specs = {spec.skill: spec}

    def ensure_discovered(self) -> None:
        return None

    def status(self, name: str, *, check_health: bool = False) -> dict:
        assert name in self._specs
        return {"running": True, "health_ok": True if check_health else None}

    def ui_surface(self, name: str, *, check_health: bool = False) -> dict | None:
        spec = self._specs.get(name)
        if spec is None or not spec.ui_enabled:
            return None
        return {
            "schema": "adaos.service.ui_surface.v1",
            "service": name,
            "access": spec.ui_access,
            "origin_policy": spec.ui_origin_policy,
            "embedding": spec.ui_embedding,
            "proxy_path": f"/api/services/{name}/ui/",
            "bootstrap_path": f"/api/services/{name}/ui-bootstrap",
            "health": {"running": True, "ok": True if check_health else None},
        }


def test_service_ui_proxy_requires_auth_and_replaces_provider_frame_policy(monkeypatch, tmp_path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        skill_root = tmp_path / "skills" / "provider_ui"
        skill_root.mkdir(parents=True)
        spec = _resolve_service_spec(
            "provider_ui",
            skill_root,
            {
                "runtime": {"kind": "service"},
                "service": {
                    "host": "127.0.0.1",
                    "port": server.server_port,
                    "command": ["-m", "handlers.service"],
                    "ui": {
                        "enabled": True,
                        "path": "/provider",
                        "access": "authenticated",
                        "origin_policy": "same-origin",
                        "embedding": "same-origin",
                    },
                },
            },
        )
        assert spec is not None
        monkeypatch.setenv("ADAOS_TOKEN", "service-ui-test-token")
        monkeypatch.setattr(service_ui, "get_service_supervisor", lambda: _Supervisor(spec))
        app = FastAPI()
        app.include_router(service_ui.router, prefix="/api")
        client = TestClient(app)

        denied = client.get("/api/services/provider_ui/ui/")
        assert denied.status_code == 401

        bootstrap = client.get(
            "/api/services/provider_ui/ui-bootstrap?token=service-ui-test-token",
            follow_redirects=False,
        )
        assert bootstrap.status_code == 303
        assert bootstrap.headers["location"] == "/api/services/provider_ui/ui/"
        assert "HttpOnly" in bootstrap.headers["set-cookie"]

        deep_link = client.get(
            "/api/services/provider_ui/ui-bootstrap",
            params={
                "token": "service-ui-test-token",
                "fragment": "#/experiments?workflowType=machine_learning",
            },
            follow_redirects=False,
        )
        assert deep_link.status_code == 303
        assert deep_link.headers["location"] == (
            "/api/services/provider_ui/ui/#/experiments?workflowType=machine_learning"
        )

        invalid_deep_link = client.get(
            "/api/services/provider_ui/ui-bootstrap",
            params={"token": "service-ui-test-token", "fragment": "https://foreign.example"},
            follow_redirects=False,
        )
        assert invalid_deep_link.status_code == 400

        proxied = client.get(
            "/api/services/provider_ui/ui/",
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        assert proxied.status_code == 200
        assert "Provider UI" in proxied.text
        assert proxied.headers["x-frame-options"] == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in proxied.headers["content-security-policy"]

        redirected = client.get(
            "/api/services/provider_ui/ui/redirect",
            follow_redirects=False,
        )
        assert redirected.status_code == 307
        assert redirected.headers["location"] == "/api/services/provider_ui/ui/target"
        assert redirected.content == b""
        assert f"127.0.0.1:{server.server_port}" not in redirected.text

        cross_site = client.get(
            "/api/services/provider_ui/ui/?token=service-ui-test-token",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert cross_site.status_code == 403

        foreign_origin = client.get(
            "/api/services/provider_ui/ui/?token=service-ui-test-token",
            headers={"Origin": "https://foreign.example"},
        )
        assert foreign_origin.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
