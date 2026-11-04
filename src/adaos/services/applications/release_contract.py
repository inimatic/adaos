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
                "created_at": {"type": ["string", "null"]},
                "release_notes": {"type": ["string", "null"], "maxLength": 20000,
                    "description": "Explicit publisher notes for this exact digest from a successful publication. Null when absent; do not substitute the Application description or infer changes."},
                "notes_status": {"enum": ["available", "missing"],
                    "description": "Missing notes are a normal historical state. Render a localized no-release-notes message; this does not block release selection or opening."},
                "channels": {"type": "array", "items": {"enum": ["stable", "prerelease"]}, "description":
                    "Effective local channel bindings: stable is Stable, prerelease is the opt-in Beta track. These do not prove public distribution. Release lifecycle is historical evidence; it does not imply a current channel or publication. Use public_stable_available/public_beta_available for Public release selection."},
                "public_stable_available": {"type": "boolean", "description": "Exact digest appears in the authoritative Core public_stable dashboard position. Filter equals true to offer Public Stable selection, including a published predecessor of a newer Private Stable."},
                "public_beta_available": {"type": "boolean", "description": "Exact digest appears in the authoritative Core public_beta dashboard position. Filter equals true to offer explicit Public Beta/prerelease selection."},
                "install_kind": {"enum": ["install", "update"], "description": "Core observed install/update kind for an explicitly reviewed selection; use the current installation revision and exact selected digest."},
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


def with_public_release_options(releases, model):
    """Join the existing public projection; local channels cannot imply an offer."""
    positions = (model.get('dashboard') or {}).get('positions') or ()
    offers = {row['id']: row.get('release_digest') for row in positions
              if row.get('scope') == 'public' and row.get('id') in {'public_beta', 'public_stable'}}
    return [{**release,
        'public_stable_available': bool(offers.get('public_stable') and offers['public_stable'] == release.get('release_digest')),
        'public_beta_available': bool(offers.get('public_beta') and offers['public_beta'] == release.get('release_digest')),
        'install_kind': installation_plan_kind(model),
    } for release in releases]


def installation_plan_kind(model):
    installation = model.get('installation') or {}
    return 'update' if installation and installation.get('status') != 'removed' else 'install'
