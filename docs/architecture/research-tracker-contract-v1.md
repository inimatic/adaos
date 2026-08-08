# Research Tracker Contract 1.0

Status: frozen provider contract, validated locally by the local reference
provider and the MLflow provider.

Last reviewed: 2026-08-08.

This contract makes experiment telemetry portable without making a tracker the
research authority. AdaOS owns scientific identity, the durable journal,
workflow gates, normalized evidence, and claim decisions. A tracker provider
owns projection, query indexes, and its optional UI.

The machine-readable contract is shipped by `research_manager_skill` as
`schemas/tracker.contract.v1.schema.json`.

## Port

A `1.0` provider implements:

- `health` and a version/capability descriptor;
- idempotent `open_session` for one physical execution attempt;
- batched `append_observations` and `append_artifacts`;
- `flush`, with an optional required-delivery barrier;
- terminal `close_session` with completeness metadata;
- `get_session`, filtered `query_sessions`, and `metric_history`;
- deterministic session and experiment exports;
- durable provider links between AdaOS identities and provider-native ids.

One logical AdaOS `Run` may have several attempts. Every `ExecutionAttempt`
gets a separate tracker session and, for MLflow, a separate MLflow Run. A retry
therefore cannot silently become a new scientific sample.

## Identity mapping

The following keys are mandatory on sessions created by the research manager.
Provider-native values are projections, never replacement identities.

| AdaOS identity | Tracker / MLflow representation |
| --- | --- |
| Study | session field `study_id`, tag `adaos.study_id`, MLflow experiment namespace |
| Experiment | session field `experiment_id`, tag `adaos.experiment_id` |
| Experiment revision | `experiment_revision_id`, tag `adaos.experiment_revision_id` |
| Trial group | tag `adaos.trial_group_id` |
| Trial | session field `trial_id`, tag `adaos.trial_id` |
| Logical run | session field `run_id`, tag `adaos.run_id` |
| Physical attempt | session field `attempt_id`, tag `adaos.attempt_id`, one MLflow Run |
| Locked protocol | tag `adaos.protocol_digest` |
| Locked analysis plan | tag `adaos.analysis_plan_digest` |
| Source/package | tag `adaos.source.code_digest` |
| Environment | tag `adaos.environment_digest` |
| Dataset/input identity | tag `adaos.data_digest` and a typed dataset input |
| Distributed trace | tag `adaos.trace_id` |
| Evidence class | tag `adaos.evidence_class` |
| Contract version | tag `adaos.contract_version` |

An MLflow provider link records the AdaOS session id, binding id, MLflow
experiment id, MLflow run id, projection authority, and lifecycle state. The
link is query metadata; the AdaOS session remains authoritative.

## Observation identity

An observation normalizes metric namespace/name, scalar or structured value
type, unit, direction, split role, dataset digest, step axis/value,
aggregation, observation time, producer attempt/sequence, and evidence role.
The default event identity is derived from the session, metric, split,
dataset, step, aggregation, and producer sequence.

Replaying an identical event is a duplicate success. Reusing the same event id
with different content is a conflict. A batch is prevalidated and committed
atomically, so a bad or over-capacity batch cannot be partially accepted.

## Delivery and outage semantics

The local journal is the transactional outbox. MLflow delivery is at least
once, while deterministic event identities and provider-run lookup make replay
idempotent.

- The admitted pending-event capacity is bounded; exceeding it raises explicit
  backpressure before insertion.
- Provider failure retains events as pending/failed and makes health degraded
  or unavailable.
- `flush` retries bounded batches after restart.
- A required flush or terminal status update that remains incomplete raises a
  delivery error. The session stays non-terminal.
- No provider outage may be converted into a successful confirmatory result.

## Evidence acceptance and deletion

Finalization exports normalized sessions, observations, artifact references,
completeness, delivery receipts, and provider links. AdaOS verifies the export
digest and writes the complete export plus a separate acceptance record into
immutable research storage. Result verification reads that accepted export and
does not depend on a live MLflow server.

Provider-native deletion is admissible only after such acceptance. Deletion
updates the provider link but cannot mutate the frozen export. MLflow backend
tables are never queried or migrated by AdaOS.

## Binding and UI boundaries

Local MLflow is a normal supervised service skill. Core injects its relational
and blob locations into the service process only; skill-facing bindings remain
opaque and owner-scoped. A provisioned PostgreSQL DSN uses a generated
least-privilege login for the skill-owned database. A provisioned object URI is
similarly isolated by owner/logical-name binding; credentials come from the
operator's workload/secret mechanism, not a scenario parameter.

An external MLflow endpoint requires an AdaOS `ServiceBinding`, TLS outside
loopback, resolved secret headers, and successful health/version probes.

The optional UI is exposed through the authenticated same-origin AdaOS service
gateway. The generic `visual.serviceFrame` widget accepts a service id rather
than an arbitrary URL and can only navigate to that service's governed
bootstrap route. The server applies lifecycle checks, request limits, origin
policy, CSP, and `SAMEORIGIN` framing policy.

## Conformance evidence

The conformance suite covers schema validation, ordering, duplicate steps,
outage and restart replay, bounded backpressure, required terminal delivery,
large artifact references, finalization, deterministic export, provider links,
authenticated external binding, version probing, and deletion after evidence
acceptance. Browser tests separately exercise the governed iframe component;
API tests exercise authentication, cookie bootstrap, cross-site rejection,
CSP, framing headers, upstream lifecycle failure, root-path preservation, and
redirect-body sanitization. A live Chrome check rendered the MLflow React UI
and loaded its relative assets/query API through the AdaOS gateway without
exposing the upstream loopback URL. E002's export has an immutable acceptance
record and its result verifier reports `tracker_verification_source` as
`accepted-export` while validating eight artifact digests. Historical sessions
retain their original pre-freeze contract tag rather than being rewritten.
