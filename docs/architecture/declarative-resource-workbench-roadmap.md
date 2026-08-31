# Declarative Resource Workbench Roadmap

Status: proposed domain roadmap.

Last reviewed: 2026-08-31.

This roadmap sequences the work needed to make the
[Declarative Resource Workbench](declarative-resource-workbench.md) a reusable
AdaOS control surface for people, Codex, Builder, skills, scenarios, and
channel adapters.

## Priority Model

- `[must]`: required for the first coherent vertical slice.
- `[should]`: required before broad repeated use or unattended Builder repair.
- `[could]`: useful optional ergonomics, diagnostics, or demo polish.
- `[deferred]`: intentionally postponed until a later governed-evolution,
  collaboration, or external-integration milestone.

Maturity follows the Governed Evolution Roadmap vocabulary:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

## DRW0. Contract Spine

Goal: resource definitions, operations, queries, events, and traces have one
shared vocabulary before domain migrations begin.

Exit proof: Dev Tickets and one synthetic Demo Metrics resource can both be
described by `adaos.resource.definition.v1`, validated, listed, inspected, and
rendered without custom per-resource browser code.

- [ ] `[must]` `DRW0-01` Specify `adaos.resource.definition.v1` with resource
  type, version, authority, scope, record schema ref, query capabilities,
  operations, views, events, workflow links, privacy, and evidence policy.
- [ ] `[must]` `DRW0-02` Specify `adaos.resource.query.v1` with filters, relation
  filters, text search, relevance context, sorting, cursor pagination, and
  heavy-field `include` hints.
- [ ] `[must]` `DRW0-03` Specify `adaos.resource.operation.v1` with input/output
  schema refs, operation kind, risk, authority, idempotency key, optimistic
  revision, evidence requirements, and expected events.
- [ ] `[must]` `DRW0-04` Specify `adaos.resource.event.v1` and
  `adaos.resource.trace.v1` as generic envelopes with domain `semantic_type`.
- [ ] `[must]` `DRW0-05` Add contract validation for definitions, queries,
  operations, and views to fail closed on unsupported or misspelled fields.
- [ ] `[should]` `DRW0-06` Add a small SDK facade for registering definitions,
  invoking resource operations, appending traces, and opening artifact refs.
- [ ] `[should]` `DRW0-07` Define the relational-plus-JSON storage contract for
  record indexes, operation ledger, events, relations, artifacts, and traces.
- [ ] `[could]` `DRW0-08` Add a compatibility generator that drafts resource
  definitions from existing skill tool manifests for human review.
- [ ] `[must]` `DRW0-09` Add i18n fields to resource definitions for resource,
  field, operation, status, validation, empty, loading, stale, degraded, and
  permission-denied text.
- [ ] `[must]` `DRW0-10` Add locale resolution and fallback rules that compose
  request, conversation/channel, user profile, browser, workspace, resource,
  and English/default fallback.
- [ ] `[must]` `DRW0-11` Add access declarations for resource read, row scope,
  field visibility, artifact visibility, operation capabilities, risk,
  approvals, and evidence gates.
- [ ] `[must]` `DRW0-12` Add actor/subject/delegation fields to operation,
  event, and trace envelopes.
- [ ] `[must]` `DRW0-13` Add privacy, sensitivity, retention, and external
  export policy fields for records, artifacts, traces, and conversation
  evidence.
- [ ] `[should]` `DRW0-14` Add accessibility and user-preference view hints for
  focus, keyboard behavior, announcements, density, high contrast, reduced
  motion, locale formatting, and compact layout.
- [ ] `[should]` `DRW0-15` Add a common readiness vocabulary for ready, stale,
  read-only, offline, permission denied, provider unavailable, unsupported
  query, validation error, conflict, rate limited, and degraded states.
- [ ] `[should]` `DRW0-16` Add named-entity integration for localized labels,
  aliases, canonical refs, and locale-aware search without creating a second
  identity namespace.

## DRW1. Dev Tickets As The First Real Resource

Goal: Dev Tickets become the first production-grade resource family over
Development Signals.

Exit proof: a person and Codex can query, inspect, claim, comment, resolve,
verify, close, and reopen the same ticket through CLI/API/UI/Builder context,
with evidence and trace visible.

- [x] `[must]` `DRW1-01` Publish a resource definition for
  `adaos.dev.ticket` that wraps the existing ticket API and CLI lifecycle.
- [x] `[must]` `DRW1-02` Normalize open-status grouping across
  `captured`, `proposed`, `accepted`, `deferred`, `waiting_for_user`,
  `ready_for_builder`, `in_builder`, `claimed`, `in_progress`, `resolved`, and
  tickets returned to active work by `reopen`.
- [x] `[must]` `DRW1-03` Add lifecycle operations `claim`, `comment`,
  `resolve`, `verify`, `close`, `reopen`, `duplicate`, and `related`.
- [x] `[must]` `DRW1-04` Keep `resolve` evidence-gated but non-terminal;
  require verification evidence before `verified`, and only then permit normal
  closure.
- [ ] `[must]` `DRW1-05` Add artifact commands such as `artifact list`,
  `artifact open`, and evidence preview through the same resource operation
  model.
- [x] `[must]` `DRW1-06` Add scenario, skill, modal, component, severity,
  blocking, source, owner, and updated-time filters.
- [x] `[must]` `DRW1-11` Extend `adaos.dev_ticket.v1` for the target lifecycle:
  `claimed`, `in_progress`, `verified`, non-terminal `resolved`, and `reopen`
  lifecycle events or operation history.
- [x] `[must]` `DRW1-12` Add field and artifact access policy for ticket
  summary, original input, screenshots, logs, traces, local paths, Builder refs,
  NLU refs, and external issue exports.
- [ ] `[must]` `DRW1-13` Preserve original ticket/report locale and expose
  localized status/action labels as derived views.
- [ ] `[must]` `DRW1-14` Add access-decision trace to ticket operations,
  including actor, subject, delegated context, required capability, policy
  digest, and denial reason.
- [ ] `[should]` `DRW1-07` Add relevance ranking by active Builder project,
  current scenario, current modal, selected files, commit diff, and recent
  runtime evidence.
- [ ] `[should]` `DRW1-08` Add initial snapshot plus change subscription so
  Codex and Builder do not rely on manual polling.
- [ ] `[should]` `DRW1-09` Add token/cost estimate fields for autonomous repair
  and make estimates visible before launch.
- [ ] `[could]` `DRW1-10` Prepare redacted external issue drafts, but keep
  GitHub Issues optional and non-authoritative.

Implementation note, 2026-08-31: `adaos.dev.ticket` now has a first resource
definition over the Development Ticket service. It declares governed lifecycle
operations, owner/component and scope filters, stage grouping, evidence and
artifact views, i18n fields, access policy, Builder handoff operations, core
capability requests, and SDK-understanding signals. The production gap is no
longer the core lifecycle contract; it is uniform agent ergonomics:
artifact-open commands, subscriptions, relevance ranking, SDK/MCP helper
surface, access-decision traces on every operation, and a Resource Workbench
renderer that can replace the custom client panel.

## DRW2. Demo Metrics Harness

Goal: Demo Metrics demonstrates the workbench renderer, synthetic resources,
events, and traces without touching production skills.

Exit proof: the Demo Metrics scenario shows a resource list/detail/form/action
surface, synthetic CRUD, validation errors, event log, and trace inspector.

- [x] `[must]` `DRW2-01` Add `demo.metric` resource definition over the existing
  Demo Metrics snapshot data.
- [x] `[must]` `DRW2-02` Add `demo.metric_note` with synthetic create, update,
  delete, validation error, and optimistic revision conflict cases.
- [x] `[must]` `DRW2-03` Add `demo.metric_event` stream projection over
  `demo_metrics.events`.
- [x] `[must]` `DRW2-04` Add a workbench demo surface that renders from resource
  definitions rather than a custom hand-written grid.
- [x] `[must]` `DRW2-05` Add fixture states for empty, normal, validation
  failure, unavailable provider, long text, and RU/EN labels.
- [x] `[must]` `DRW2-09` Add owner/admin/member/guest role fixtures that prove
  hidden, disabled, allowed, and denied operation states.
- [x] `[must]` `DRW2-10` Add i18n fixture coverage for fields, operations,
  statuses, validation messages, empty states, and permission denials.
- [ ] `[should]` `DRW2-06` Add visual regression and browser E2E tests for the
  demo workbench surface.
- [x] `[should]` `DRW2-07` Show query, operation, event, and render traces in a
  read-only inspector.
- [ ] `[should]` `DRW2-11` Add accessibility fixture checks for keyboard
  navigation, focus return, announcement text, compact layout, long labels, and
  high-contrast rendering.
- [ ] `[could]` `DRW2-08` Add a compact/mobile renderer profile after desktop
  behavior is stable.

Implementation note, 2026-08-31: Demo Metrics now exposes a Resource Workbench
modal with resource definitions, live `demo.metric_note` create/update/delete,
validation and revision-conflict behavior, role fixtures, RU/EN labels,
readiness fixtures, role matrix, and trace tables. The visible form exposes
Create, Update selected, and Delete selected so CRUD semantics can be tested
without custom code paths. Browser E2E, visual regression, mobile profile, and
accessibility fixture checks remain open.

## DRW3. Builder Observability

Goal: Builder can inspect resource behavior directly instead of relying only
on end-to-end tests, screenshots, or manual log analysis.

Exit proof: a Builder session opened for a Dev Ticket can show the relevant
resource definition, query trace, operation trace, evidence refs, event
delivery, provider diagnostics, and source availability choices.

- [ ] `[must]` `DRW3-01` Add a read-only Resource Inspector tab or panel in
  Builder.
- [ ] `[must]` `DRW3-02` Show definition id, version, digest, provider,
  capabilities, schema refs, and source availability.
- [ ] `[must]` `DRW3-03` Show query traces with filters, cursor, result count,
  latency, unsupported filters, and provider fallback.
- [ ] `[must]` `DRW3-04` Show operation traces with actor, idempotency key,
  payload summary, validation result, revision, risk, and result event.
- [ ] `[must]` `DRW3-05` Show event delivery and projection state for emitted
  and subscribed resource events.
- [ ] `[must]` `DRW3-06` Link artifact/evidence refs to preview/open commands.
- [ ] `[must]` `DRW3-10` Show localization traces: requested locale, resolved
  locale, fallback chain, missing message keys, and named-entity matches.
- [ ] `[must]` `DRW3-11` Show access traces: subject, actor, delegated context,
  role preset, required capabilities, policy digest, decision, and denial
  reason.
- [ ] `[must]` `DRW3-12` Show readiness/degraded-mode traces for stale data,
  read-only operation mode, provider failure, unsupported filters, queued work,
  and missing authority.
- [ ] `[should]` `DRW3-07` Add relevance queries for "tickets/resources related
  to this project, scenario, skill, modal, open file set, and current diff".
- [ ] `[should]` `DRW3-08` Add autonomous-repair cost estimates and risk labels
  to Builder handoff views.
- [ ] `[could]` `DRW3-09` Add trace comparison between prototype behavior and
  implemented behavior.

## DRW4. Notebook Migration

Goal: prove that a simple existing skill can move from custom CRUD wiring to a
resource declaration without rewriting its domain handlers first.

Exit proof: Notebook list/detail/edit/attach/delete works through resource
views and operations backed by the current notebook skill tools.

- [ ] `[must]` `DRW4-01` Declare `notebook.note` over existing snapshot,
  create, save, and delete tools.
- [ ] `[must]` `DRW4-02` Declare `notebook.note_attachment` over existing attach
  and open/list behavior.
- [ ] `[must]` `DRW4-03` Keep selected note as `notebook.session` or view state,
  not as note domain truth.
- [ ] `[must]` `DRW4-04` Replace one Notebook custom list/detail/form path with
  a workbench-rendered path behind a compatibility flag.
- [ ] `[must]` `DRW4-08` Add localized note UI labels and validation messages
  while preserving note text in its original language.
- [ ] `[must]` `DRW4-09` Add read/write/delete access rules for note records and
  attachment artifacts using role presets only as capability sources.
- [ ] `[should]` `DRW4-05` Add revision/conflict handling for note edits.
- [ ] `[should]` `DRW4-06` Add events for note created, updated, deleted, and
  attachment changed.
- [ ] `[could]` `DRW4-07` Use Notebook as a first conversational resource
  operation test: "create a note", "edit this note", "attach this file".

## DRW5. Media Center Selective Adoption

Goal: apply the workbench to Media Center where the domain is resource-like
without forcing playback/control flows into CRUD.

Exit proof: playlists, roots, profile/settings, metadata claims, and read-only
catalog queries have declarative resource views while playback remains a
domain command surface.

- [ ] `[must]` `DRW5-01` Declare `media.playlist` and `media.playlist_item`
  with list/create/update/delete/add/remove/reorder operations.
- [ ] `[must]` `DRW5-02` Declare `media.root` with list/add/disable/remove,
  delete, and scan operations.
- [ ] `[must]` `DRW5-03` Declare `media.catalog_item` as read/search/facet
  first, with favorites as a typed operation.
- [ ] `[must]` `DRW5-04` Declare `media.metadata_claim` as a provenance and
  evidence-backed resource with accept/reject/comment operations.
- [ ] `[must]` `DRW5-08` Apply profile/household role and capability policy to
  catalog visibility, playlist mutation, roots administration, metadata edits,
  and playback-control commands.
- [ ] `[must]` `DRW5-09` Add localized display fields and aliases for media
  collections, playlists, profile labels, metadata claim status, and action
  confirmations.
- [ ] `[should]` `DRW5-05` Add query filters for profile, collection, favorite,
  provider, media type, status, and text search.
- [ ] `[should]` `DRW5-06` Add relation views between catalog item, playlist,
  collection, metadata claim, source, and rendition operation.
- [ ] `[should]` `DRW5-10` Add privacy and retention rules for household media
  names, local paths, external metadata provider evidence, screenshots, and
  playback traces.
- [ ] `[could]` `DRW5-07` Use Media Center to validate large-list paging,
  cursor stability, and query relevance under real data volume.

## DRW6. Research Workbench Adoption

Goal: prove that resources can be workflow-bound, append-only, evidence-gated,
and audit-heavy.

Exit proof: studies, experiments, revisions, attempts, evidence bundles, and
claim decisions are inspectable as resources, while mutation remains governed
by research workflows.

- [ ] `[must]` `DRW6-01` Declare `research.study` and `research.experiment`
  read/list/detail surfaces.
- [ ] `[must]` `DRW6-02` Declare protocol revision operations as append-only
  commands, not generic update.
- [ ] `[must]` `DRW6-03` Declare experiment workflow transitions as resource
  operations bound to workflow authority.
- [ ] `[must]` `DRW6-04` Expose attempts, artifacts, evidence bundles, and claim
  decisions as read/evidence resources.
- [ ] `[must]` `DRW6-07` Preserve source, protocol, review, evidence, and claim
  text locale while keeping scientific identifiers and resource refs canonical.
- [ ] `[must]` `DRW6-08` Bind research workflow transitions to explicit role,
  capability, approval, isolation, and evidence policies.
- [ ] `[should]` `DRW6-05` Add trace links from resource operations to workflow
  metrics evidence and research tracker receipts.
- [ ] `[should]` `DRW6-09` Add access-scoped views for unpublished evidence,
  blinded/unblinded states, reviewer comments, and external publication drafts.
- [ ] `[could]` `DRW6-06` Add Builder prototype generation of synthetic research
  workbench resources before real execution providers are wired.

## DRW7. Conversational And Channel Routing

Goal: conversation, voice, Telegram, browser, Builder, and Codex can route to
resource operations without each channel inventing its own lifecycle.

Exit proof: a feedback phrase, a voice correction, a Telegram note, a Builder
action, and a CLI command can create or operate on the same Dev Ticket resource
with traceable classification and scope.

- [ ] `[must]` `DRW7-01` Define `resource_operation_intent` as the channel-neutral
  output of conversational classification when the user asks to inspect or
  mutate a resource.
- [ ] `[must]` `DRW7-02` Route feedback notes and development requests to Dev
  Tickets through the resource operation contract.
- [ ] `[must]` `DRW7-03` Keep NLU Teacher correction, Feedback Skill intake,
  Builder planning, and resource operation execution as separate state
  machines linked by refs.
- [ ] `[must]` `DRW7-04` Add bounded clarification when the channel classifier
  cannot distinguish immediate action, correction, feedback, development, or
  personal adaptation.
- [ ] `[must]` `DRW7-08` Include locale, channel, subject, actor, delegated
  context, and access-decision refs in `resource_operation_intent`.
- [ ] `[must]` `DRW7-09` Route permission denial and missing capability outcomes
  back to conversational explanation without creating surprise Builder work.
- [ ] `[should]` `DRW7-05` Add RU/EN/STT-noisy conversation-story tests for
  resource operation intents.
- [ ] `[should]` `DRW7-06` Teach stable phrases in context, such as "record an
  improvement" for feedback and "No, I meant ..." for understanding repair.
- [ ] `[should]` `DRW7-10` Add conversational stories for role-dependent
  operation availability, delegated Builder action, and localized entity
  aliases.
- [ ] `[could]` `DRW7-07` Add per-channel compact renderers for resource forms
  and action confirmations.

## DRW8. Query, Storage, And Event Hardening

Goal: the workbench scales from a demo and Dev Tickets to real project,
skill, scenario, and domain resource use.

Exit proof: indexed queries, subscriptions, signed event receipts where needed,
and relation traversal support Builder and human workflows across multiple
resource families.

- [ ] `[must]` `DRW8-01` Add indexed storage for resource record metadata,
  relations, operation ledger, events, artifact refs, and traces.
- [ ] `[must]` `DRW8-02` Add initial snapshot plus delta subscription for
  resource queries.
- [ ] `[must]` `DRW8-03` Add explicit unsupported-query diagnostics to every
  provider adapter.
- [ ] `[must]` `DRW8-04` Add privacy/redaction checks for screenshots, logs,
  voice transcripts, DOM snapshots, local paths, and external exports.
- [ ] `[must]` `DRW8-09` Add locale indexes for translated labels, aliases,
  normalized text, original-language evidence, and canonical ref matching.
- [ ] `[must]` `DRW8-10` Add indexed access metadata for row, field, artifact,
  trace, external-export, and delegation decisions.
- [ ] `[must]` `DRW8-11` Add audit queries for who saw, changed, exported,
  verified, closed, or reopened a resource and under which policy digest.
- [ ] `[should]` `DRW8-05` Add signed or tamper-evident event receipts for
  cross-node, support, and Builder acceptance contexts.
- [ ] `[should]` `DRW8-06` Add import/migration tools for hand-written CRUD
  surfaces to resource declarations.
- [ ] `[should]` `DRW8-07` Add performance budgets for paging, filtering,
  evidence preview, and event delivery under Media Center-like volume.
- [ ] `[should]` `DRW8-12` Add quota and rate-limit traces for expensive
  queries, artifact previews, LLM classification, and autonomous repair cost
  estimation.
- [ ] `[could]` `DRW8-08` Add analytics over operation latency, validation
  failure rate, unsupported filter rate, stale revision rate, and autonomous
  repair cost.

## Deferred

- [ ] `[deferred]` `DRW-D01` Public GitHub Issue automation waits until internal
  Dev Ticket lifecycle, redaction, and human approval are validated.
- [ ] `[deferred]` `DRW-D02` Direct table editing over arbitrary SQL stores is
  not admitted until resource authority, policy, and audit are mature.
- [ ] `[deferred]` `DRW-D03` Cross-workspace shared resource collaboration waits
  for trusted development group policy and revocation.
- [ ] `[deferred]` `DRW-D04` Full visual workflow studio generation waits until
  workflow-bound resources are validated in Research Workbench or another
  second complex domain.
- [ ] `[deferred]` `DRW-D05` Fine-grained encrypted private-field storage waits
  for the next Personalization/Identity/Access privacy milestone; the workbench
  still records field sensitivity and visibility policy now.

## Recommended First Slice

```text
Dev Ticket resource definition
  -> i18n/access/privacy fields in the resource ABI
  -> unified query/list/show/action model
  -> resolved/verified/closed/reopen lifecycle
  -> artifact open/preview
  -> Builder Resource Inspector with localization/access/readiness traces
  -> Demo Metrics synthetic workbench surface with RU/EN and role fixtures
  -> Notebook note declaration behind a compatibility flag
```

This keeps real value anchored in Dev Tickets while Demo Metrics absorbs the
renderer and observability risk.

## Documentation Owners

- [Declarative Resource Workbench](declarative-resource-workbench.md) owns the
  architecture and invariants.
- This roadmap owns delivery sequencing for resource workbench contracts,
  renderer adoption, storage/query hardening, and domain pilots.
- [Development Signals Roadmap](development-signals-roadmap.md) owns
  Development Signals and Dev Ticket lifecycle work that is not workbench-wide.
- [Builder Roadmap](builder-roadmap.md) owns Builder session, Automation, and
  repair workflow readiness.
- [Operational Event Model Roadmap](operational-event-model-roadmap.md) owns
  shared event, projection, and browser subscription infrastructure.
- [Web UI Architecture](web-ui-architecture.md) owns renderer and semantic UI
  contracts outside resource-specific concerns.
