from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from adaos.services.public_site_delivery import (
    PublicSiteDeliveryError,
    build_delivery_bundle,
    build_delivery_pointer,
    build_routing_descriptor,
    build_rollback_pointer,
    export_delivery_bundle,
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


def test_markdown_bundle_identity_is_independent_of_host_mime_registry(tmp_path, monkeypatch):
    import mimetypes
    output = _compiled_directory(tmp_path)
    (output/'routes/portable.md').write_text('# Portable publication\n', encoding='utf-8')
    before = build_delivery_bundle(output).manifest
    guess = mimetypes.guess_type
    monkeypatch.setattr(mimetypes, 'guess_type', lambda name, *a, **kw:
        ('application/octet-stream', None) if str(name).endswith('.md') else guess(name, *a, **kw))
    after = build_delivery_bundle(output).manifest
    assert before == after
    assert next(a for a in after['artifacts'] if a['path']=='routes/portable.md')['media_type']=='text/markdown'


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

    rollback_target = dict(first)
    rollback_target_body = {
        key: value for key, value in rollback_target.items() if key != "pointer_digest"
    }
    rollback_target_body["deployment_digest"] = "sha256:" + "a" * 64
    rollback_target = {
        **rollback_target_body,
        "pointer_digest": digest(rollback_target_body),
    }
    rollback = build_rollback_pointer(
        second,
        rollback_target,
        published_at="2026-11-04T00:03:00Z",
    )
    assert rollback["generation"] == 3
    assert rollback["deployment_digest"] == rollback_target["deployment_digest"]
    assert rollback["previous_pointer_digest"] == second["pointer_digest"]
    assert rollback["rollback_target_pointer_digest"] == rollback_target["pointer_digest"]
    assert rollback["client_release"] == second["client_release"]


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
    assert receipt["metrics"]["artifact_count"] == len(bundle.manifest["artifacts"])
    assert receipt["metrics"]["total_bytes"] == sum(
        item["size_bytes"] for item in bundle.manifest["artifacts"]
    )

    failed = RecordingAdapter(fail_after=2)
    with pytest.raises(RuntimeError, match="upload failed"):
        publish_delivery(failed, bundle, pointer)
    assert all(call[0] != "pointer" for call in failed.calls)


def test_delivery_export_uses_portable_content_addressed_keys(tmp_path: Path) -> None:
    bundle = build_delivery_bundle(_compiled_directory(tmp_path / "compiled"))
    destination = tmp_path / "mirror"

    first = export_delivery_bundle(bundle, destination)
    second = export_delivery_bundle(bundle, destination)

    assert first["written_objects"] == len(bundle.payloads)
    assert second["written_objects"] == 0
    assert second["reused_objects"] == len(bundle.payloads)
    manifest = destination / first["manifest_key"]
    assert json.loads(manifest.read_text(encoding="utf-8")) == bundle.manifest
    for object_digest, payload in bundle.payloads.items():
        assert (destination / "objects" / "sha256" / object_digest[7:]).read_bytes() == payload


def test_routing_descriptor_binds_one_deployment_to_ordered_zones(
    tmp_path: Path,
) -> None:
    bundle = build_delivery_bundle(_compiled_directory(tmp_path))
    pointer = build_delivery_pointer(
        bundle,
        _client_release(),
        published_at="2026-11-05T00:00:00Z",
        generation=4,
    )

    descriptor = build_routing_descriptor(
        bundle,
        pointer,
        platform_path="st_0123456789ab",
        canonical_url="https://p.adaos.io/st_0123456789ab/",
        zones=[
            {
                "zone_id": "ru-primary",
                "provider": "regru",
                "artifact_base_url": "https://s3.regru.cloud/adaos-public-sites/",
                "priority": 20,
                "region": "ru",
            },
            {
                "zone_id": "global-primary",
                "provider": "cloudflare",
                "artifact_base_url": "https://cf.p.adaos.io/",
                "priority": 10,
                "region": "global",
            },
        ],
    )

    assert [zone["zone_id"] for zone in descriptor["zones"]] == [
        "global-primary",
        "ru-primary",
    ]
    assert descriptor["deployment_digest"] == bundle.deployment_digest
    assert descriptor["artifacts"] == bundle.manifest["artifacts"]
    assert len(json.dumps(descriptor, separators=(",", ":")).encode()) < 16 * 1024

    with pytest.raises(PublicSiteDeliveryError, match="artifact base URL"):
        build_routing_descriptor(
            bundle,
            pointer,
            platform_path="st_0123456789ab",
            canonical_url="https://p.adaos.io/st_0123456789ab/",
            zones=[
                {
                    "zone_id": "untrusted",
                    "provider": "s3",
                    "artifact_base_url": "https://user:secret@example.invalid/",
                    "priority": 1,
                }
            ],
        )


def test_site_studio_export_selects_the_active_retained_revision(tmp_path: Path) -> None:
    compiled = _compiled_directory(tmp_path / "compiled")
    artifacts = {}
    for path in compiled.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(compiled).as_posix()
        artifacts[relative] = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".json"
            else path.read_text(encoding="utf-8")
        )
    database_path = tmp_path / "authoring.sqlite3"
    database = sqlite3.connect(database_path)
    database.executescript(
        "CREATE TABLE site_studio_projects(project_id TEXT,body TEXT);"
        "CREATE TABLE site_studio_revisions(revision_id TEXT,project_id TEXT,source_digest TEXT,"
        "artifact_digest TEXT,source_body TEXT);"
        "CREATE TABLE site_studio_artifacts(digest TEXT,kind TEXT,body TEXT);"
    )
    source = _source()
    artifact_digest = digest(artifacts)
    database.execute(
        "INSERT INTO site_studio_projects VALUES(?,?)",
        ("fixture", json.dumps({"active_revision_id": "delivery-test-r1"})),
    )
    database.execute(
        "INSERT INTO site_studio_revisions VALUES(?,?,?,?,?)",
        ("delivery-test-r1", "fixture", digest(source), artifact_digest, json.dumps(source)),
    )
    database.execute(
        "INSERT INTO site_studio_artifacts VALUES(?,?,?)",
        (artifact_digest, "compiled_site_revision", json.dumps(artifacts)),
    )
    database.commit()
    database.close()
    spec = importlib.util.spec_from_file_location(
        "site_studio_export",
        Path(__file__).resolve().parents[1] / "scripts/export-site-studio-publication.py",
    )
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    (tmp_path / "files").mkdir()

    receipt = exporter.export_publication(
        database_path,
        tmp_path / "files",
        tmp_path / "publication",
        canonical_base_url="https://p.adaos.io/st_fixture/",
    )

    assert receipt["revision_id"] == "delivery-test-r1"
    assert receipt["canonical_url"] == "https://p.adaos.io/st_fixture/"
    publication = tmp_path / "publication"
    route = json.loads((publication / "routes/landing.route.json").read_text())
    projection = json.loads((publication / "site.projection.json").read_text())
    deployment = json.loads((publication / "site.deployment.json").read_text())
    discovery = json.loads(
        (publication / ".well-known/adaos-site.json").read_text()
    )
    assert route["canonical_url"] == "https://p.adaos.io/st_fixture/"
    assert route["head"]["open_graph"]["og:url"] == route["canonical_url"]
    assert all(
        item["url"] == route["canonical_url"]
        for item in route["json_ld"]
        if "url" in item
    )
    assert projection["routes"][0]["artifact_digest"] == digest(route)
    assert deployment["projection_digest"] == projection["projection_digest"]
    assert receipt["deployment_digest"] == deployment["deployment_digest"]
    assert discovery["canonical_origin"] == "https://p.adaos.io/st_fixture"
    assert discovery["projection_digest"] == projection["projection_digest"]
    assert "https://p.adaos.io/st_fixture/" in (
        publication / "sitemap.xml"
    ).read_text()
    assert "https://draft.invalid" not in "".join(
        path.read_text(encoding="utf-8")
        for path in publication.rglob("*")
        if path.is_file() and path.suffix in {".json", ".txt", ".xml"}
    )


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
