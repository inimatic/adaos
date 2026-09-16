from contextlib import closing
import sqlite3

import pytest

from adaos.domain.application import RuntimeSelection
from adaos.services.applications.configuration import ApplicationConfigurationStore
from adaos.services.applications.data_lifecycle import LocalApplicationDataLifecycle, OwnedDataComponent, declared_databases
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict


DIGEST = "sha256:" + "a" * 64
CONFIG = {"schema": {"type": "object", "properties": {"page_size": {"type": "integer"}},
                     "required": ["page_size"], "additionalProperties": False}, "defaults": {"page_size": 10}}


def sql(path, statement, parameters=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute(statement, parameters).fetchall()
        connection.commit()
        return result


def manifest(number):
    return {"configuration": CONFIG, "data_lifecycle": {"schema": "adaos.skill.data_lifecycle.v1",
        "execution": "native_tools", "databases": [{"path": "records.sqlite", "migrations": [
            {"version": i, "name": f"field{i}", "statements": [f"ALTER TABLE records ADD COLUMN field{i} TEXT"]}
            for i in range(1, number + 1)]}]}}


def seed(tmp_path):
    stable = tmp_path / "workspace/data"
    sql(stable / "records.sqlite", "CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
    sql(stable / "records.sqlite", "INSERT INTO records VALUES(1,'original')")
    state = tmp_path / "state"
    channel = ApplicationRuntimeChannel(state, "sample")
    channel.select(RuntimeSelection(webspace_id="desktop", application_id="sample", source="stable_installation",
        release_digest=DIGEST, runtime_root_ref="workspace", revision=1), expected_revision=0)
    return state, stable, channel


def coordinator(tmp_path, number, stable_manifest=None, target_root=None):
    stable = tmp_path / "workspace/data"
    return LocalApplicationDataLifecycle(state_root=tmp_path / "state", private_root=tmp_path,
        application_id="sample", candidate_id=f"candidate{number}", release_digest="sha256:" + str(number) * 64,
        stable_digest=DIGEST if number == 1 else "sha256:" + str(number - 1) * 64,
        components=(OwnedDataComponent("skill:worker", stable, tmp_path / f"beta{number}/data", target_root or stable,
                                       stable_manifest or {}, manifest(number)),))


def test_two_complete_local_data_cutovers_preserve_records_and_settings(tmp_path):
    state, stable, channel = seed(tmp_path)
    calls = []
    for number in (1, 2):
        lifecycle = coordinator(tmp_path, number, manifest(number - 1) if number > 1 else {})
        beta = tmp_path / f"beta{number}/data/records.sqlite"

        def activate(key):
            with pytest.raises(RuntimeChannelConflict, match="fenced"):
                with channel.execution("workspace", DIGEST):
                    pass
            assert len(sql(beta, "SELECT * FROM records")) == number
            calls.append(("activate", key))
            return {"ok": True}

        admitted = lifecycle.prepare_beta(webspace_id="desktop", activate=activate)
        assert admitted["completed"]
        config = ApplicationConfigurationStore(state, "sample", "skill:worker")
        assert config.read()["beta"]["values"]["page_size"] == (10 if number == 1 else 20)
        config.update_beta(candidate_id=f"candidate{number}", schema=CONFIG["schema"], values={"page_size": 20},
                           credentials={}, expected_revision=config.read()["revision"])
        sql(beta, "INSERT INTO records(id,value) VALUES(?,?)", (number + 1, f"beta{number}"))

        def publish(key):
            assert len(sql(stable / "records.sqlite", "SELECT * FROM records")) == number + 1
            assert config.read()["stable"]["values"] == {"page_size": 20}
            calls.append(("publish", key))
            return {"ok": True}

        accepted = lifecycle.accept_beta(webspace_id="desktop", publish=publish)
        assert accepted["completed"]
        assert channel.read()[0].runtime_root_ref == "workspace"
        assert lifecycle.accept_beta(webspace_id="desktop", publish=publish)["completed"]
        assert lifecycle.prepare_beta(webspace_id="desktop", activate=activate)["completed"]
        assert channel.read()[0].runtime_root_ref == "workspace"
    assert len(calls) == 4
    assert sql(stable / "records.sqlite", "SELECT id,value FROM records ORDER BY id") == [(1, "original"), (2, "beta1"), (3, "beta2")]


def test_publication_interruption_keeps_both_runtimes_fenced_and_retries_exact_effect(tmp_path):
    _state, _stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    sql(tmp_path / "beta1/data/records.sqlite", "INSERT INTO records(id,value) VALUES(2,'beta')")
    keys = []

    def fail(key):
        keys.append(key)
        raise SystemExit("publication response lost")

    with pytest.raises(SystemExit):
        lifecycle.accept_beta(webspace_id="desktop", publish=fail)
    with pytest.raises(RuntimeChannelConflict, match="fenced"):
        with channel.execution("trial:candidate1", "sha256:" + "1" * 64):
            pass
    result = coordinator(tmp_path, 1).accept_beta(webspace_id="desktop", publish=lambda key: keys.append(key) or {"ok": True})
    assert result["completed"] and len(keys) == 2 and keys[0] == keys[1]


def test_conflicting_stable_writes_are_not_silently_overwritten(tmp_path):
    _state, stable, _channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    sql(stable / "records.sqlite", "INSERT INTO records(id,value) VALUES(3,'external')")
    with pytest.raises(RuntimeChannelConflict, match="Stable data changed"):
        lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: pytest.fail("Must not publish conflicting data"))
    assert len(sql(stable / "records.sqlite", "SELECT * FROM records")) == 2


@pytest.mark.parametrize("extra", [{"events": {"subscribe": ["timer.tick"]}}, {"service": {"run": "worker"}},
                                  {"runtime": {"lifecycle": {"after_activate": "start"}}}])
def test_background_workers_are_rejected_without_false_drain_receipts(tmp_path, extra):
    seed(tmp_path)
    with pytest.raises(ValueError, match="verified owner drain"):
        coordinator(tmp_path, 1, extra)


def test_undeclared_data_remains_untouched(tmp_path):
    _state, stable, _channel = seed(tmp_path)
    (stable / "attachment.txt").write_text("private attachment", encoding="utf-8")
    with pytest.raises(ValueError, match="Undeclared runtime data"):
        coordinator(tmp_path, 1).prepare_beta(webspace_id="desktop", activate=lambda _: pytest.fail("Must not activate"))
    assert (stable / "attachment.txt").read_text() == "private attachment"


@pytest.mark.parametrize("path", ["../other.sqlite", "C:/other.sqlite", "a/../other.sqlite", "files/secrets.json"])
def test_manifest_store_paths_are_owner_relative(path):
    value = manifest(1)
    value["data_lifecycle"]["databases"][0]["path"] = path
    with pytest.raises(ValueError, match="owner data path"):
        declared_databases(value)


@pytest.mark.parametrize("phase", ["beta", "stable"])
def test_existing_destination_data_is_not_discarded(tmp_path, phase):
    seed(tmp_path)
    target = tmp_path / "workspace/new-bucket/data"
    lifecycle = coordinator(tmp_path, 1, target_root=target)
    destination = tmp_path / "beta1/data" if phase == "beta" else target
    sql(destination / "records.sqlite", "CREATE TABLE records(id INTEGER)")
    sql(destination / "records.sqlite", "INSERT INTO records VALUES(99)")
    with pytest.raises(RuntimeChannelConflict, match="Target data already exists"):
        lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
        lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: pytest.fail("Must not overwrite unrelated data"))
    assert sql(destination / "records.sqlite", "SELECT * FROM records") == [(99,)]


def test_successful_minor_version_data_adoption_preserves_old_bucket(tmp_path):
    seed(tmp_path)
    target = tmp_path / "workspace/new-bucket/data"
    lifecycle = coordinator(tmp_path, 1, target_root=target)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    sql(tmp_path / "beta1/data/records.sqlite", "INSERT INTO records(id,value) VALUES(2,'beta')")
    lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": True})
    assert len(sql(target / "records.sqlite", "SELECT * FROM records")) == 2
    assert len(sql(tmp_path / "workspace/data/records.sqlite", "SELECT * FROM records")) == 1


def test_data_declaration_is_consistent_with_both_skill_schemas():
    import json
    from pathlib import Path
    import jsonschema
    declaration = manifest(1)["data_lifecycle"]
    root = Path(__file__).resolve().parents[1] / "src/adaos"
    schemas = [json.loads((root / path).read_text(encoding="utf-8"))["properties"]["data_lifecycle"]
               for path in ("abi/skill.schema.json", "services/skill/skill_schema.json")]
    assert schemas[0] == schemas[1]
    for schema in schemas:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(declaration, schema)
        for path in ("../records.sqlite", "a/../b", "a//b", "/outside", "C:/outside"):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate({**declaration, "databases": [{"path": path, "migrations": []}]}, schema)


def test_failed_beta_activation_can_restore_stable_settings_without_touching_data(tmp_path):
    state, stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    with pytest.raises(RuntimeChannelConflict, match="not verified"):
        lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": False})
    config = ApplicationConfigurationStore(state, "sample", "skill:worker")
    assert config.read()["beta"]["active"]
    failed_beta = (tmp_path / "beta1/data/records.sqlite").read_bytes()
    result = lifecycle.abort_beta_preparation(verify_source=lambda _: {"ok": True})
    assert result["aborted"] and not result["completed"]
    assert config.read()["beta"]["active"] is False
    assert sql(stable / "records.sqlite", "SELECT * FROM records") == [(1, "original")]
    assert (tmp_path / "beta1/data/records.sqlite").read_bytes() == failed_beta
    with channel.execution("workspace", DIGEST):
        pass
    assert lifecycle.abort_beta_preparation(verify_source=lambda _: pytest.fail("No duplicate recovery")) == result
    with pytest.raises(RuntimeChannelConflict, match="cancelled"):
        lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: pytest.fail("Do not reuse cancelled Candidate"))


def test_rejected_completed_beta_restores_stable_channel_and_retains_beta_data(tmp_path):
    state, stable, channel = seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    beta = tmp_path / "beta1/data/records.sqlite"
    sql(beta, "INSERT INTO records(id,value) VALUES(2,'beta-only')")
    config = ApplicationConfigurationStore(state, "sample", "skill:worker")
    assert config.read()["beta"]["active"] is True

    result = lifecycle.reject_beta(webspace_id="desktop", verify_source=lambda _: {"ok": True})

    selected = channel.read()
    assert result["completed"] and selected is not None
    assert len(selected) == 1
    assert selected[0].runtime_root_ref == "workspace"
    assert selected[0].release_digest == DIGEST
    assert selected[0].revision == 3
    assert config.read()["beta"]["active"] is False
    assert sql(stable / "records.sqlite", "SELECT id,value FROM records") == [(1, "original")]
    assert sql(beta, "SELECT id,value FROM records ORDER BY id") == [(1, "original"), (2, "beta-only")]
    with channel.execution("workspace", DIGEST):
        pass
    assert lifecycle.reject_beta(
        webspace_id="desktop", verify_source=lambda _: pytest.fail("No duplicate compensation")
    ) == result


def test_rejected_first_beta_clears_unpublished_runtime_channel(tmp_path):
    lifecycle = LocalApplicationDataLifecycle(
        state_root=tmp_path / "state",
        private_root=tmp_path,
        application_id="sample",
        candidate_id="candidate1",
        release_digest="sha256:" + "1" * 64,
        stable_digest=None,
        components=(),
    )
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    assert lifecycle.channel.read()[0].runtime_root_ref == "trial:candidate1"

    lifecycle.reject_beta(webspace_id="desktop", verify_source=lambda _: {"ok": True})

    assert lifecycle.channel.read() == ()


def test_recovery_cannot_cancel_after_stable_adoption_started(tmp_path):
    seed(tmp_path)
    lifecycle = coordinator(tmp_path, 1)
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    with pytest.raises(RuntimeChannelConflict):
        lifecycle.accept_beta(webspace_id="desktop", publish=lambda _: {"ok": False})
    with pytest.raises(RuntimeChannelConflict, match="adoption has started"):
        lifecycle.abort_beta_preparation(verify_source=lambda _: pytest.fail("Recover publication instead"))


def test_beta_does_not_silently_inherit_credentials_for_a_changed_purpose(tmp_path):
    from adaos.services.applications.configuration import ConfigurationConflict
    state, stable, _channel = seed(tmp_path)
    previous = manifest(0)
    previous["configuration"] = {**CONFIG, "credentials": {"token": {"purpose": "Original explicit purpose"}}}
    target = manifest(1)
    target["configuration"] = {**CONFIG, "credentials": {"token": {"purpose": "Different purpose"}}}
    store = ApplicationConfigurationStore(state, "sample", "skill:worker")
    store.set_stable(release_digest=DIGEST, schema=CONFIG["schema"], values=CONFIG["defaults"],
                     credentials={"token": "credential:" + "a" * 32}, expected_revision=0)
    lifecycle = LocalApplicationDataLifecycle(state_root=state, private_root=tmp_path, application_id="sample",
        candidate_id="candidate", release_digest="sha256:" + "b" * 64, stable_digest=DIGEST,
        components=(OwnedDataComponent("skill:worker", stable, tmp_path / "beta/data", stable, previous, target),))
    with pytest.raises(ConfigurationConflict, match="purpose changed"):
        lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: pytest.fail("Owner review required"))
    assert store.read()["beta"] is None
