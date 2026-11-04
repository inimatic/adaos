from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.public_site_delivery import (
    PublicSiteDeliveryError,
    build_delivery_bundle,
    build_delivery_pointer,
    publish_delivery,
)
from adaos.services.site_studio.compiler import compile_site_revision
from adaos.services.site_studio.contracts import digest


def test_bundle_admission_verifies_semantic_and_byte_digests(tmp_path: Path) -> None:
    output = _compiled_directory(tmp_path)

    bundle = build_delivery_bundle(output)

    assert bundle.site_id == "adaos-public"
    assert bundle.manifest["bundle_digest"].startswith("sha256:")
    assert {entry["kind"] for entry in bundle.manifest["artifacts"]} >= {
        "asset",
        "projection",
        "component_package",
        "component_release_set",
        "deployment",
    }
    assert "bucket" not in json.dumps(bundle.manifest)
    route = output / "routes/landing.route.json"
    route.write_text(route.read_text(encoding="utf-8") + " ", encoding="utf-8")
    # Whitespace changes the delivery object, but not the semantic route digest.
    changed = build_delivery_bundle(output)
    assert changed.manifest["bundle_digest"] != bundle.manifest["bundle_digest"]
    body = json.loads(route.read_text(encoding="utf-8"))
    body["head"]["title"] = "Tampered"
    route.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(PublicSiteDeliveryError, match="digest mismatch"):
        build_delivery_bundle(output)


def test_pointer_locks_client_release_and_supports_rollback_lineage(tmp_path: Path) -> None:
    bundle = build_delivery_bundle(_compiled_directory(tmp_path))
    release = _client_release()

    first = build_delivery_pointer(
        bundle,
        release,
        published_at="2026-11-04T00:00:00Z",
        generation=1,
    )
    second = build_delivery_pointer(
        bundle,
        release,
        published_at="2026-11-04T00:01:00Z",
        generation=2,
        previous_pointer_digest=first["pointer_digest"],
    )

    assert second["previous_pointer_digest"] == first["pointer_digest"]
    changed = dict(release)
    changed["source_origin"] = "https://another.invalid"
    with pytest.raises(PublicSiteDeliveryError, match="semantic digest"):
        build_delivery_pointer(
            bundle,
            changed,
            published_at="2026-11-04T00:02:00Z",
            generation=3,
        )


def test_publication_changes_pointer_only_after_all_immutable_uploads(
    tmp_path: Path,
) -> None:
    bundle = build_delivery_bundle(_compiled_directory(tmp_path))
    pointer = build_delivery_pointer(
        bundle,
        _client_release(),
        published_at="2026-11-04T00:00:00Z",
        generation=1,
    )
    adapter = RecordingAdapter()

    receipt = publish_delivery(adapter, bundle, pointer)

    assert adapter.calls[-1][0] == "pointer"
    assert len(adapter.calls) == len(bundle.manifest["artifacts"]) + 1
    assert receipt["pointer_digest"] == pointer["pointer_digest"]
    assert receipt["provider_receipt"]["provider"] == "fixture"

    failed = RecordingAdapter(fail_after=2)
    with pytest.raises(RuntimeError, match="upload failed"):
        publish_delivery(failed, bundle, pointer)
    assert all(call[0] != "pointer" for call in failed.calls)


class RecordingAdapter:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_after = fail_after

    def _put(self, kind, artifact, payload):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("upload failed")
        assert digest(payload) == artifact["digest"]
        self.calls.append((kind, artifact["path"]))
        return {"status": "confirmed"}

    def put_immutable_asset(self, artifact, payload):
        return self._put("asset", artifact, payload)

    def put_projection(self, artifact, payload):
        return self._put("projection", artifact, payload)

    def put_component_package(self, artifact, payload):
        return self._put("component_package", artifact, payload)

    def put_component_release_set(self, artifact, payload):
        return self._put("component_release_set", artifact, payload)

    def put_deployment(self, artifact, payload):
        return self._put("deployment", artifact, payload)

    def publish_deployment_pointer(self, pointer, *, expected_pointer_digest):
        self.calls.append(("pointer", pointer["pointer_digest"]))
        return {"status": "confirmed", "provider": "fixture"}

    def resolve_request(self, host, path):
        return None

    def verify_domain(self, hostname, proof):
        return {"status": "verified"}

    def bind_domain(self, hostname, site_id, *, base_path="/"):
        return {"status": "confirmed"}

    def purge_or_revalidate(self, urls):
        return {"status": "confirmed"}

    def report_delivery_health(self):
        return {"status": "ready"}


def _compiled_directory(tmp_path: Path) -> Path:
    compiled = compile_site_revision(_source())
    output = tmp_path / "site"
    for relative, value in compiled["artifacts"].items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return output


def _client_release() -> dict:
    body = {
        "schema": "adaos.public_site_client_release.v1",
        "release_id": "site-client-test",
        "renderer_abi": "adaos.public-renderer.v1",
        "source_origin": "https://site-client-test.web.app",
        "base_path": "/",
        "entry": {"scripts": ["main.0123456789abcdef.js"], "styles": []},
        "assets": [
            {
                "path": "main.0123456789abcdef.js",
                "digest": digest(b"fixture"),
                "size_bytes": 7,
                "media_type": "text/javascript",
            }
        ],
    }
    return {**body, "release_digest": digest(body)}


def _source() -> dict:
    return {
        "$schema": "adaos.site.v1",
        "profile": "public_site",
        "id": "adaos-public",
        "revision": {
            "id": "delivery-test-r1",
            "created_at": "2026-11-04T00:00:00Z",
            "prompt_packet_ref": "prompt-packet.json",
        },
        "locale": "en",
        "identity": {
            "name": "AdaOS",
            "category": "Software platform",
            "canonical_origin": "https://adaos.io",
        },
        "theme": {
            "tokens": {
                "color.canvas": "#eef3f4",
                "color.surface": "#ffffff",
                "color.ink": "#102028",
                "color.muted": "#596c73",
                "color.accent": "#2dd4bf",
                "color.accentSoft": "#d9f2ed",
                "color.signal": "#f2b84b",
                "color.dark": "#08131a",
                "color.darkInk": "#f3fbfb",
                "color.line": "#cad7da",
                "size.maxWidth": "1200px",
            }
        },
        "assets": [],
        "resources": [
            {
                "id": "navigation",
                "type": "site.navigation",
                "public": True,
                "data": {"brand": {"name": "AdaOS"}, "items": []},
            },
            {
                "id": "hero",
                "type": "site.hero",
                "public": True,
                "data": {
                    "title": "AdaOS",
                    "statement": "Software that can keep evolving",
                    "body": "A governed local-first environment.",
                    "actions": [],
                },
            },
            {
                "id": "footer",
                "type": "site.footer",
                "public": True,
                "data": {"brand": "AdaOS", "items": [], "legal": "Preview"},
            },
        ],
        "routes": [
            {
                "id": "landing",
                "path": "/",
                "canonical_url": "https://adaos.io/",
                "metadata": {
                    "title": "AdaOS",
                    "description": "A governed local-first environment.",
                    "robots": "index,follow",
                    "open_graph": {
                        "og:title": "AdaOS",
                        "og:description": "AdaOS",
                        "og:type": "website",
                    },
                    "twitter": {"twitter:card": "summary", "twitter:title": "AdaOS"},
                },
                "json_ld": [
                    {"@context": "https://schema.org", "@type": "WebSite", "name": "AdaOS"}
                ],
                "sections": [
                    {"id": "header", "component": "site.header", "resource_ref": "navigation"},
                    {"id": "hero", "component": "site.hero", "resource_ref": "hero"},
                    {"id": "footer", "component": "site.footer", "resource_ref": "footer"},
                ],
            }
        ],
    }
