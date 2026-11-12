"""Governed static-site authoring contracts, compiler, and retained store."""

from .compiler import compile_site_revision, default_component_catalog
from .contracts import (
    apply_change_set,
    canonical_json,
    digest,
    normalize_same_page_target,
    normalize_site_source,
    validate_contract,
)
from .authoring import (
    authoring_diagnostics,
    authoring_projection,
    inspect_source_anchor,
    intake_material,
    propose_semantic_changes,
    read_material_object,
    update_material_usage,
)
from .store import (
    change_set_history,
    commit_proposed_change_set,
    commit_revision,
    current_authoring_context,
    current_preview,
    ensure_store,
    material_inventory,
    project_state,
    propose_change_set,
    revision_history,
)
from .chat_authoring import (
    CHAT_OUTPUT_SCHEMA, CHAT_PROMPT, MAX_STEPS, build_chat_context,
    read_chat_source, propose_chat_change,
)
from .source_materials import source_files, save_source_file, delete_source_file, read_source_file
from .component_editor import component_editor, component_field_operations

__all__ = [
    "CHAT_OUTPUT_SCHEMA", "CHAT_PROMPT", "MAX_STEPS", "build_chat_context",
    "read_chat_source", "propose_chat_change", "source_files", "save_source_file",
    "delete_source_file", "read_source_file", "component_editor", "component_field_operations",
    "apply_change_set",
    "authoring_diagnostics",
    "authoring_projection",
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
    "inspect_source_anchor",
    "intake_material",
    "material_inventory",
    "normalize_same_page_target",
    "normalize_site_source",
    "project_state",
    "propose_change_set",
    "propose_semantic_changes",
    "read_material_object",
    "revision_history",
    "update_material_usage",
    "validate_contract",
]
