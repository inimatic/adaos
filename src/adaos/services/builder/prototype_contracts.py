"""Executable state-proof rules also exposed to the Prototype design model."""

STATE_PROOF_RULES = {
    "collection_empty": {"view_roles": ["collection"], "filters": "none", "min_items": 0, "max_items": 0, "empty_state": True, "fixture_mode": "empty_override_of_same_collection"},
    "collection_items": {"view_roles": ["collection"], "filters": "optional", "min_items": 1},
    "field_predicate": {"view_roles": ["collection", "details", "editor"], "filters": "required", "min_items": 1, "visible_predicates": True},
    "query_empty": {"view_roles": ["collection"], "filters": "required", "min_items": 0, "max_items": 0, "empty_state": True, "query_controls": True},
}
