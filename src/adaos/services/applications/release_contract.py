"""Browser-safe immutable release identity and effective channel bindings."""


def release_list_result_schema():
    text = {"type": "string"}
    return {"type": "object", "required": ["releases"], "properties": {
        "releases": {"type": "array", "items": {
            "type": "object", "required": ["application_id", "version", "release_digest", "channels"],
            "properties": {
                **{name: text for name in ("schema", "application_id", "publisher_ref", "legacy_project_id",
                    "version", "release_digest", "accepted_candidate_id", "lifecycle")},
                "published_at": {"type": ["string", "null"]},
                "channels": {"type": "array", "items": text, "description":
                    "Effective channel bindings for this exact digest. Release lifecycle is historical evidence; it does not imply a current channel or publication."},
                "project_release": {"type": "object", "properties": {
                    "project_id": text, "version": text, "release_digest": text,
                    "catalog": {"type": ["object", "null"], "additionalProperties": True},
                    "components": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                }, "additionalProperties": True},
                "acceptance_evidence_count": {"type": "integer", "minimum": 0},
                "provenance_refs": {"type": "array", "items": text},
                "addresses_report_ids": {"type": "array", "items": text},
            }, "additionalProperties": True,
        }},
    }, "additionalProperties": True}
