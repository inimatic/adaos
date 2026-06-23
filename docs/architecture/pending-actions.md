# Pending Actions

Status: target architecture with an initial core service, SDK helper slice,
browser global surface, and NLU Teacher candidate-confirmation migration.

Pending Actions are the AdaOS core mechanism for asking a human to choose an
action later, possibly through a different channel than the one that created the
request.

The mechanism is intentionally not a notification system. Notifications inform.
Pending Actions wait for a bounded, auditable response that may drive a runtime
mutation, retry, delegated workflow, or rejection.

## Purpose

AdaOS needs one global process for deferred human reactions across skills,
scenarios, NLU Teacher, device pairing, Builder review, runtime operations, and
other governed workflows.

The core owns durability, idempotency, expiration, channel normalization, and
response routing. Skills and scenarios use the SDK to publish requests and to
handle responses.

## Implementation Status

Current implemented slice:

- [x] Core service stores the registry under `data.pending_actions`.
- [x] Pending Action records are node-aware for producer identity and explicit
  response targets.
- [x] SDK helpers expose publish/respond/list/expire entry points.
- [x] Event command subscriptions support bus-based publish/respond/expire.
- [x] Responses are recorded idempotently and routed through
  `response_route.topic`.
- [x] Browser FAB/global overlay UI shows active actions from
  `data.pending_actions`, previews the first three entries, expands to the full
  active list, and sends `pending_actions.respond.request`.
- [x] NLU Teacher candidate confirmations publish Pending Actions and accept
  responses from the Pending Actions registry, browser UI, and voice yes/no.
- [ ] NLU Teacher clarification sessions still use the legacy chat-local flow
  and should be migrated separately if they need cross-channel response.
- [ ] Builder, pairing, runtime recovery, capability elevation, and guarded
  skill-action producer migrations.
- [ ] Full Pending Actions workbench/modal with filtering, history, and direct
  links to source evidence.
- [ ] Notification deep links to Pending Actions without making notifications
  the source of truth.
- [ ] Delegated response-handler subscription handshake.

## Identity

Skill identity is node-aware. A skill is unique only in combination with the
node that hosts it.

Every Pending Action must carry stable actor identity:

```json
{
  "producer": {
    "type": "skill",
    "node_id": "node-123",
    "skill_id": "nlu_teacher",
    "instance_id": "nlu_teacher@node-123"
  },
  "owner_scope": {
    "webspace_id": "default",
    "node_id": "node-123"
  }
}
```

The UI may aggregate by webspace, but apply/reject/test callbacks must route by
the explicit target actor, not by `skill_id` alone.

## Data Shape

Target projection:

```json
{
  "id": "pa.123",
  "kind": "nlu.teacher.candidate_confirmation",
  "status": "pending",
  "created_at": 1730000000.0,
  "expires_at": 1730000900.0,
  "title": "Confirm command understanding",
  "title_i18n": {
    "key": "pending_actions.nlu.confirm_title",
    "params": {}
  },
  "summary": "Open Face Vision?",
  "summary_i18n": {
    "key": "pending_actions.nlu.open_tool_summary",
    "params": {"tool": "Face Vision"}
  },
  "request_text": "show the camera",
  "request_locale": "en",
  "producer": {"type": "skill", "node_id": "node-123", "skill_id": "nlu_teacher"},
  "domain_ref": {
    "webspace_id": "default",
    "candidate_id": "cand.123",
    "request_id": "req.123"
  },
  "allowed_actions": [
    {
      "id": "test",
      "label": "Test",
      "label_i18n": {"key": "pending_actions.action.test"}
    },
    {
      "id": "approve",
      "label": "Approve",
      "label_i18n": {"key": "pending_actions.action.approve"}
    },
    {
      "id": "refuse",
      "label": "Refuse",
      "label_i18n": {"key": "pending_actions.action.refuse"}
    },
    {
      "id": "postpone",
      "label": "Later",
      "label_i18n": {"key": "pending_actions.action.postpone"}
    }
  ],
  "default_text_binding": true,
  "response_route": {
    "type": "event",
    "topic": "nlp.teacher.candidate.confirmation.response",
    "target": {"node_id": "node-123", "skill_id": "nlu_teacher"}
  }
}
```

Rules:

- `id`, `kind`, `status`, `created_at`, `producer`, `allowed_actions`, and
  `response_route` are required.
- `expires_at` is optional. Missing or `null` means no automatic expiration.
  Publishers must not use `0`, `""`, or `"0"` to mean "no TTL"; those values
  are invalid.
- `ttl_s` may be accepted by SDK helpers as input, but core storage should
  persist the resolved `expires_at`.
- `allowed_actions[].id` is stable logic. Labels are presentation only.
- `allowed_actions[].terminal` controls whether a response closes the Pending
  Action. Default SDK presets keep `test`, `preview`, and `postpone` active,
  while `approve` and `refuse` are terminal.
- `*_i18n.key` should be preferred for system strings. Plain text fields remain
  required as fallback for legacy clients, logs, and incomplete dictionaries.
- `request_locale` is evidence for label choice, not an ownership key.
- Payloads must not contain secrets. They may contain references to durable
  state that the response handler can reread under its own capability policy.

## Localization

There is already a core i18n service and a client i18n service. Pending Actions
should build on that foundation instead of adding a separate localization
mechanism.

Core producers should publish:

- a stable i18n key for every system-controlled title, summary, action label,
  and short outcome string
- fallback text for every key-bearing field
- params that are safe to show to a human
- `request_locale` and `preferred_locales` when known

Human-authored names, device labels, skill display names, and user-confirmed
aliases are values, not translation keys. They may appear in params but must not
be translated or used as storage identifiers.

## Notifications

Notifications and Pending Actions share some presentation surfaces but not
semantics.

Use notifications for:

- completed operation summaries
- warnings that require no choice
- transient status and degraded-mode messages
- audit-friendly toasts and notification history

Use Pending Actions for:

- a choice that affects future behavior
- approval before mutation
- a retry/refuse decision that becomes evidence
- a postponed action that should remain addressable
- cross-channel human response handling

A Pending Action may emit a notification when it is created, completed,
expired, or fails, but the notification is only a pointer to the durable action.
The notification must not be the source of truth for the requested decision.

## System Producers

Initial system producers should include:

- NLU Teacher candidate confirmation: approve/refuse/test/postpone a proposed
  causal link between utterance and action.
- Builder review: approve, reject, or redirect a preview before apply.
- Pairing/admission: approve device, browser, Telegram, or endpoint join flows.
- Runtime operation recovery: continue, retry, rollback, or abandon a stalled
  install/update/activation operation.
- Security and capability elevation: approve a scoped temporary capability.
- Destructive or external-IO skill actions: require explicit human approval
  before dispatch.
- Ambiguous routing or ownership: choose the target node, skill, scenario, or
  endpoint when automatic routing is not safe.
- Quarantine/guard recovery: acknowledge, disable, retry with reduced scope, or
  open a repair task.

These are not notifications because each one can change control flow or durable
state.

Implemented producer slice:

- NLU Teacher voice regex/capability candidate confirmations now create
  `kind=nlu.teacher.candidate_confirmation` records.
- Voice `yes/no` first records a Pending Action response and then applies the
  same confirmation handler synchronously for the current voice request. The
  routed Pending Action event is marked as already handled by the voice path to
  avoid double application.
- Browser responses go through `pending_actions.respond.request`; the core
  records the response and routes it to
  `nlp.teacher.candidate.confirmation.response`.

## Response Routing

The base model is explicit routing:

```json
{
  "response_route": {
    "type": "event",
    "topic": "some.skill.pending_action.response",
    "target": {"node_id": "node-123", "skill_id": "some_skill"}
  }
}
```

The core validates and records the response, then emits to the route. This makes
cross-skill handling possible without hidden subscriptions.

The initial core service publishes command subscriptions for bus callers:

- `pending_actions.publish.request`
- `pending_actions.respond.request`
- `pending_actions.expire.request`

State-change events use separate names such as `pending_actions.created`,
`pending_actions.responded`, `pending_actions.expired`, and
`pending_actions.changed`.

One skill may publish an action whose response is handled by another skill, but
the route must be explicit and policy-checked.

Deferred task: support a delegation handshake where one skill proposes that
another skill subscribe to a human response:

```text
skill A -> pending_actions.delegate.request -> skill B
skill B -> pending_actions.delegate.accept
core publishes action with B as response handler
```

This is intentionally later work. The first implementation should use explicit
`response_route`.

## SDK Contract

The implemented SDK surface exposes import-light helpers:

```python
from adaos.sdk.data import publish_pending_action

action = publish_pending_action(
    kind="nlu.teacher.candidate_confirmation",
    title_i18n={"key": "pending_actions.nlu.confirm_title"},
    title="Confirm command understanding",
    summary_i18n={
        "key": "pending_actions.nlu.open_tool_summary",
        "params": {"tool": "Face Vision"},
    },
    summary="Open Face Vision?",
    actions=["test", "approve", "refuse", "postpone"],
    ttl_s=900,
    default_text_binding=True,
    response_topic="nlp.teacher.candidate.confirmation.response",
    payload_ref={"candidate_id": "cand.123"},
)
```

Available helpers:

- `publish_pending_action(...)`
- `respond_pending_action(action_id, response_action_id, ...)`
- `list_pending_actions(...)`
- `expire_pending_actions(...)`

SDK helpers may provide common action sets, but core storage should always
contain explicit `allowed_actions`.

Guidance for generated skills should focus on implementation-sensitive
ambiguities:

- omit `ttl_s` or pass `None` for no automatic expiration; do not pass `0` or
  empty string
- use stable action ids and localized/fallback labels
- treat `approve` as a response, not proof that the action already succeeded
- make response handlers idempotent; a repeated response should be detected by
  the core and safe in the handler
- publish references, not large mutable snapshots
- use `test`/`preview` actions for dry runs when mutation risk exists
- record refusal and expiration as evidence when they affect future NLU,
  Builder, or routing behavior

## UI Projection

The browser should provide a global Pending Actions affordance, independent of
the current modal or scenario.

Implemented initial behavior:

- show a floating action button while owner chrome is visible
- show the count of active pending actions
- preview up to three latest/highest-priority actions in a compact overlay
- expand the overlay to the full active queue and send responses through the
  event command plane
- resolve `title_i18n`, `summary_i18n`, and `label_i18n` through the client i18n
  service with fallback text

Open UI work:

- open a dedicated modal/workbench for filtering, history, source evidence, and
  long queues
- keep notifications separate but allow notification entries to deep-link to a
  pending action

## Verification

Automated checks used for the current slice:

- `py -3.11 -m pytest tests/test_pending_actions.py tests/test_nlu_teacher_confirmation_runtime.py`
- `npx ng test --watch=false --browsers=ChromeHeadless --include=src/app/app.component.spec.ts`

Manual checks:

- Trigger an NLU Teacher candidate from voice, verify the bottom-right Pending
  Actions affordance appears, and approve/refuse from the browser.
- Trigger the same flow and answer by voice `да/нет`; verify the Pending Action
  record moves to responded while the NLU confirmation is accepted/rejected.
- Open a modal or switch scenario while an action is pending; the global
  affordance should remain reachable.

