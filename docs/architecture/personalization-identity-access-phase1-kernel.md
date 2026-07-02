# Personalization Phase 1 Access Kernel

Status: implemented backend kernel for Phase 1.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/domain/personalization_access.py`
- `src/adaos/services/personalization_access.py`
- `tests/test_personalization_access_kernel.py`

## Purpose

Phase 1 turns the Phase 0 contracts into a local backend decision layer. It is
intentionally below UI, SDK convenience helpers, and join-browser flows:
user-facing surfaces can be added later, but they must call this kind of shared
service instead of making access decisions from UI state or skill-local state.

The implementation is a small JSON-backed service kernel. The storage backend
is replaceable; the behavior it pins down is the important part.

## Stored Facts

`PersonalizationAccessStore` persists contract dictionaries for:

- users;
- profiles;
- user keys;
- device keys;
- sessions;
- memberships;
- grants;
- invites;
- recovery actions;
- revocations;
- append-only audit records.

Records keep the Phase 0 `schema_version`, so later migrations can replace the
storage backend without changing subject/session/grant semantics.

## Policy Kernel

`PersonalizationAccessService.evaluate(...)` implements the Phase 1 shape:

```text
is_allowed(actor, action, subject, scope, resource, context)
```

It returns `PolicyDecision` with:

- `decision`: `allow` or `deny`;
- `reason_code`;
- matched `grant_ids` where applicable;
- explicit actor, subject, scope, and resource.

The current rules are deliberately minimal:

- the configured owner subject has implicit subnet-admin authority;
- role presets expand to the Phase 0 capability bundles;
- explicit grants and memberships are ignored unless active, unexpired, and
  scope-compatible;
- actions can require an `approval_id` through grant constraints;
- session actors are resolved to their attached user only when the session is
  active and unexpired;
- revoked devices block their attached sessions.

This is not yet a full API authorization middleware. Phase 2+ should route SDK,
API, and projection access through the kernel.

## Revocation Rules

Phase 1 pins down the first propagation behavior:

- revoked grants stop matching immediately;
- revoked sessions deny session-actor decisions;
- revoked devices revoke active sessions attached to the same device id;
- user-key, device, session, and grant revocations write durable revocation
  facts and audit records.

Later phases can add live websocket/Yjs disconnect propagation and encrypted
secret isolation, but the durable authority state now has one place to live.

## Invite and Recovery Guards

The service rejects stale privileged material before UI flows exist:

- accepted, expired, or revoked invites cannot be claimed again;
- expired invites are marked `expired`;
- accepted or revoked recovery actions cannot be completed again.

Targeted invite, public guest join, device pairing, and recovery UX still belong
to later phases. Phase 1 only establishes the replay/stale-write guards those
flows must use.

## Audit Surface

Every service mutation and policy decision writes an append-only `AuditRecord`.
Audit query helpers support filtering by:

- actor;
- subject;
- scope;
- device;
- session;
- source;
- event type;
- decision;
- time range.

Generic audit records store metadata and redacted diffs, not private content.

## Current Boundaries

Implemented now:

- local JSON-backed store and service;
- owner implicit admin;
- role-preset capability expansion;
- grant and membership evaluation;
- session/device-aware evaluation;
- structured allow/deny decisions;
- audit append and query helpers;
- invite/recovery replay guards;
- local-owner baseline regression.

Not implemented in Phase 1:

- API middleware integration;
- client settings/profile UI;
- guest join and targeted invite browser flows;
- device pairing UX;
- recovery without owner/co-owner;
- root-server/global identity trust;
- encrypted private data or secret isolation.

## Required Local Verification

Phase 1 is locally verified by:

- `python -m pytest tests/test_personalization_access_contracts.py tests/test_personalization_access_kernel.py`
- `python -m pytest tests/test_conversation_contracts.py tests/test_event_envelope.py tests/test_infrascope_event_sources.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
