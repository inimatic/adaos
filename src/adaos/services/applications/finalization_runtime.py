"""Continue confirmed Finalize intents in the existing Core lifecycle journal."""
from datetime import datetime, timezone
import logging

from adaos.services.mutation_lock import MutationLockTimeout
from adaos.domain.artifact_release import canonical_payload_digest
from .lifecycle_operations import ApplicationLifecycleOperations, PLAN_SCHEMA

_LOG = logging.getLogger(__name__)


def retryable_finalization_error(error: Exception) -> bool:
    """Retry transport/storage interruptions, never a rejected business decision."""
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    # HTTP adapters retain their own exception types. Authentication, policy and
    # malformed-response errors are deliberately outside this transport set.
    return type(error).__module__.split('.')[0] in {'requests', 'httpx'} and type(error).__name__ in {
        'ConnectError', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout',
        'ConnectionError', 'Timeout', 'TimeoutException'}


def reconcile_finalizations(owner, *, limit: int = 2, now: datetime | None = None) -> list[dict]:
    operations = ApplicationLifecycleOperations(owner)
    observed = now or datetime.now(timezone.utc)
    results = []
    for op in operations.store.list_operations():
        confirmation = op.result.get('confirmation') or {}
        execution = op.result.get('execution') or {}
        if (op.plan.get('schema') != PLAN_SCHEMA or op.plan.get('action') != 'promote_stable'
                or canonical_payload_digest(op.plan) != op.plan_digest
                or op.status not in {'applying', 'reconciling', 'unknown'}
                or confirmation != {'confirmed': True, 'plan_digest': op.plan_digest,
                    'actor_ref': op.actor_ref, 'subnet_ref': op.subnet_ref}):
            continue
        if op.status == 'unknown' and (execution.get('status') != 'retrying'
                or datetime.fromisoformat(execution['retry_at']) > observed):
            continue
        try:
            result = operations.apply(op.operation_id, plan_digest=op.plan_digest,
                actor_ref=op.actor_ref, subnet_ref=op.subnet_ref,
                capability='applications.publish', recover=True)
            results.append({'operation_id': op.operation_id, 'status': result['status']})
        except MutationLockTimeout:
            continue  # The foreground or another Core slot still owns this intent.
        except Exception:
            _LOG.warning('Confirmed Finalize could not complete operation=%s', op.operation_id, exc_info=True)
            results.append({'operation_id': op.operation_id, 'status': 'pending'})
        if len(results) >= max(1, min(limit, 10)):
            break
    return results
