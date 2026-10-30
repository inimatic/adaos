"""Literal value search shared by local and disposable resource providers."""
import json


def search_text(record, fields=None):
    if fields is None:
        # Preserve the historical whole-record behavior for existing queries.
        return json.dumps(record, ensure_ascii=False, sort_keys=True).lower()
    values = []
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        values.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True))
    return " ".join(values).lower()
