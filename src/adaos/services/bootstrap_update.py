from __future__ import annotations

# Files in these lists affect root-launched bootstrap, supervisor, and sidecar
# behavior and therefore require root promotion only after the prepared slot has
# already been validated.
SUPERVISOR_BOOTSTRAP_PATHS: tuple[str, ...] = (
    "src/adaos/build_info.py",
    "src/adaos/apps/supervisor.py",
    "src/adaos/apps/api/auth.py",
    "src/adaos/apps/bootstrap.py",
    "src/adaos/apps/cli/commands/api.py",
    "src/adaos/services/agent_context.py",
    "src/adaos/services/core_slots.py",
    "src/adaos/services/core_update.py",
    "src/adaos/services/core_update_policy.py",
    "src/adaos/services/node_config.py",
    "src/adaos/services/realtime_sidecar.py",
    "src/adaos/services/root/memory_profile_sync.py",
    "src/adaos/services/runtime_environment.py",
    "src/adaos/services/runtime_paths.py",
    "src/adaos/services/supervisor_memory.py",
)

SIDECAR_CONTROLLED_PATHS: tuple[str, ...] = (
    "src/adaos/services/realtime_sidecar.py",
    "src/adaos/services/nats_config.py",
    "src/adaos/services/nats_errors.py",
    "src/adaos/services/nats_ws_transport.py",
    "src/adaos/services/node_runtime_state.py",
    "src/adaos/services/runtime_dotenv.py",
    "src/adaos/services/runtime_paths.py",
)

UPDATE_CONTROL_PATHS: tuple[str, ...] = (
    "src/adaos/apps/autostart_runner.py",
    "src/adaos/apps/core_update_apply.py",
    "src/adaos/apps/cli/commands/node.py",
    "src/adaos/services/autostart.py",
    "src/adaos/services/bootstrap_update.py",
    "src/adaos/services/node_display.py",
    "src/adaos/services/runtime_refresh.py",
    "src/adaos/services/scenario/webspace_runtime.py",
    "src/adaos/services/subnet/link_client.py",
    "src/adaos/services/subnet/link_manager.py",
    "src/adaos/apps/cli/commands/setup.py",
    "src/adaos/apps/cli/commands/skill.py",
)

RUNTIME_SKILL_CONTROL_PATHS: tuple[str, ...] = (
    "src/adaos/domain/__init__.py",
    "src/adaos/domain/event_envelope.py",
    "src/adaos/domain/projection_keys.py",
    "src/adaos/domain/projection_record.py",
    "src/adaos/domain/projection_subscription.py",
    "src/adaos/interpreter_data/rasa_nlu_service_skill/skill.yaml",
    "src/adaos/services/eventbus.py",
    "src/adaos/services/nlu/rasa_skill_installer.py",
    "src/adaos/sdk/core/decorators.py",
    "src/adaos/sdk/data/bus.py",
    "src/adaos/services/skills_loader_importlib.py",
    "src/adaos/services/skill/manager.py",
    "src/adaos/services/models/__init__.py",
    "src/adaos/services/models/artifacts.py",
)

BOOTSTRAP_CRITICAL_PATHS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *SUPERVISOR_BOOTSTRAP_PATHS,
            *SIDECAR_CONTROLLED_PATHS,
            *UPDATE_CONTROL_PATHS,
            *RUNTIME_SKILL_CONTROL_PATHS,
        )
    )
)
