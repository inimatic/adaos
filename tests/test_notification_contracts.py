from copy import deepcopy

import pytest

from adaos.domain.notifications import NotificationError, compile_manifest, render
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.policy import evaluate
from adaos.services.user.attention_preferences import AttentionPreferences, default_preferences


def manifest():
    return {"schema": "adaos.notifications.manifest.v1", "categories": [{
        "id": "review.completed", "title_key": "review.title", "purpose_key": "review.purpose",
        "source_event": "review.completed.v1", "payload_schema": {
            "type": "object", "properties": {"title": {"type": "string", "maxLength": 200}},
            "required": ["title"], "additionalProperties": False},
        "recipient_policy": "current_user", "default_mode": "center_only",
        "maximum_urgency": "normal", "sensitivity": "private",
        "template": {"en": "Completed: {title}", "ru": "Готово: {title}"}, "lifetime_seconds": 86400,
    }]}


def record():
    return {"principal_ref": "user:alice", "subnet_ref": "subnet:A", "application_id": "reviews",
            "category_id": "review.completed", "source_id": "R1", "source_state": "active",
            "expires_at": 2000000000, "content_revision": 1, "admitted_endpoint_ids": ["max:1"],
            "admitted_endpoint_revisions": {"max:1": 1}}


def endpoint():
    return {"endpoint_id": "max:1", "principal_ref": "user:alice", "subnet_ref": "subnet:A",
            "status": "active", "expires_at": None, "revision": 1}


def test_compile_disclosure_and_localized_safe_template():
    compiled = compile_manifest(manifest())
    assert compiled["permission_disclosure"][0]["id"] == "review.completed"
    assert render(compiled["categories"]["review.completed"], {"title": "A"}, "ru") == "Готово: A"
    assert compile_manifest(manifest())["digest"] == compiled["digest"]
    with pytest.raises(NotificationError):
        render(compiled["categories"]["review.completed"], {"title": "A", "foreign": True})


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(critical=True),
    lambda c: c.update(default_mode="immediate"),
    lambda c: c.update(template={"en": "{title.__class__}"}),
    lambda c: c.update(payload_schema={"$ref": "https://foreign.test/schema"}),
    lambda c: c.update(recipient_policy="all_users"),
])
def test_invalid_or_unbounded_declaration_is_rejected(mutate):
    value = manifest()
    mutate(value["categories"][0])
    with pytest.raises((NotificationError, ValueError)):
        compile_manifest(value)


def test_identity_preference_cas_preserves_legacy_profile_and_subnet_scope():
    from adaos.domain.notifications import NotificationConflict
    ctx = get_ctx()
    ctx.kv.set("users/alice/preferences.v0", {"theme": "dark"})
    prefs = AttentionPreferences(ctx)
    value = prefs.read("user:alice", "subnet:A")
    value["rules"] = [{"scope": {"category_id": "review.completed"},
                       "choice": {"mode": "off", "endpoint_ids": []}}]
    saved = prefs.write("user:alice", "subnet:A", value, expected_revision=0)
    assert saved["revision"] == 1
    with pytest.raises(NotificationConflict):
        prefs.write("user:alice", "subnet:A", value, expected_revision=0)
    assert prefs.read("user:alice", "subnet:B")["revision"] == 0
    assert prefs.read("user:bob", "subnet:A")["revision"] == 0
    assert ctx.kv.get("users/alice/preferences.v0") == {"theme": "dark"}


def test_hard_mute_survives_more_specific_enable_and_quiet_exception():
    prefs = default_preferences()
    prefs["rules"] = [
        {"scope": {"category_id": "review.completed"}, "choice": {"mode": "off", "endpoint_ids": []}},
        {"scope": {"source_id": "R1"}, "choice": {
            "mode": "immediate", "endpoint_ids": ["max:1"], "quiet_exception": True}},
    ]
    result = evaluate(record(), prefs, [endpoint()], now=1800000000)
    assert result["reason"] == "muted"
    assert result["alert"] is False


def test_endpoints_do_not_expand_historical_selection_or_cross_user():
    prefs = default_preferences()
    prefs["defaults"] = {"mode": "immediate", "endpoint_ids": ["max:2"]}
    other = {**endpoint(), "endpoint_id": "max:2"}
    assert evaluate(record(), prefs, [other], now=1800000000)["alert"] is False
    assert evaluate(record(), prefs, [other], now=1800000000, admission=True)["alert"]
    other["principal_ref"] = "user:bob"
    assert evaluate(record(), prefs, [other], now=1800000000, admission=True)["alert"] is False


def test_quiet_until_expiry_and_presence_is_not_read():
    prefs = default_preferences()
    prefs["defaults"] = {"mode": "immediate", "endpoint_ids": ["max:1"]}
    prefs["quiet_hours"].update(enabled=True, timezone="Europe/Moscow")
    value = record()
    value["expires_at"] = 2000000000
    result = evaluate(value, prefs, [endpoint()], now="2026-11-08T20:00:00Z")
    assert result["reason"] == "quiet_hours"
    assert result["next_eligible_at"] == 1794200400  # 2026-11-09 05:00 UTC
    value["test_only"] = True
    assert evaluate(value, prefs, [endpoint()], now="2026-11-09T07:00:00Z")["reason"] == "preview"


@pytest.mark.parametrize("schema", [
    {"type":"string","pattern":"(a+)+$"},
    {"anyOf":[{"type":"string"},{"type":"number"}]},
    {"type":"object","properties":{"anything":{"type":"string"}}},
    {"type":"array","uniqueItems":True,"items":{"type":"string"}},
])
def test_payload_schema_cannot_run_unbounded_regex_combinators_or_open_structures(schema):
    value=manifest()
    value["categories"][0]["payload_schema"]["properties"]["title"]=schema
    with pytest.raises(NotificationError): compile_manifest(value)


def test_intent_and_error_abi_reject_authority_fields_and_describe_backpressure():
    from jsonschema import Draft202012Validator,ValidationError
    from adaos.sdk import notifications
    values={"category_id":"C","event_id":"E","source_id":"S","source_revision":1,"occurred_at":1,"payload":{}}
    schema=Draft202012Validator(notifications.contract("intent"))
    schema.validate(values)
    for field in ("principal_ref","namespace","author_ref","endpoint_id","bot_token"):
        with pytest.raises(ValidationError): schema.validate({**values,field:"forged"})
    error=notifications.NotificationBackpressure("producer_admission_capacity",retry_after_seconds=30)
    Draft202012Validator(notifications.contract("errors")).validate(error.as_dict())
    assert error.retryable is True
