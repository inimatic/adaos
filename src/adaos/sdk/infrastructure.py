"""Read-only infrastructure observations; host credentials never cross the SDK."""
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.infrastructure_observation import read_inventory


def snapshot() -> dict:
    ctx = require_ctx("sdk.infrastructure.snapshot")
    require_skill_capability(ctx, "external_provider.use")
    return read_inventory(ctx)
