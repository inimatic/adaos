from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from adaos.services.site_studio import (
    apply_change_set,
    change_set_history,
    commit_proposed_change_set,
    commit_revision,
    compile_site_revision,
    current_preview,
    default_component_catalog,
    digest,
    project_state,
    propose_change_set,
    revision_history,
)
from adaos.services.site_studio.contracts import (
    SiteStudioContractError,
    canonical_json,
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

    structural = _change_set(source, value="unchanged", identity="change-structure")
    structural["operations"] = [
        {
            "operation_id": "add-resource",
            "kind": "add_resource",
            "target": "resources.new-section",
            "value": {
                "id": "new-section",
                "type": "site.narrative",
                "public": True,
                "data": {"title": "New", "paragraphs": ["Content"]},
            },
        },
        {
            "operation_id": "add-section",
            "kind": "add_section",
            "target": "routes.landing.sections.new-section",
            "value": {
                "id": "new-section",
                "component": "site.section",
                "resource_ref": "new-section",
            },
            "before": "footer",
        },
    ]
    expanded = apply_change_set(source, structural)
    assert expanded["resources"][-1]["id"] == "new-section"
    assert expanded["routes"][0]["sections"][-2]["id"] == "new-section"


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
    assert {
        package["family"] for package in first["preview"]["component_packages"]
    } == {"site.core"}
    assert first["preview"]["deployment"]["required_packages"] == [
        {
            "family": "site.core",
            "digest": first["preview"]["component_packages"][0]["digest"],
        }
    ]
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


def test_compiler_resolves_semantic_asset_ids_to_content_addressed_paths() -> None:
    source = _source("revision-one", CREATED)
    raw = b"owned-image-bytes"
    source["assets"] = [
        {
            "id": "hero-image",
            "source_path": "objects/hero-image.png",
            "public_path": "assets/hero-image.png",
            "media_type": "image/png",
            "digest": digest(raw),
        }
    ]
    source["resources"][1]["data"]["media"] = {
        "src": "hero-image",
        "alt": "AdaOS preview",
    }
    compiled = compile_site_revision(source, asset_resolver=lambda _asset: raw)
    assert source["resources"][1]["data"]["media"]["src"] == "hero-image"
    assert (
        compiled["preview"]["page"]["widgets"][1]["inputs"]["data"]["media"]["src"]
        == "assets/hero-image.png"
    )


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
    bad_change["source_map_targets"] = ["theme.tokens.custom.css"]
    bad_change["operations"] = [
        {
            "operation_id": "change-three-op",
            "kind": "set_theme_token",
            "target": "theme.tokens.custom.css",
            "value": "body{}",
        }
    ]
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


def test_retained_preview_rejects_tampered_artifact_chain() -> None:
    db = sqlite3.connect(":memory:")
    source = _source("revision-one", CREATED)
    retained = commit_revision(
        db,
        project_id="adaos-site",
        title="AdaOS",
        source=source,
        authoring_context=_context(source),
        change_set=_change_set(source, value=source["resources"][1]["data"]["body"]),
    )
    tampered = deepcopy(retained["preview"])
    tampered["page"]["title"] = "Tampered after retention"
    db.execute(
        "UPDATE site_studio_artifacts SET body=? WHERE digest=? AND kind='site_preview'",
        (
            canonical_json(tampered),
            retained["project"]["preview_deployment_digest"],
        ),
    )
    with pytest.raises(ValueError, match="integrity"):
        current_preview(db, "adaos-site")


def test_proposed_change_set_previews_then_commits_through_one_path() -> None:
    db = sqlite3.connect(":memory:")
    source = _source("revision-one", CREATED)
    commit_revision(
        db,
        project_id="adaos-site",
        title="AdaOS",
        source=source,
        authoring_context=_context(source),
        change_set=_change_set(source, value=source["resources"][1]["data"]["body"]),
    )
    proposal = _change_set(
        source, value="Previewed before commit", identity="change-two"
    )
    candidate = propose_change_set(db, project_id="adaos-site", change_set=proposal)
    assert candidate["preview"]["source"]["resources"][1]["data"]["body"] == (
        "Previewed before commit"
    )
    assert project_state(db, "adaos-site")["active_revision_id"] == "revision-one"

    committed = commit_proposed_change_set(
        db,
        project_id="adaos-site",
        change_set_id="change-two",
        revision_id="revision-two",
        created_at="2026-11-03T00:01:00Z",
        authoring_context=_context(source, identity="context-two"),
        title="AdaOS",
    )
    assert committed["revision"]["parent_revision_id"] == "revision-one"
    assert (
        current_preview(db, "adaos-site")["source"]["resources"][1]["data"]["body"]
        == "Previewed before commit"
    )
    assert [item["revision_id"] for item in revision_history(db, "adaos-site")] == [
        "revision-two",
        "revision-one",
    ]
    assert change_set_history(db, "adaos-site")[0]["status"] == "committed"

    divergent = _source("revision-three", "2026-11-03T00:02:00Z")
    divergent["resources"][1]["data"]["body"] = "Not represented by the operation"
    stale_operation = _change_set(
        committed["preview"]["source"],
        value="A different value",
        identity="change-three",
    )
    with pytest.raises(ValueError, match="semantic change set replay"):
        commit_revision(
            db,
            project_id="adaos-site",
            title="AdaOS",
            source=divergent,
            authoring_context=_context(divergent, identity="context-three"),
            change_set=stale_operation,
        )


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
