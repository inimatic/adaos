from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from adaos.services.site_studio import (
    apply_change_set,
    commit_revision,
    compile_site_revision,
    current_preview,
    default_component_catalog,
    digest,
)
from adaos.services.site_studio.contracts import (
    SiteStudioContractError,
    validate_contract,
)


CREATED = "2026-11-03T00:00:00Z"


def test_authoring_context_and_change_set_are_typed_and_bounded() -> None:
    source = _source("revision-one", CREATED)
    context = _context(source)
    validate_contract("site_studio_authoring_context.v1.schema.json", context)
    oversized = deepcopy(context)
    oversized["outcome"]["description"] = "x" * 8000
    oversized["constraints"] = [f"{index}:" + "x" * 7900 for index in range(12)]
    with pytest.raises(SiteStudioContractError, match="limit"):
        validate_contract("site_studio_authoring_context.v1.schema.json", oversized)

    change_set = _change_set(source, value="Changed by one semantic operation")
    changed = apply_change_set(source, change_set)
    assert (
        changed["resources"][1]["data"]["body"] == "Changed by one semantic operation"
    )
    stale = deepcopy(change_set)
    stale["base_source_digest"] = f"sha256:{'0' * 64}"
    with pytest.raises(SiteStudioContractError, match="base source"):
        apply_change_set(source, stale)


def test_compiler_emits_digest_bound_static_preview_and_component_lock() -> None:
    source = _source("revision-one", CREATED)
    first = compile_site_revision(source)
    second = compile_site_revision(source)

    assert first["artifact_digest"] == second["artifact_digest"]
    assert first["deployment_digest"] == second["deployment_digest"]
    assert first["component_set_digest"] == second["component_set_digest"]
    assert first["projection"]["policy"]["data_sources"] == ["static"]
    assert first["preview"]["source"] == source
    assert (
        first["preview"]["deployment"]["projection_digest"]
        == first["projection_digest"]
    )
    assert {package["family"] for package in first["component_packages"]} == {
        "site.collections",
        "site.core",
        "site.media",
    }
    assert first["artifacts"]["routes/landing.route.json"]["visible_content"]
    assert (
        first["artifacts"][".well-known/adaos-site.json"]["sitemap_ref"]
        == "../sitemap.xml"
    )


def test_compiler_rejects_unresolved_assets_and_executable_fields() -> None:
    source = _source("revision-one", CREATED)
    source["assets"] = [
        {
            "id": "hero-image",
            "source_path": "objects/hero-image.png",
            "public_path": "assets/hero-image.png",
            "media_type": "image/png",
            "digest": f"sha256:{'1' * 64}",
        }
    ]
    with pytest.raises(SiteStudioContractError, match="asset resolver"):
        compile_site_revision(source)
    source["assets"] = []
    source["resources"][1]["data"]["script"] = "alert(1)"
    with pytest.raises(SiteStudioContractError, match="forbidden static field"):
        compile_site_revision(source)


def test_revision_store_moves_preview_only_after_successful_compile() -> None:
    db = sqlite3.connect(":memory:")
    source = _source("revision-one", CREATED)
    context = _context(source)
    initial = _change_set(source, value=source["resources"][1]["data"]["body"])
    retained = commit_revision(
        db,
        project_id="adaos-site",
        title="AdaOS",
        source=source,
        authoring_context=context,
        change_set=initial,
    )
    assert (
        current_preview(db, "adaos-site")["deployment"]
        == retained["preview"]["deployment"]
    )

    change_set = _change_set(
        source, value="A retained correction", identity="change-two"
    )
    changed = apply_change_set(source, change_set)
    changed["revision"] = {
        "id": "revision-two",
        "created_at": "2026-11-03T00:01:00Z",
        "prompt_packet_ref": "prompt-packet.json",
        "base_digest": digest(source),
    }
    context_two = _context(changed, identity="context-two")
    committed = commit_revision(
        db,
        project_id="adaos-site",
        title="AdaOS",
        source=changed,
        authoring_context=context_two,
        change_set=change_set,
    )
    assert committed["revision"]["parent_revision_id"] == "revision-one"
    assert (
        current_preview(db, "adaos-site")["source"]["revision"]["id"] == "revision-two"
    )

    broken = deepcopy(changed)
    broken["revision"] = {
        "id": "revision-three",
        "created_at": "2026-11-03T00:02:00Z",
        "prompt_packet_ref": "prompt-packet.json",
        "base_digest": digest(changed),
    }
    broken["theme"]["tokens"]["custom.css"] = "body{}"
    bad_change = _change_set(changed, value="not committed", identity="change-three")
    with pytest.raises(SiteStudioContractError):
        commit_revision(
            db,
            project_id="adaos-site",
            title="AdaOS",
            source=broken,
            authoring_context=_context(broken, identity="context-three"),
            change_set=bad_change,
        )
    assert (
        current_preview(db, "adaos-site")["source"]["revision"]["id"] == "revision-two"
    )


def test_default_catalog_is_safe_inspector_input() -> None:
    catalog = default_component_catalog()
    validate_contract("site_component_catalog.v1.schema.json", catalog)
    assert all(
        component["data_schema"]["type"] == "object"
        for component in catalog["components"]
    )
    assert all("addable" in component["editor"] for component in catalog["components"])


def _source(revision_id: str, created_at: str) -> dict:
    return {
        "$schema": "adaos.site.v1",
        "profile": "public_site",
        "id": "adaos-public",
        "revision": {
            "id": revision_id,
            "created_at": created_at,
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
                "data": {"brand": "AdaOS", "items": [], "legal": "Development preview"},
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
                    {
                        "@context": "https://schema.org",
                        "@type": "WebSite",
                        "name": "AdaOS",
                    }
                ],
                "sections": [
                    {
                        "id": "header",
                        "component": "site.header",
                        "resource_ref": "navigation",
                        "anchor_id": "top",
                    },
                    {
                        "id": "hero",
                        "component": "site.hero",
                        "resource_ref": "hero",
                        "anchor_id": "hero",
                    },
                    {
                        "id": "footer",
                        "component": "site.footer",
                        "resource_ref": "footer",
                        "anchor_id": "footer",
                    },
                ],
            }
        ],
    }


def _context(source: dict, identity: str = "context-one") -> dict:
    catalog = default_component_catalog()
    return {
        "schema": "adaos.site_studio.authoring_context.v1",
        "context_id": identity,
        "site_id": source["id"],
        "actor": {"id": "user:owner", "kind": "owner", "display_name": "Owner"},
        "outcome": {
            "goal": "Explain AdaOS",
            "audience": "Developers",
            "description": "Static public site",
        },
        "jobs": [],
        "interface_regions": [
            {
                "id": "structure",
                "role": "structure",
                "width_mode": "fixed",
                "min_width_px": 240,
                "max_width_px": 320,
                "responsive": "drawer",
                "content_budget": {
                    "max_columns": 1,
                    "max_form_columns": 1,
                    "compatible_presentations": ["tree", "list"],
                },
            },
            {
                "id": "canvas",
                "role": "canvas",
                "width_mode": "growing",
                "min_width_px": 360,
                "max_width_px": 1800,
                "responsive": "persistent",
                "content_budget": {
                    "max_columns": 1,
                    "max_form_columns": 1,
                    "compatible_presentations": ["preview"],
                },
            },
            {
                "id": "inspect",
                "role": "inspect",
                "width_mode": "bounded",
                "min_width_px": 300,
                "max_width_px": 420,
                "responsive": "tabs",
                "content_budget": {
                    "max_columns": 1,
                    "max_form_columns": 1,
                    "compatible_presentations": ["form", "conversation"],
                },
            },
        ],
        "current_selection": {
            "route_id": "landing",
            "section_id": "hero",
            "resource_id": "hero",
            "source_anchor": "routes.landing.sections.hero",
        },
        "materials": [],
        "source_digest": digest(source),
        "catalog_digest": digest(catalog),
        "constraints": ["Static data only", "No arbitrary executable content"],
        "representative_states": ["default", "long_content", "compact"],
        "review_criteria": [
            "No horizontal overflow",
            "Metadata and structured data are present",
        ],
        "created_at": source["revision"]["created_at"],
    }


def _change_set(source: dict, *, value: str, identity: str = "change-one") -> dict:
    return {
        "schema": "adaos.site_change_set.v1",
        "change_set_id": identity,
        "project_id": "adaos-site",
        "base_revision_id": source["revision"]["id"],
        "base_source_digest": digest(source),
        "actor": {"id": "user:owner", "kind": "owner"},
        "source_map_targets": ["resources.hero.data.body"],
        "operations": [
            {
                "operation_id": f"{identity}-op",
                "kind": "set_resource_field",
                "target": "resources.hero.data.body",
                "value": value,
            }
        ],
        "rationale": "Retained direct-edit correction",
        "validation": {"status": "pending", "findings": []},
        "status": "proposed",
        "created_at": source["revision"]["created_at"],
    }
