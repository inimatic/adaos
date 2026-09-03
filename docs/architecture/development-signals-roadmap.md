# Development Signals Roadmap

Status: proposed cross-domain roadmap.

Last reviewed: 2026-09-03.

This roadmap sequences the work needed to make evolution feedback a governed,
natural AdaOS interface for both people and Codex. It is subordinate to
[Development Signals And Evolution Feedback](development-signals.md) for
architecture and to the domain roadmaps for implementation details.

## Priority Model

- `[must]`: required for the first coherent Development Signal vertical slice.
- `[should]`: required before broad repeated use or unattended repair.
- `[could]`: useful ergonomic or analytics improvement.
- `[deferred]`: intentionally waits for AdaOS Issue-first support, trusted
  collaboration, or public reuse.

Maturity follows the Governed Evolution Roadmap vocabulary:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

## DS0. Contract And Storage Spine

Goal: observations become immutable, scoped records before they become
Dev Tickets, Builder tasks, NLU corrections, or Issues.

Exit proof: a user feedback note, a runtime compatibility finding, and an NLU
miss are all captured as Development Signals with scope, artifact identity,
evidence refs, dedup metadata, and terminal or deferred state; at least one
signal becomes a Dev Ticket visible to a person and consumable by Codex.

- [x] `[must]` `DS0-01` Specify `adaos.development_signal.v1` with `owner_scope`,
  `origin_scope`, `target_scope`, `artifact_refs`, conversation refs, Teacher
  refs, Builder refs, Issue refs, lifecycle state, severity, blocking,
  confidence, policy, and provenance.
- [x] `[must]` `DS0-02` Implement a workspace evolution inbox that can store
  signals for installed, catalog, remote, read-only, and not-yet-materialized
  artifacts.
- [ ] `[must]` `DS0-03` Add artifact-local signal projection for skills,
  scenarios, WebUI surfaces, and components when writable source exists,
  without making the projection the source of truth.
- [x] `[must]` `DS0-04` Add dedup and relationship keys for repeated signals,
  supersession, duplicates, stale-after-version-change, and linked repairs.
- [ ] `[must]` `DS0-05` Store screenshots, logs, transcripts, DOM/context
  snapshots, and test output as artifact refs with digest, media type,
  sensitivity, redaction, origin, and retention metadata.
- [ ] `[should]` `DS0-06` Add SDK helpers for `capture_signal`,
  `classify_signal`, `link_signal`, `defer_signal`, and `resolve_signal` so
  skills and scenarios do not write private shapes.
- [ ] `[should]` `DS0-07` Add a compact global projection for active signals by
  workspace, artifact, severity, blocker status, and pending decision.
- [ ] `[could]` `DS0-08` Provide import/migration from current
  `builder.development_feedback.v1`, Builder repair tasks, review anchors, and
  NLU Teacher promotion candidates.
- [x] `[must]` `DS0-09` Specify `adaos.dev_ticket.v1` as the user/Codex-visible
  backlog object over one or more Development Signals, with status, target,
  severity, owner, dedup group, pending action refs, Builder refs, external
  refs, and closure evidence.
- [x] `[must]` `DS0-10` Implement a workspace ticket store that keeps Dev
  Tickets local/private by default and links each ticket to its source signals.
- [x] `[must]` `DS0-11` Define ticket lifecycle states:
  `captured`, `proposed`, `accepted`, `deferred`, `waiting_for_user`,
  `waiting_for_core`, `ready_for_builder`, `in_builder`, `claimed`,
  `in_progress`, `resolved`, `verified`, `closed`, `superseded`, and `stale`;
  define `reopen` as a lifecycle operation that returns the ticket to an
  active state.
- [ ] `[should]` `DS0-12` Add ticket projection indexes by current scenario,
  skill, modal/surface, status, blocker flag, source, and target artifact
  version.
- [ ] `[must]` `DS0-13` Add first-class ticket relationships:
  `blocks`, `blocked_by`, `related`, `duplicate_of`, `supersedes`, and
  `caused_by`, with optimistic revision checks.
- [x] `[must]` `DS0-14` Add `owner_area` and stable `component_ref` fields so
  tickets can distinguish project, skill, scenario, modal/component, SDK/API,
  Builder, and core ownership without relying on display text.
- [x] `[should]` `DS0-15` Add open/read-model filters for `status_group`,
  `owner_area`, `component_ref`, `scenario_id`, `skill_id`, `modal_id`,
  `severity`, `blocking`, `owner`, `updated_since`, and full-text search.
- [x] `[should]` `DS0-16` Import legacy
  `builder.development_feedback.v1` records idempotently into the typed
  Development Feedback review registry; repair-task, review-anchor, and NLU
  candidate migration remain under `DS0-08`.

Implementation note, 2026-08-31: the first store lives in runtime state under
`development_tickets/state.json` and is intentionally local/private. It stores
`adaos.development_signal.v1` records and `adaos.dev_ticket.v1` tickets with
generic target scopes, so installed, catalog, remote, read-only, and
not-yet-materialized artifacts can be referenced before source is available.
Signals and tickets deduplicate active records, merge evidence refs, link
Builder repairs back to ticket history, and expose stable `owner_area` plus
`component_ref` for project, skill, scenario, modal/component, SDK/API,
Builder, and core ownership. The API and CLI read model supports status-group,
owner/component, scenario, skill, modal, severity, blocker, owner,
updated-time, and text filters. Artifact-local projections, rich artifact blob
storage, optimistic relationship revision checks, relevance ranking, and
subscription indexes remain open.

## DS1. Feedback Skill Intake

Goal: a person can record and review scoped tickets from UI, chat, or voice
without entering Builder.

Exit proof: a feedback action opened from a scenario header, skill panel, and
modal creates correctly scoped signals; screenshot capture hides and restores
the modal; voice input stores transcript and not raw audio by default; the
resulting Dev Ticket is visible in the same context.

- [ ] `[must]` `DS1-01` Implement the Feedback Skill modal with summary,
  category, scope display, scope correction, severity, and record/postpone/
  Builder actions.
- [ ] `[must]` `DS1-02` Wire invocation-site scope: scenario header, skill
  panel, modal, component affordance, runtime diagnostic, chat turn, and voice
  turn.
- [ ] `[must]` `DS1-03` Implement screenshot capture as an artifact flow:
  hide feedback modal, capture current surface, store artifact ref, restore
  modal, and show attachment status.
- [ ] `[must]` `DS1-04` Capture voice feedback through transcript and
  conversation refs; raw audio retention requires explicit policy or consent.
- [ ] `[must]` `DS1-05` Keep Feedback Skill conversations short and bounded to
  intake clarification. Builder planning and NLU correction stay outside this
  state machine.
- [ ] `[should]` `DS1-06` Add one-tap user choices for "record only",
  "postpone", "ask Builder to repair autonomously", and "open Builder".
- [ ] `[should]` `DS1-07` Add contextual microcopy after ambiguity, such as
  "Say 'Ada, record an improvement' for future changes" and "Say 'No, I meant
  ...' when command understanding was wrong."
- [ ] `[could]` `DS1-08` Add component-level attachment helpers for UI controls
  that can pass a stable semantic component id.
- [x] `[must]` `DS1-09` Add a scenario-header ticket affordance that opens the
  workspace ticket list and can create a scenario-scoped ticket.
- [x] `[must]` `DS1-10` Add a modal/panel ticket affordance that creates and
  lists tickets scoped to the active surface.
- [x] `[must]` `DS1-11` Add a ticket detail view with summary, status, scope,
  target version, evidence refs, screenshots, source, dedup links, and actions.
- [x] `[should]` `DS1-12` Keep the ticket UI separate from Builder while
  providing explicit "open Builder" and "repair autonomously" actions.
- [x] `[must]` `DS1-13` Apply the initial Dev Tickets filter from the
  invocation context, especially modal/panel component refs, while keeping the
  filter visible and reversible.
- [x] `[should]` `DS1-14` Add stage and component dropdown filters to the
  Dev Tickets panel.
- [x] `[must]` `DS1-15` Add comment/edit flow for ticket text and ticket notes
  after screenshot-first capture.
- [ ] `[should]` `DS1-16` Move Dev Tickets UI toward the Declarative Resource
  Workbench rendering model once the resource definition supports the current
  custom surface.

Implementation note, 2026-08-31: the first human UI lives in the AdaOS client
owner chrome rather than a standalone Feedback Skill. It adds a header entry,
ticket list/detail, feedback text intake, active modal/surface scope detection,
a screenshot capture hook that hides and restores the ticket panel, evidence
preview, state actions, stage/component filters, invocation-scoped initial
filtering, optional text search from the feedback note field for dedup
inspection, summary edit, and ticket comments. Full Feedback Skill ownership,
voice intake, bounded conversational clarification, and hardened artifact
storage/retention remain open.

## DS2. Conversational Failure Triage

Goal: AdaOS does not surprise the user by choosing between action dispatch,
NLU correction, feedback, adaptation, and development on low confidence.

Exit proof: ambiguous voice/chat utterances produce a visible clarification
before durable training, feedback, or Builder work is created.

- [ ] `[must]` `DS2-01` Define the conversational failure taxonomy:
  `asr_misrecognition`, `nlu_no_match`, `wrong_intent`, `wrong_target`,
  `wrong_action`, `missing_parameter`, `action_unavailable`,
  `development_request`, `feedback_note`, `user_adaptation`,
  `support_question`, and `runtime_failure`.
- [ ] `[must]` `DS2-02` Add a lightweight fallback classifier after deterministic
  NLU/Rasa failure and before NLU Teacher or Feedback/Builder handoff.
- [ ] `[must]` `DS2-03` Add bounded disambiguation prompts for low-confidence
  cases: perform another action, correct command understanding, record
  feedback, save a personal adaptation, or plan development.
- [ ] `[must]` `DS2-04` Route `correct_understanding` and NLU examples to NLU
  Teacher; route `development_request` and `feedback_note` to Development
  Signals; route `do_now` back to action preview/dispatch.
- [ ] `[must]` `DS2-05` Prevent feedback loops: failure to understand "record
  feedback" is an NLU/Teacher issue, not a product feedback signal by itself.
- [ ] `[should]` `DS2-06` Persist classifier result, confidence, rejected
  alternatives, user clarification, and final owner as conversation evidence.
- [ ] `[should]` `DS2-07` Add conversation-story tests for each taxonomy class,
  including RU/EN/STT-noisy variants.
- [ ] `[could]` `DS2-08` Add analytics for clarification rate, wrong-route rate,
  and user correction outcomes.

## DS3. NLU Teacher Bridge

Goal: NLU Teacher and Development Signals share evidence without sharing
state machines.

Exit proof: an NLU miss can link to a Development Signal or Builder task when
the problem is a descriptor gap or missing capability, while ordinary
understanding correction remains inside NLU Teacher.

- [ ] `[must]` `DS3-01` Add `nlu_teacher_ref` to Development Signal records:
  request id, candidate id, promotion candidate id, phrase, intent/action
  candidate, confidence, and trace ref.
- [ ] `[must]` `DS3-02` Allow NLU Teacher to create or link a Development Signal
  when a repeated miss indicates descriptor gap, missing capability, wrong
  action routing, or runtime failure.
- [ ] `[must]` `DS3-03` Allow Feedback Skill to link a signal to the active NLU
  trace when the user reports "you misunderstood" or "the wrong thing ran".
- [ ] `[must]` `DS3-04` Keep accepted Teacher corrections in scoped runtime
  overlays first; source promotion remains a Builder patch.
- [ ] `[should]` `DS3-05` Add user-visible terminal wording that distinguishes
  "correction planned", "development planned", "feedback recorded", and
  "action executed".
- [ ] `[should]` `DS3-06` Add replay evidence from originating Teacher request
  after a Builder descriptor or capability fix.
- [ ] `[could]` `DS3-07` Add a Teacher/Feedback combined history view that
  groups refs without merging records.

## DS4. Runtime Compatibility And Legacy Receiver Findings

Goal: deterministic runtime contract problems become repairable signals
and Dev Tickets instead of compatibility fallbacks or raw logs.

Exit proof: a legacy skill that handles stream/Yjs events without declared
receiver/data-route contracts produces one deduplicated compatibility signal,
one Dev Ticket, one repair context, and one Pending Action with autonomous and
interactive Builder options.

- [ ] `[must]` `DS4-01` Add compatibility finding codes for missing or invalid
  `data_routes`, `webio.receivers`, projection slots, receiver ownership, and
  route/projection mismatches.
- [ ] `[must]` `DS4-02` Convert activation, validation, stream-admission, route
  pressure, and projection-rule-miss evidence into Development Signals and
  Builder repair tasks when design-time fixable.
- [x] `[must]` `DS4-09` Convert user-visible or blocking compatibility signals
  into Dev Tickets before creating Pending Actions, so people and Codex inspect
  one backlog object rather than raw diagnostic records.
- [x] `[must]` `DS4-03` Add computed blocker fields:
  `blocking`, `run_policy`, `design_time_fixable`, and
  `autonomous_repair_eligible`.
- [x] `[must]` `DS4-04` Publish a Pending Action for user-visible or blocking
  compatibility findings with actions `preview_evidence`,
  `start_autonomous_repair`, `open_builder`, `postpone`, and where policy
  permits `run_once_with_compatibility` or `disable_until_fixed`.
- [ ] `[must]` `DS4-05` Make descriptor-only receiver fixes eligible for bounded
  autonomous repair only when they do not broaden permissions, receivers,
  external I/O, or data access beyond the observed contract.
- [ ] `[must]` `DS4-06` Require acceptance evidence: strict validation,
  activation/smoke import, expected receiver admission, negative admission for
  unrelated receivers, and no unexpected projection/route diagnostics.
- [ ] `[should]` `DS4-07` Add a migration scan that creates deferred
  compatibility signals after core ABI changes, without activating every
  affected skill.
- [ ] `[could]` `DS4-08` Add batch triage for low-risk descriptor debt.

Implementation note, 2026-08-26: the first runtime producer is the
stream/Yjs receiver admission guard. It reports
`compat.stream_receiver_policy_missing` and
`compat.stream_receiver_not_declared` into the ticket store, deduplicates by
skill/reason/receiver, and publishes a Pending Action for user-visible review.
The active runtime hook reports only the legacy policy-missing receiver case to
avoid false positives for well-declared skills receiving foreign events.
Broader validation, invalid data-route, projection-rule, route/projection
mismatch, activation/validation, and route-pressure producers remain open.

## DS5. Builder Handoff And Closure

Goal: a Dev Ticket can become autonomous or interactive Builder work without
losing its underlying signal scope, evidence, or version lineage.

Exit proof: both "repair autonomously" and "open Builder" create the same
typed Builder context; completion links the result back to the originating Dev
Ticket and signals, and closes only with evidence.

- [x] `[must]` `DS5-01` Define the handoff packet from Development Signal to
  `builder.task.v1`, `builder.repair_task.v1`, or `builder.realize_request.v1`.
- [x] `[must]` `DS5-02` Add Pending Action response handlers for
  `start_autonomous_repair` and `open_builder`.
- [x] `[must]` `DS5-03` Materialize a development context for installed,
  catalog, remote, or read-only artifacts through existing DEV source,
  local fork/overlay, upstream proposal, deferred state, or
  `not_design_time_fixable`.
- [x] `[must]` `DS5-04` Include Development Signal context in Builder packets:
  original words, scopes, versions, digests, evidence refs, NLU refs, privacy
  constraints, and acceptance expectations.
- [x] `[must]` `DS5-05` Close signals only through verified version, verified
  overlay, acceptance evidence, explicit rejection, supersession, stale
  revalidation, or not-design-time-fixable state.
- [ ] `[should]` `DS5-06` Add delayed completion notifications that deep-link to
  signal, repair, Builder run, release, and verification evidence without
  making notifications authoritative.
- [x] `[should]` `DS5-07` Support user-visible status labels: recorded,
  postponed, waiting for approval, in autonomous repair, in Builder, fixed in
  version, still blocked, or cannot be fixed locally.
- [ ] `[could]` `DS5-08` Add comparison/evaluation hooks for multiple repair
  strategies when a signal is eligible for more than one adaptation method.
- [x] `[must]` `DS5-09` Add `adaos dev ticket` commands for
  `new`, `list`, `show`, `defer`, `handoff`, `resolve`, and `close` as the
  Codex/developer CLI over the same ticket service used by the client UI.
- [x] `[must]` `DS5-10` Allow Codex to create proposed tickets during core,
  skill, scenario, or review work with source, target, reason, evidence refs,
  dedup key, proposed action, and acceptance hint.
- [x] `[must]` `DS5-11` Keep Codex-created tickets in `captured` or `proposed`
  unless deterministic policy accepts them as blockers; human or policy
  triage moves them to accepted/deferred/refused.
- [x] `[should]` `DS5-12` Add Builder context filters that surface active Dev
  Tickets for the current artifact without flooding Builder with unrelated
  workspace debt.
- [ ] `[must]` `DS5-13` Add ticket lifecycle operations for `claim`,
  `in_progress`, `comment`, `verify`, `reopen`, `duplicate`, and `related`
  across API, CLI, client UI, Builder, and Codex-facing helpers.
- [x] `[must]` `DS5-14` Keep `resolve` non-terminal: a resolved ticket must show
  candidate fix evidence, but normal closure requires verification evidence
  and an explicit `verified` or `close` transition.
- [x] `[must]` `DS5-15` Add Builder intake qualification before repair:
  `project_solvable`, `needs_source`, `needs_core`, `mixed`,
  `uncertain_sdk`, and `needs_user_clarification`.
- [x] `[must]` `DS5-16` Add batch repair planning for related tickets under the
  same project, source tree, skill/scenario/modal/component family, or shared
  core blocker, while keeping per-ticket comments and evidence. Package
  planning applies only high-confidence zero-model source qualifications,
  merges exact files and digest preconditions once, and uses a bounded
  incremental token budget rather than multiplying singleton budgets.
- [x] `[must]` `DS5-17` Make the absent-dev-source choice explicit:
  `materialize`, `fork project`, `runtime overlay`, or `defer`.
- [x] `[must]` `DS5-24` Publish completed Dev Ticket skill repairs to the user
  as a dev-to-workspace `.runtime` overlay: prepare and test the default
  workspace runtime slot from DEV source, preserve workspace source, rebuild
  the target webspace, and record an `aprobation_runtime_overlay` receipt.
- [x] `[must]` `DS5-25` Complete scenario/project acceptance publication with
  the same rule: materialize the user's workspace projection from DEV
  scenario/project source without replacing workspace source, keep an explicit
  receipt, and provide rollback/acceptance controls for the overlay.
- [x] `[must]` `DS5-26` Bind component stages to release gates: validated Trial
  is `alpha`, accepted publication is `beta`, and only durable promotion plus
  activation health evidence may produce `stable`.
- [x] `[must]` `DS5-27` Create/deduplicate project-owned `runtime_failure`
  tickets when Builder tests, scenario validation, Trial activation, or
  accepted-candidate publication fails; keep originating tickets open and
  close technical gate findings only after successful publication evidence.
- [x] `[should]` `DS5-28` Reuse the publication-gate producer for manual CLI,
  setup, migration, and other non-Builder skill/scenario activation paths,
  with source-specific policy so unattended setup failures do not flood the
  user's project inbox.
- [x] `[must]` `DS5-29` Make activation observations gate-specific and
  bidirectional: CLI/API skill tests, scenario tests/validation, preparation,
  installation, and activation emit typed failure/success evidence; a success
  closes only the matching component/space/gate finding, including JSON CLI
  validation paths.
- [x] `[must]` `DS5-30` Gate singleton autonomous repair before materializing a
  Builder repair or Skill Factory task. Require an admitted profile, exact
  target files, semantic target refs or structured operations, and acceptance
  checks; expose `qualification_required`, `bounded_patch_agent`, or
  `structured_edits` plus the estimated budget in Dev Ticket and Builder read
  models.
- [x] `[must]` `DS5-31` Add the deterministic first stage of the bounded
  qualification worker. Index the authoritative component DEV source through
  JSON/YAML/Python-aware readers, map ordinary English or Russian ticket text
  to a bounded profile/files/refs/checks envelope, retain source digest
  preconditions, expose a dry-run/apply API and Builder read model, and stop for
  clarification below high confidence. An explicit Autonomous action may apply
  a high-confidence zero-model candidate; source drift blocks work creation.
- [x] `[must]` `DS5-34` Close public skill-tool contracts during qualification.
  When one repair crosses `webui.json` and a handler, include `skill.yaml`,
  retain manifest/WebUI/handler source preconditions and a typed
  `skill_public_tool_graph` closure through package planning, and reject an
  incomplete exact change scope before the first model token.
- [x] `[must]` `DS5-32` Add the residual bounded-language qualifier for cases
  that deterministic extraction cannot resolve. Use a small Root-accounted LLM
  only to propose intent and candidate refs, resolve every ref and precondition
  in core, persist its usage receipt, and ask the user rather than admit a
  low-confidence envelope.
- [x] `[must]` `DS5-33` Require an authoritative owning DEV Project before a
  qualified repair package can create Builder work. Reuse the single existing
  DEV owner, require materialization when the owner exists only in Workspace,
  stop on ambiguous ownership, and adopt a standalone DEV skill/scenario into
  one validated alpha Project when no owner exists. Bind every package ticket
  and source signal to that Project before creating the repair so publication,
  trial, acceptance, and rollback share one ProjectRelease lineage.

Implementation note, 2026-09-03: `DS5-32` is deterministic-first. The
`builder-qualification/language` endpoint calls the Root LLM only after the
source-index qualifier returns an ambiguous bounded candidate. The model may
propose at most four indexed paths and typed concepts; core validates the
proposal schema, resolves paths and SHA preconditions from authoritative DEV
source, closes the public skill-tool graph, and persists provider usage as
ticket evidence. Invalid refs or confidence below the admission threshold
remain user clarification and cannot create Builder work.

- [ ] `[should]` `DS5-18` Add ticket artifact commands and SDK helpers for
  `artifact open`, screenshot preview, incremental evidence, optimistic
  revision, comment, claim, progress, resolve, verify, close, reopen,
  duplicate, and related.
- [ ] `[should]` `DS5-19` Add subscription support:
  initial snapshot plus ticket change events so Builder, Codex, and the client
  do not rely on polling.
- [x] `[should]` `DS5-20` Add cost estimates before autonomous repair:
  approximate token budget, runtime/test cost, expected source scope, and
  confidence/risk.
- [x] `[must]` `DS5-21` Split the user Dev Ticket lifecycle from Builder work
  lifecycles: one user ticket can spawn several Builder repair tasks, and the
  ticket detail must show them as read-only linked work items with their own
  statuses.
- [x] `[must]` `DS5-22` Carry token-accounting requirements into Builder repair
  handoff metadata: autonomous and interactive repairs use `codex.api.tokens`,
  and failed, errored, or cancelled provider calls remain billable usage events
  in the Subscription/economic stream.
- [x] `[should]` `DS5-23` Add a richer ticket history feed that interleaves the
  original user report, follow-up comments, Builder work items, validation
  evidence, user rejection, reopen notes, and delayed completion notices.

Implementation note, 2026-08-31: `adaos dev ticket` now covers create, list,
show, defer, handoff, claim, start, comment, resolve, verify, close, reopen,
related, and duplicate, but agent ergonomics are still incomplete:
artifact-open commands, relevance ranking, subscriptions, SDK/MCP helpers, and
client UI for duplicate/related remain open. Pending Action responses can
choose postpone, open Builder, or autonomous repair; Builder repair tasks link
back to the Dev Ticket and source Development Signals. The Builder workbench
open path accepts `ticket_id`, selects the target skill/scenario, stores a
`development_ticket` context in the durable workbench binding, exposes source
options when dev source is absent, filters related active tickets for the
current artifact, and emits deterministic intake qualification plus a batch
repair plan. Ticket detail and Builder context expose linked Builder repair
tasks as read-only work items, so one user ticket can spawn multiple Builder
tasks without giving human ticket actions authority over Builder task state.
Handoff metadata carries the `codex.api.tokens` Subscription accounting
requirement; actual usage remains authoritative in the root economic stream,
including failed, errored, or cancelled provider calls with reported billable
tokens. Resolution is evidence-gated, `resolved` is non-terminal, `verify`
requires verification evidence, and normal closure requires verified status.
Completed skill and scenario/project repairs can now be exposed to the user's
workspace as a dev-to-workspace `.runtime` candidate without silently
replacing stable workspace source. Explicit trial acceptance records the
ProjectRelease evidence used for verification and closure. Delayed completion
notifications, realtime subscriptions, direct artifact preview, and client UI
for duplicate/related relations remain open.

Implementation note, 2026-09-02: the authoritative Dev Ticket ABI now carries
a monotonic `revision`; lifecycle and relation mutations accept
`expected_revision` and reject stale writes atomically. Legacy workspace inbox
records are read as revision 1 and gain a persisted revision on their next
mutation. HTTP returns `409` for revision conflicts, and the same contract is
available through the Python SDK, Root MCP, and the Codex MCP bridge. Agent
operations now include `duplicate` and `related` as well as the existing
lifecycle transitions. The durable change-feed projection accepts project,
scenario, skill, modal, component, owner, kind, and text relevance filters and
returns an initial open-ticket snapshot plus cursor-based changes. This closes
the SDK optimistic-write and initial-snapshot gaps, but `DS5-13`, `DS5-18`, and
`DS5-19` remain open for client relation controls, direct artifact-open UX, and
a realtime push transport; the current feed is deliberately a durable polling
contract rather than a claimed subscription transport.

E2E acceptance note, 2026-08-31: a real autonomous repair for
`subscription_status_skill` materialized source that was absent from DEV,
created four separately visible Builder tasks under one user ticket, preserved
and revalidated the budget-stopped Codex candidate instead of repeating model
work, and activated DEV version `0.1.13` as an idempotent default-workspace
runtime overlay while workspace source remained at `0.1.12`. Task-scoped MCP
leases admitted bounded task context without secrets. Failed Codex iterations
reported `2,874,058` tokens to Root in total; the validation-only continuation
reported zero additional model usage. Automation then synchronized validation,
commit, and runtime-overlay evidence back to the repair and moved the user
ticket only to non-terminal `resolved`. That run exposed missing owning-project
identity on standalone skill targets; subsequent project-scoped handoff and
scenario/project acceptance work closes the bounded path while retaining a
compatibility fallback for legacy tickets without `project_id`.

E2E acceptance update, 2026-09-02: the real
`dticket.01M1F7TZRSS92GV1X52V77Z79H` run started from a Home Shopping List
scenario that was absent from DEV, materialized its owning project, executed a
qualified zero-model structured repair, published
`shopping_list_14504c40@0.2.3`, exposed the candidate in the user's runtime,
recorded accepted trial and ProjectRelease evidence, and completed
`resolved -> verified -> closed`. The successful terminal usage receipt was
reported to Root with zero billable tokens; failed precursor attempts remain
visible as separate Builder work/evidence. Builder context and package
handoffs now retain canonical `project_ref`/`project_id`, and packages reject
same-component tickets from different or partially unknown projects before
spending tokens. The remaining absent-source risk is legacy tickets that lack
project identity entirely; they may run singly for compatibility but cannot be
mixed with project-scoped tickets in a batch.

Demo Metrics E2E update, 2026-09-02: ticket
`dticket.01M1H4YFYNV1AVY9728R8KAVTM` materialized and repaired the
`semantic_ui_demo` project, published `semantic_ui_demo@0.10.4` with
`demo_metrics_skill@0.13.28`, exposed the candidate through the user runtime,
and completed `resolved -> verified -> closed` after accepted ProjectRelease
evidence. Two failed full-Codex attempts consumed 224,000 billable/model tokens
(192,233 cached input and 31,767 `fresh_plus_output`) before the final exact
structured repair completed with zero model tokens and a Root usage receipt.
That measured failure mode led to `DS5-30`: an unqualified singleton now stops
before repair/task creation, while admitted surgical/CRUD work defaults to a
24,000 `fresh_plus_output` ceiling and structured work advertises a zero-model
route. `DS5-31` supplies the zero-model automation step between ordinary user
text and that exact envelope; a related two-ticket package now defaults to
30,000 tokens when it touches two files, adding only bounded per-ticket and
per-file increments. The residual Root-accounted language path remains
`DS5-32`.

Owning-project update, 2026-09-02: package planning no longer uses a legacy
skill/scenario id as an implicit release identity. A standalone DEV component
is adopted into one validated `adaos.project.v1` source manifest before any
Builder task is created; repeated planning reuses that Project. A component
owned by a Workspace Project stops at `needs_materialization`, and ambiguous
DEV ownership stops at `ambiguous`. The resulting `project_id`, manifest
evidence, and history event are written back to every user ticket and linked
signal before the package repair is materialized. This closes the missing
ProjectRelease identity exposed by the Subscription E2E while preserving the
explicit materialize/fork/overlay/defer choice for sources that are not in DEV.

## DS6. Analytics, Campaigns, And Policy Hardening

Goal: feedback improves the platform without turning private observations into
uncontrolled telemetry.

Exit proof: a core ABI migration can generate scoped compatibility signals,
dedupe them, ask for bounded repair where eligible, and report aggregate
outcomes without leaking private evidence.

- [ ] `[must]` `DS6-01` Define minimum metrics for signal source, target,
  severity, duplicate rate, classification confidence, clarification rate,
  repair eligibility, time to closure, stale rate, and false-positive rate.
- [ ] `[must]` `DS6-02` Add privacy and retention policy checks for screenshot,
  voice transcript, raw audio, logs, DOM snapshots, and remote support export.
- [ ] `[must]` `DS6-03` Add campaign mode for core/runtime/schema migrations:
  scan, create signals, dedupe, batch triage, repair eligible items, and
  record unresolved debt.
- [ ] `[should]` `DS6-04` Promote recurring model-discovered symptoms into
  deterministic compatibility checkers or validation rules.
- [ ] `[should]` `DS6-05` Add dashboards only after event fields answer
  operational questions reliably.
- [ ] `[could]` `DS6-06` Add user-level preference reports that separate
  personal adaptations from shared artifact evolution.
- [ ] `[deferred]` `DS6-07` Share signals across trusted development groups only
  after GE4 defines proposal visibility, consent, and revocation.
- [ ] `[deferred]` `DS6-08` Use aggregated signal evidence for public capability
  ranking only after GE5 defines verified reuse, privacy, and comparability.

## DS7. External Issue Tracker Projection

Goal: AdaOS can link or export tickets to GitHub Issues or another external
tracker without making the external tracker the internal source of truth.

Exit proof: one internal Dev Ticket can link to an existing GitHub issue and
one redacted draft can be prepared for human approval, while private evidence
remains local.

- [x] `[should]` `DS7-01` Add `external_refs` to Dev Tickets with provider,
  repository, issue id, target path, privacy, sync mode, and provenance.
- [x] `[should]` `DS7-02` Support `none`, `link_only`, `draft_export`,
  `private_repo_issue`, `public_upstream_issue`, and `mirror_status` policy
  modes.
- [x] `[should]` `DS7-03` Generate redacted GitHub issue drafts from ticket
  summaries, public artifact versions, expected/actual behavior, and safe
  reproduction steps.
- [x] `[must]` `DS7-04` Block automatic public issue creation for screenshots,
  logs, NLU examples, DOM state, local paths, device names, runtime traces, or
  private workspace evidence unless redaction and explicit approval pass.
- [ ] `[could]` `DS7-05` Add status-only mirroring for private team repos after
  the internal ticket lifecycle is stable.
- [ ] `[deferred]` `DS7-06` Defer public upstream automation until the first
  internal ticket and Builder repair loop is validated locally.

## DS8. Core Evolution Rails

Goal: Builder can identify and raise core, runtime, API, SDK, policy, and
lifecycle needs without modifying core from a project repair context or hiding
platform gaps as project workarounds.

Exit proof: a project Dev Ticket that cannot be solved within the public
SDK/API creates or links a Core Dev Ticket, moves the project ticket to a
visible waiting state, and resumes through an event when the core capability
is released and verified.

- [x] `[must]` `DS8-01` Add `target_scope.type = core`, `owner_area = core`,
  and stable `component_ref` values such as `core:runtime`, `core:sdk`,
  `core:router`, `core:builder`, and `core:client`.
- [x] `[must]` `DS8-02` Define the first core impact taxonomy:
  `blocker`, `speed`, `generalization`, `contract_gap`,
  `observability_gap`, `lifecycle_gap`, `policy_boundary`,
  `compatibility_debt`, and `security_governance`.
- [x] `[must]` `DS8-03` Add a Builder-facing `core_capability_request`
  operation that records motivation, desired public contract, observed
  limitation, rejected workarounds, impact, blocked ticket refs, and expected
  validation.
- [x] `[must]` `DS8-04` Link project tickets to core tickets with
  `blocked_by`/`blocks` relations and a user-visible `waiting_for_core`
  status or status group.
- [x] `[must]` `DS8-05` Add core ticket lifecycle events:
  `core_ticket.created`, `core_ticket.qualified`, `core_ticket.accepted`,
  `core_ticket.deferred`, `core_ticket.released`,
  `core_ticket.verified`, `core_ticket.reopened`, and
  `core_capability.available`.
- [ ] `[must]` `DS8-06` Fan out signed core lifecycle events to linked project
  tickets, affected subnet Builders, Pending Actions, and user notifications
  where policy allows.
- [x] `[must]` `DS8-07` Prevent project Builder runs from closing tickets that
  are still blocked by unresolved core tickets unless the user explicitly
  accepts a reduced-scope result.
- [ ] `[should]` `DS8-08` Add a core backlog view for maintainers with filters
  by component, impact, affected projects/subnets, release target, and
  verification state.
- [x] `[should]` `DS8-09` Add approved projection from Core Dev Tickets to
  AdaOS Issues, private repository issues, or GitHub issue drafts after
  redaction and ownership checks.
- [x] `[should]` `DS8-10` Add priority/ranking signals based on repeated
  blockers, generalization pressure, safety impact, and release proximity.
- [ ] `[could]` `DS8-11` Add advanced-user inspection and subscription controls
  for core tickets that affect their projects.

Implementation note, updated 2026-09-02: the first core rail is implemented in the
Dev Ticket service, API, CLI, and Builder intake. Builder or Codex can create
`core_capability_request` tickets with `owner_area = core`, stable
`component_ref`, impact taxonomy, motivation, desired contract, observed
limitation, rejected workarounds, expected validation, and blocked project
ticket refs. Project tickets can be linked with `blocked_by`/`blocks` and move
to `waiting_for_core`; Builder qualification then forbids ordinary project
repair and evidence closure against unresolved core blockers unless reduced
scope is explicitly accepted. Core tickets emit created, qualified, accepted,
deferred, released, verified, and reopened lifecycle events. Verified
capabilities update linked project tickets, signals, and Pending Actions so
Builder can resume. A ranked Core backlog read model is available to API, SDK,
Root MCP, and Codex by component, impact, affected projects/subnets, release
target, verification state, and deterministic pressure score. Core or project
tickets can prepare redacted GitHub-compatible drafts or link an existing
issue while the internal ticket remains authoritative. Screenshot, trace, log,
local-path, device, and credential-like material is excluded or redacted;
draft export remains approval-gated, and Builder/Codex cannot approve it.
Signed cross-subnet fanout, maintainer backlog UI, automatic public issue
creation, and status mirroring remain open.

## DS9. SDK Understanding And Agent Product UX

Goal: ambiguous SDK/API/resource contracts, failed method application, and
user-rejected Builder results become structured learning signals, docs/API
improvements, and eval cases instead of repeated failed patches.

Exit proof: a Builder failure or user rejection produces a qualified SDK
Understanding Signal with method/resource refs, trace evidence, rejected
workarounds, and a diagnosis that routes to user clarification, Builder retry,
SDK docs/examples, SDK/API implementation, policy decision, or Core Dev Ticket.

- [x] `[must]` `DS9-01` Add signal kinds:
  `sdk_unclear_definition`, `sdk_application_failure`,
  `sdk_observability_gap`, `sdk_example_gap`, `sdk_policy_boundary`,
  `sdk_generalization_pressure`, and `builder_rejection_learning`.
- [ ] `[must]` `DS9-02` Capture Builder method/resource application traces:
  public contract ref, operation id, input summary, expected behavior,
  observed behavior, validation result, user response, and trace/test refs.
- [ ] `[must]` `DS9-03` Add rejection qualification classes:
  `requirement_ambiguity`, `builder_misread_user`, `sdk_doc_ambiguity`,
  `sdk_capability_gap`, `weak_patch`, and `insufficient_validation`.
- [ ] `[must]` `DS9-04` Route qualified signals to the right owner:
  NLU/User clarification, Builder retry, SDK documentation/example update,
  SDK/API implementation, policy review, or Core Dev Ticket.
- [ ] `[must]` `DS9-05` Add replayable eval-candidate creation from accepted
  SDK ambiguity, application failure, user rejection, and core compatibility
  findings.
- [ ] `[must]` `DS9-06` Expose an agent-facing SDK/API product surface:
  capability catalog, typed resources/tools, method contracts, known
  limitations, deprecation/compatibility notes, and common failure modes.
- [ ] `[should]` `DS9-07` Add executable examples and counterexamples for
  high-use SDK/resource operations, including RU/EN user-language traces where
  conversation initiated the task.
- [ ] `[should]` `DS9-08` Add metrics for ambiguity rate, failed application
  rate, rejected workaround rate, acceptance after docs/API fix, and eval
  regression recurrence.
- [ ] `[should]` `DS9-09` Add human audit or LLM grader support for
  qualification quality, with the audit result stored as evidence rather than
  replacing deterministic validation.
- [ ] `[could]` `DS9-10` Add model-specific guidance packs only after the
  language-neutral SDK/resource contracts and eval cases are stable.
- [x] `[must]` `DS9-11` Add workspace-authoritative
  `adaos.development_feedback.v1` with typed source/category/impact,
  project/component/SDK refs, evidence, dedup, optimistic revision, comments,
  and `observed -> triaged -> accepted/rejected -> promoted` lifecycle.
- [x] `[must]` `DS9-12` Capture bounded Development Feedback from the
  Root-accounted pre-Codex qualifier and the isolated Codex final response;
  keep it advisory and mutually exclusive with blocking core escalation.
- [x] `[must]` `DS9-13` Expose Development Feedback through SDK, authenticated
  API, Root MCP, role-aware Declarative Resource Workbench operations, and a
  project/component/ticket-scoped Builder Context Inspector projection.
- [x] `[must]` `DS9-14` Require explicit acceptance before promotion and route
  accepted observations to project review debt, SDK Understanding, or Core
  capability requests without allowing a model to grant its own authority.
- [ ] `[must]` `DS9-15` Add validator and runtime producers for public method
  application failures, including expected/observed result, exact operation,
  validation receipt, task/run ref, and user acceptance or rejection.
- [ ] `[should]` `DS9-16` Add a dedicated Builder review view over the generic
  resource projection with saved filters, related evidence/artifact opening,
  and promotion preview; do not create a second private ticket store.

Implementation note, 2026-09-03: the first SDK Understanding Signal path is
available through service, API, CLI, and Builder-facing classification. It
records method/resource refs, signal kind, diagnosis, observed behavior,
expected behavior, trace evidence, and optional links back to project tickets.
Pre-Codex LLM and Codex observations now enter a separate workspace review
registry and are visible through Resource Workbench, Root MCP, and the scoped
Builder Context Inspector. Legacy Builder session feedback imports
idempotently. Promotion remains an explicit human/policy operation. Validator
application traces, replay/eval creation, richer rejection taxonomy,
docs/example routing, the dedicated review view, and qualification metrics
remain open.

## Recommended First Slice

The first implementation should be narrow:

```text
Feedback Skill modal + workspace inbox
  -> screenshot artifact ref
  -> Development Signal schema
  -> Dev Ticket
  -> runtime receiver compatibility finding
  -> BuilderRepairService report
  -> Pending Action: autonomous repair or open Builder
  -> descriptor fix
  -> strict receiver validation evidence
```

This proves the full lifecycle while avoiding the harder unresolved pieces of
global Issues, public collaboration, and broad autonomous development.

The next local implementation slice should prove the human/Codex/Builder
workflow as one loop:

```text
Dev Tickets UI with invocation-scoped filters
  -> comment/edit and evidence preview
  -> Builder intake qualification
  -> batch repair or source strategy choice
  -> autonomous repair result
  -> per-ticket comment + validation evidence
  -> resolved
  -> human or deterministic verify
  -> closed or reopened
```

The first core-evolution slice should be narrower:

```text
project ticket blocked by missing SDK/API capability
  -> Builder creates Core Dev Ticket with rejected workaround evidence
  -> project ticket waits on blocked_by relation
  -> core maintainer qualifies or defers
  -> core release emits capability-available event
  -> Builder resumes and validates original project ticket
```

## Documentation Owners

- [Development Signals And Evolution Feedback](development-signals.md) owns the
  architecture.
- This roadmap owns the Development Signal sequencing.
- [Governed Evolution Roadmap](governed-evolution-roadmap.md) owns the
  cross-domain milestone gates.
- [NLU Teacher Evolution Roadmap](nlu-evolution-roadmap.md) owns NLU and
  conversational repair gates.
- [Builder Roadmap](builder-roadmap.md) owns Builder readiness, repair context,
  and release evidence.
- [Pending Actions](pending-actions.md) owns durable user decisions.
- Future AdaOS Issue architecture owns accepted support/development work after
  Issue-first repair is admitted.
