"""Independent acceptance, materialized only after each model turn finishes.

The system read-model test uses a contracted provider seam, not a live subnet.
The storage test executes the actual public SDK's injected owner-data boundary.
This suite does not claim deployed-browser or publication acceptance.
"""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest


def load_candidate():
    path = Path(os.environ["ADAOS_ABLATION_SOURCE"])
    spec = importlib.util.spec_from_file_location("ablation_candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASK = os.environ["ADAOS_ABLATION_TASK"]


@pytest.mark.skipif(TASK != "durable_notes", reason="different fixed task")
def test_owner_storage_empty_replace_unicode_and_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_SKILL_INTERNAL_DATA_ROOT", str(tmp_path / "owner-data"))
    candidate = load_candidate()
    assert candidate.list_notes() == {"items": []}
    assert candidate.save_note("b", "Привет") == {"ok": True, "id": "b"}
    assert candidate.save_note("a", "one") == {"ok": True, "id": "a"}
    assert candidate.save_note("a", "two") == {"ok": True, "id": "a"}
    assert load_candidate().list_notes() == {"items": [{"id": "a", "text": "two"}, {"id": "b", "text": "Привет"}]}
    assert any(path.is_file() for path in (tmp_path / "owner-data").rglob("*"))


@pytest.mark.skipif(TASK != "operational_read_model", reason="different fixed task")
@pytest.mark.parametrize("value,expected", [
    ({"subnet": {"available": True, "subnet_id": "sn-fixed", "display_name": "Lab"}},
     {"available": True, "subnet_id": "sn-fixed", "display_name": "Lab"}),
    ({"subnet": {"available": False, "subnet_id": "stale", "display_name": "Stale"}},
     {"available": False, "subnet_id": None, "display_name": None}),
    ({}, {"available": False, "subnet_id": None, "display_name": None}),
])
def test_admitted_read_model(value, expected):
    with patch("adaos.sdk.system.get_operational_snapshot", return_value=value) as provider:
        assert load_candidate().subnet_status("ws-fixed") == expected
        kwargs = provider.call_args.kwargs
        sections = kwargs["sections"]
        assert set([sections] if isinstance(sections, str) else sections) == {"summary"}
        assert kwargs["webspace_id"] == "ws-fixed"
        assert kwargs["limit"] == 10
        assert provider.call_count == 1


@pytest.mark.skipif(TASK != "operational_read_model", reason="different fixed task")
def test_provider_failure_never_fabricates_identity():
    with patch("adaos.sdk.system.get_operational_snapshot", side_effect=RuntimeError("not admitted")):
        assert load_candidate().subnet_status() == {"available": False, "subnet_id": None, "display_name": None}


@pytest.mark.skipif(TASK != "locale_identity", reason="different fixed task")
@pytest.mark.parametrize("locale,labels", [
    ("en", ["Save", "Cancel"]), ("ru", ["Сохранить", "Отмена"]),
    ("ru-RU", ["Сохранить", "Отмена"]), ("ru_RU", ["Сохранить", "Отмена"]),
    ("unknown", ["Save", "Cancel"]), ("", ["Save", "Cancel"]),
])
def test_localized_presentation_preserves_canonical_identity(locale, labels):
    assert load_candidate().action_items(locale) == [
        {"id": "save", "label": labels[0]}, {"id": "cancel", "label": labels[1]},
    ]
