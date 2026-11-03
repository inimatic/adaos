"""Stable SDK facade for governed static-site authoring."""

from adaos.services.site_studio import (
    apply_change_set,
    canonical_json,
    change_set_history,
    commit_proposed_change_set,
    commit_revision,
    compile_site_revision,
    current_authoring_context,
    current_preview,
    default_component_catalog,
    digest,
    ensure_store,
    material_inventory,
    project_state,
    propose_change_set,
    revision_history,
    validate_contract,
)

__all__ = [
    "apply_change_set",
    "canonical_json",
    "change_set_history",
    "commit_proposed_change_set",
    "commit_revision",
    "compile_site_revision",
    "current_authoring_context",
    "current_preview",
    "default_component_catalog",
    "digest",
    "ensure_store",
    "material_inventory",
    "project_state",
    "propose_change_set",
    "revision_history",
    "validate_contract",
]
