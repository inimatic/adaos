import ast
from pathlib import Path

from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS


def test_core_version_metadata_is_bootstrap_promoted() -> None:
    assert "pyproject.toml" in BOOTSTRAP_CRITICAL_PATHS


def test_bounded_io_helper_is_bootstrap_promoted() -> None:
    assert "src/adaos/services/bounded_io.py" in BOOTSTRAP_CRITICAL_PATHS


def test_root_webui_helpers_are_bootstrap_promoted() -> None:
    assert "src/adaos/services/browser_assets.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/webui_contract.py" in BOOTSTRAP_CRITICAL_PATHS


def test_model_artifact_helpers_are_bootstrap_promoted() -> None:
    assert "src/adaos/services/models/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/models/artifacts.py" in BOOTSTRAP_CRITICAL_PATHS


def test_operational_event_domain_helpers_are_bootstrap_promoted() -> None:
    assert "src/adaos/domain/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/conversation.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/event_envelope.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/personalization_access.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/project_events.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/projection_keys.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/projection_record.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/projection_subscription.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/skill.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/skill_registry.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/types.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/domain/workspace_manifest.py" in BOOTSTRAP_CRITICAL_PATHS


def test_domain_package_reexports_are_bootstrap_promoted() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    domain_root = repo_root / "src" / "adaos" / "domain"
    tree = ast.parse((domain_root / "__init__.py").read_text(encoding="utf-8"))
    imported_paths: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        module_path = domain_root / Path(*node.module.split("."))
        for candidate in (module_path.with_suffix(".py"), module_path / "__init__.py"):
            if candidate.exists():
                imported_paths.add(candidate.relative_to(repo_root).as_posix())
                break

    assert not sorted(imported_paths - set(BOOTSTRAP_CRITICAL_PATHS))


def test_managed_rasa_service_skill_bootstrap_helpers_are_promoted() -> None:
    assert "src/adaos/services/nlu/rasa_skill_installer.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/interpreter_data/rasa_nlu_service_skill/skill.yaml" in BOOTSTRAP_CRITICAL_PATHS


def test_skill_facing_sdk_surfaces_are_bootstrap_promoted() -> None:
    assert "src/adaos/ports/skill_context.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/core/_ctx.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/core/decorators.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/core/errors.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/data/context.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/data/device_access.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/__init__.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/context.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/endpoint_audio.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/io/media.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/sdk/redevice.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/endpoint_audio.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/endpoint_router.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/skill/activation.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/skill/context.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/skill/runtime_env.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/status/hot_events.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/workspace_registry.py" in BOOTSTRAP_CRITICAL_PATHS
