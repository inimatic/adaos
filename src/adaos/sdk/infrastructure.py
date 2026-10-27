"""Read-only infrastructure observations; host credentials never cross the SDK."""
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.infrastructure_observation import read_inventory


def snapshot() -> dict:
    ctx = require_ctx("sdk.infrastructure.snapshot")
    require_skill_capability(ctx, "external_provider.use")
    return read_inventory(ctx)


def owner_is_current() -> bool:
    """Whether this installed handler still owns its selected runtime slot."""
    from adaos.services.infrastructure_observation import observer_owner_is_current
    ctx = require_ctx("sdk.infrastructure.owner_is_current")
    require_skill_capability(ctx, "external_provider.use")
    return observer_owner_is_current(ctx)
