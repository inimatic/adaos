import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from adaos.services.applications.configuration import ApplicationConfigurationStore, ConfigurationConflict


SCHEMA = {"type": "object", "properties": {"language": {"type": "string"},
          "page_size": {"type": "integer", "minimum": 1}, "optional": {"type": ["string", "null"]}},
          "required": ["language", "page_size"], "additionalProperties": False}


@pytest.fixture
def store(tmp_path):
    value = ApplicationConfigurationStore(tmp_path, "app", "skill:example")
    value.set_stable(release_digest="stable-1", schema=SCHEMA,
                     values={"language": "ru", "page_size": 10, "optional": "old"},
                     credentials={"api": "credential:owner-scoped-api"}, expected_revision=0)
    return value


def prepare(store, candidate="beta-1", schema=SCHEMA, defaults=None):
    return store.prepare_beta(candidate_id=candidate, release_digest=candidate,
                              source_release_digest="stable-1", schema=schema, defaults=defaults or {},
                              expected_revision=store.read()["revision"])


def test_settings_and_credential_references_survive_two_betas_and_acceptance(store):
    first = prepare(store)
    assert first["beta"]["values"] == first["stable"]["values"]
    changed = store.update_beta(candidate_id="beta-1", schema=SCHEMA,
        values={"language": "en", "page_size": 25}, credentials=first["beta"]["credentials"],
        expected_revision=first["revision"])
    second = prepare(store, "beta-2")
    assert second["beta"]["values"] == changed["beta"]["values"]
    assert "optional" not in second["beta"]["values"]
    assert second["stable"]["values"]["page_size"] == 10
    assert store.credential_reference("api", candidate_id="beta-2") == "credential:owner-scoped-api"
    assert prepare(store, "beta-2") == second
    accepted = store.adopt_beta(candidate_id="beta-2", expected_revision=second["revision"])
    assert accepted["stable"]["values"] == changed["beta"]["values"]
    assert accepted["beta"] is None
    assert store.credential_reference("api") == "credential:owner-scoped-api"
    assert store.adopt_beta(candidate_id="beta-2", expected_revision=accepted["revision"]) == accepted


def test_new_beta_defaults_do_not_discard_existing_overrides(store):
    first = prepare(store)
    store.update_beta(candidate_id="beta-1", schema=SCHEMA,
        values={"language": "en", "page_size": 25, "optional": None}, credentials={},
        expected_revision=first["revision"])
    result = prepare(store, "beta-2", defaults={"page_size": 5})
    assert result["beta"]["values"] == {"language": "en", "page_size": 25, "optional": None}
    assert result["beta"]["credentials"] == {}


def test_incompatible_beta_does_not_reset_configuration(store):
    prepare(store)
    before = store.path.read_bytes()
    incompatible = {**SCHEMA, "required": [*SCHEMA["required"], "new_required_setting"]}
    with pytest.raises(ConfigurationConflict, match="incompatible"):
        prepare(store, "beta-2", schema=incompatible)
    assert store.path.read_bytes() == before


def test_stale_candidate_or_revision_cannot_mutate_current_settings(store):
    prepare(store)
    second = prepare(store, "beta-2")
    with pytest.raises(ConfigurationConflict, match="not active"):
        store.adopt_beta(candidate_id="beta-1", expected_revision=second["revision"])
    with pytest.raises(ConfigurationConflict, match="changed"):
        store.deactivate_beta(candidate_id="beta-2", expected_revision=0)
    with pytest.raises(ConfigurationConflict, match="inactive"):
        store.credential_reference("api")
    with pytest.raises(ConfigurationConflict, match="inactive"):
        store.set_stable(release_digest="stable-1", schema=SCHEMA, values={"language": "en", "page_size": 5},
                         credentials={}, expected_revision=second["revision"])


def test_return_to_stable_retains_overrides_but_detects_later_stable_changes(store):
    first = prepare(store)
    store.deactivate_beta(candidate_id="beta-1", expected_revision=first["revision"])
    assert store.credential_reference("api")
    prepare(store)
    store.deactivate_beta(candidate_id="beta-1", expected_revision=store.read()["revision"])
    store.set_stable(release_digest="stable-1", schema=SCHEMA, values={"language": "fr", "page_size": 5},
                     credentials={}, expected_revision=store.read()["revision"])
    with pytest.raises(ConfigurationConflict, match="underneath"):
        prepare(store, "beta-2")


def test_first_beta_without_stable_requires_explicit_empty_base(tmp_path):
    store = ApplicationConfigurationStore(tmp_path, "new-app", "skill:new")
    with pytest.raises(ConfigurationConflict, match="base"):
        prepare(store)
    first = store.prepare_beta(candidate_id="first", release_digest="release-first",
        source_release_digest=None, schema=SCHEMA, defaults={"language": "ru", "page_size": 10}, expected_revision=0)
    assert first["stable"] is None and first["beta"]["credentials"] == {}
    accepted = store.adopt_beta(candidate_id="first", expected_revision=1)
    assert accepted["stable"]["values"]["language"] == "ru"


def test_concurrent_editors_are_serialized_by_revision(store):
    prepare(store)
    revision = store.read()["revision"]
    def write(page_size):
        try:
            other = ApplicationConfigurationStore(store.path.parents[2], "app", "skill:example")
            other.update_beta(candidate_id="beta-1", schema=SCHEMA, values={"language": "ru", "page_size": page_size},
                              credentials={}, expected_revision=revision)
            return True
        except ConfigurationConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [20, 30])) == [False, True]


@pytest.mark.parametrize("bad_schema", [
    {"type": "object", "$ref": "https://example.com/schema"},
    {"type": "object", "$id": "https://example.com/schema"},
    {"type": "object", "properties": {"password": {"type": "string", "writeOnly": True}}},
])
def test_configuration_cannot_retrieve_schemas_or_store_secret_fields(store, bad_schema):
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        prepare(store, schema=bad_schema)
    assert store.path.read_bytes() == before


def test_raw_secrets_rejected_and_diagnostics_do_not_echo_values(store):
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="references"):
        store.set_stable(release_digest="stable-1", schema=SCHEMA, values={"language": "ru", "page_size": 10},
                         credentials={"api": "actual-secret-value"}, expected_revision=1)
    with pytest.raises(ConfigurationConflict) as exc:
        store.set_stable(release_digest="stable-1", schema=SCHEMA, values={"language": "private-title", "page_size": -1},
                         credentials={}, expected_revision=1)
    assert "private-title" not in str(exc.value)
    assert "actual-secret-value" not in store.path.read_text(encoding="utf-8")
    assert store.path.read_bytes() == before


def test_store_is_owner_separated_and_not_inside_runtime_or_package(store, tmp_path):
    other = ApplicationConfigurationStore(tmp_path, "other-app", "skill:example")
    assert other.read()["stable"] is None
    assert not other.path.exists()
    assert store.path.is_relative_to(tmp_path / "applications/configuration")
    assert json.loads(store.path.read_text(encoding="utf-8"))["component_ref"] == "skill:example"
