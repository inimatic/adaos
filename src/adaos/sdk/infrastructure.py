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


def distribution_snapshot() -> dict:
    """Read publication history, regional demand/errors and the whole build plan."""
    from adaos.services.infrastructure_distribution import distribution_snapshot as read
    ctx = require_ctx('sdk.infrastructure.distribution_snapshot')
    require_skill_capability(ctx, 'external_provider.use')
    return read(ctx)


def publish_distribution(*, expected_plan_sha256: str) -> dict:
    """Queue the exact complete plan through the admitted infrastructure controller."""
    from adaos.services.infrastructure_distribution import publish_distribution as publish
    ctx = require_ctx('sdk.infrastructure.publish_distribution')
    require_skill_capability(ctx, 'external_provider.use')
    return publish(ctx, expected_plan_sha256)
