"""Executable state-proof rules also exposed to the Prototype design model."""

STATE_EVALUATION_POLICY = (
    'State count bounds are checked against STATIC authored fixture records using the collection permanent scope '
    'and the representative state explicit filters. A selection_filter is a runtime interaction, NOT an implicit '
    'state-proof filter: neither a selected-sounding label nor an assumed click narrows that static count. '
    'For a bounded subset proof declare its exact typed state filters; an unfiltered three-record fixture cannot '
    'claim max_items=2. Browser checks independently exercise the actual selection/query behavior.'
)

STATE_PROOF_RULES = {
    "collection_empty": {"view_roles": ["collection"], "filters": "none", "min_items": 0, "max_items": 0, "empty_state": True, "fixture_mode": "empty_override_of_same_collection"},
    "collection_items": {"view_roles": ["collection"], "filters": "optional", "min_items": 1},
    "field_predicate": {"view_roles": ["collection", "details", "editor"], "filters": "required", "min_items": 1, "visible_predicates": True},
    "query_empty": {"view_roles": ["collection"], "filters": "required", "min_items": 0, "max_items": 0, "empty_state": True, "query_controls": True},
}
