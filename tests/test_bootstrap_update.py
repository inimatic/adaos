from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS


def test_core_version_metadata_is_bootstrap_promoted() -> None:
    assert "pyproject.toml" in BOOTSTRAP_CRITICAL_PATHS


def test_bounded_io_helper_is_bootstrap_promoted() -> None:
    assert "src/adaos/services/bounded_io.py" in BOOTSTRAP_CRITICAL_PATHS


def test_model_artifact_helpers_are_bootstrap_promoted() -> None:
    assert "src/adaos/services/models/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/models/artifacts.py" in BOOTSTRAP_CRITICAL_PATHS


def test_operational_event_domain_helpers_are_bootstrap_promoted() -> None:
    assert "src/adaos/domain/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/event_envelope.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/projection_keys.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/projection_record.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/projection_subscription.py" in BOOTSTRAP_CRITICAL_PATHS


def test_managed_rasa_service_skill_bootstrap_helpers_are_promoted() -> None:
    assert "src/adaos/services/nlu/rasa_skill_installer.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/interpreter_data/rasa_nlu_service_skill/skill.yaml" in BOOTSTRAP_CRITICAL_PATHS


def test_skill_facing_sdk_surfaces_are_bootstrap_promoted() -> None:
    assert "src/adaos/sdk/data/device_access.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/endpoint_audio.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/media.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/redevice.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/endpoint_audio.py" in BOOTSTRAP_CRITICAL_PATHS

