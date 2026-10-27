from adaos.services.scenario.webspace_components.materialization_runtime import payload_cache_identity


def test_content_cache_key_includes_override_and_policy_identity():
    identity = {"key_hash": "release-policy-user", "application_id": "applications"}
    first = payload_cache_identity(identity, {"ui": {"title": "one"}})
    assert first == payload_cache_identity(identity, {"ui": {"title": "one"}})
    assert first != payload_cache_identity(identity, {"ui": {"title": "two"}})
    assert first != payload_cache_identity(identity | {"key_hash": "different-policy"}, {"ui": {"title": "one"}})
    assert identity["key_hash"] == "release-policy-user"


def test_content_order_is_irrelevant_and_unscoped_content_cannot_acquire_authority():
    assert payload_cache_identity({"key_hash": "scope"}, {"a": 1, "b": 2}) == payload_cache_identity(
        {"key_hash": "scope"}, {"b": 2, "a": 1})
    assert payload_cache_identity(None, {"ui": {}}) is None
