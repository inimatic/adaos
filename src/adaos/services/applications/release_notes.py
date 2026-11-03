"""Read explicit release notes from existing successful publication operations.

This projection owns no storage and never treats a draft, application summary,
or unrelated component changelog as notes for an immutable release.
"""
from collections.abc import Iterable, Mapping


def publication_notes(operations: Iterable[Mapping], *, application_id: str,
                      publisher_ref: str) -> dict[str, str]:
    result = {}
    ordered = sorted(operations, key=lambda item: (str(item.get('updated_at') or ''),
        str(item.get('operation_id') or '')), reverse=True)
    for item in ordered:
        if (item.get('application_id') != application_id
                or item.get('subnet_ref') != publisher_ref
                or item.get('status') != 'succeeded'
                or item.get('action') not in {'publish_to_registry', 'publish_stable_source'}):
            continue
        intent = item.get('intent')
        if not isinstance(intent, Mapping):
            continue
        digest, notes = intent.get('release_digest'), intent.get('release_notes')
        if isinstance(digest, str) and isinstance(notes, str) and digest not in result:
            result[digest] = notes.strip()[:20_000]
    return result
