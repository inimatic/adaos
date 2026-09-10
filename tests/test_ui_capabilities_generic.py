from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder_domain_packs import (
    domain_pack_ids_for_recipes,
    load_domain_pack,
)
from adaos.services.ui_capabilities import (
    evaluate_ui_request,
    get_ui_capability,
    qualify_ui_request,
    selected_ui_capabilities,
    ui_capability_catalog,
)


APPLICATION_PACKS = ("applications.compatibility.v1",)


def _empty_webui() -> dict:
    return {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "empty",
                        "layout": {
                            "type": "single",
                            "pattern": "stack",
                            "areas": [{"id": "main", "role": "main"}],
                        },
                        "widgets": [],
                    }
                }
            }
        },
    }


def test_generic_catalog_contains_no_subject_recipe() -> None:
    catalog = ui_capability_catalog()

    assert "recipe.application_manager" not in {
        item["id"] for item in catalog["recipes"]
    }
    with pytest.raises(KeyError):
        get_ui_capability("recipe.application_manager")


def test_generic_request_cannot_select_subject_pack_by_wording_or_id() -> None:
    for request in (
        "Create an Applications marketplace with stable and prerelease versions",
        "Use recipe.application_manager",
    ):
        qualification = qualify_ui_request(request)
        selection = selected_ui_capabilities(request)

        assert qualification["surface_kind"] == "unspecified"
        assert qualification["input_attribution"] == {
            "profile": "generic",
            "domain_packs": [],
        }
        assert "recipe.application_manager" not in selection["root_item_ids"]
        assert selection["input_attribution"]["domain_packs"] == []


def test_explicit_compatibility_pack_preserves_legacy_qualification() -> None:
    request = (
        "Create the Applications marketplace with installed and prerelease versions"
    )
    qualification = qualify_ui_request(request, domain_packs=APPLICATION_PACKS)
    selection = selected_ui_capabilities(request, domain_packs=APPLICATION_PACKS)

    assert qualification["surface_kind"] == "application_manager"
    assert selection["root_item_ids"] == ["recipe.application_manager"]
    assert (
        selection["input_attribution"]["domain_packs"][0]["pack_id"]
        == APPLICATION_PACKS[0]
    )
    assert selection["domain_policy"]["locale_dictionaries_required"] is True
    assert selection["repair_guidance"]["general"]
    assert (
        get_ui_capability("recipe.application_manager", domain_packs=APPLICATION_PACKS)[
            "id"
        ]
        == "recipe.application_manager"
    )


def test_generic_evaluation_records_clean_attribution() -> None:
    result = evaluate_ui_request("Create a simple page", _empty_webui())

    assert result["input_attribution"] == {"profile": "generic", "domain_packs": []}


def test_generic_core_sources_have_no_subject_vocabulary() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "src" / "adaos" / "services" / "ui_capabilities.py",
        root / "src" / "adaos" / "abi" / "ui.capability_catalog.v1.json",
        root / "src" / "adaos" / "services" / "builder" / "prompt_rule_capsules.json",
    )
    forbidden = (
        "recipe.application_manager",
        "applications.list",
        "applications.show",
        "applications.plan",
        "applications.apply",
        "shopping_list",
        "recipe_book",
        "adaos.application.lifecycle",
        "adaos.research.",
    )

    for path in paths:
        content = path.read_text(encoding="utf-8").casefold()
        assert not [term for term in forbidden if term.casefold() in content], path


def test_domain_pack_is_versioned_and_content_addressed() -> None:
    pack = load_domain_pack(APPLICATION_PACKS[0])

    assert pack["schema"] == "adaos.builder.domain_pack.v1"
    assert pack["classification"] == "compatibility"
    assert pack["digest"].startswith("sha256:")
    assert json.dumps(pack["ui_recipes"], ensure_ascii=False)
    assert domain_pack_ids_for_recipes(["recipe.application_manager"]) == [
        APPLICATION_PACKS[0]
    ]
    assert domain_pack_ids_for_recipes(["recipe.unknown"]) == []
