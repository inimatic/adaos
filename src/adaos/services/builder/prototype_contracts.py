"""Executable state-proof rules also exposed to the Prototype design model."""

STATE_PROOF_RULES = {
    "collection_empty": {"filters": "none", "min_items": 0, "max_items": 0, "empty_state": True},
    "collection_items": {"filters": "optional", "min_items": 1},
    "field_predicate": {"filters": "required", "min_items": 1, "visible_predicates": True},
    "query_empty": {"filters": "required", "min_items": 0, "max_items": 0, "empty_state": True, "query_controls": True},
}
