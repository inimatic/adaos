from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS


def test_model_artifact_helpers_are_bootstrap_promoted() -> None:
    assert "src/adaos/services/models/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/models/artifacts.py" in BOOTSTRAP_CRITICAL_PATHS

