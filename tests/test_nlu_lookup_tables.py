from __future__ import annotations


def test_desktop_lookup_tables_collect_workspace_ids() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables, lookup_values, rasa_lookup_entries

    payload = collect_desktop_lookup_tables(get_ctx(), webspace_id="desktop")

    assert payload["ok"] is True
    assert payload["webspace_id"] == "desktop"
    assert "apps_catalog" in lookup_values(payload, "modal_id")
    assert "nlu_teacher_modal" in lookup_values(payload, "modal_id")
    assert "nlu_teacher_app" in lookup_values(payload, "app_id")
    assert "web_desktop" in lookup_values(payload, "scenario_id")
    assert lookup_values(payload, "webspace_id") == ["desktop"]

    rasa_entries = rasa_lookup_entries(payload)
    assert any(entry.get("lookup") == "modal_id" for entry in rasa_entries)
    assert any(entry.get("lookup") == "scenario_id" for entry in rasa_entries)
