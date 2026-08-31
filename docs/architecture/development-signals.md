# Development Signals And Evolution Feedback

Status: target architecture.

Last reviewed: 2026-08-31.

This document defines the AdaOS boundary for user, runtime, review, and
conversation feedback that may drive software evolution. It sits between raw
observations and durable development work. It does not replace AdaOS Issues,
Builder tasks, NLU Teacher candidates, Pending Actions, incidents, or release
records.

## Purpose

AdaOS needs a natural way for people and deterministic runtime checks to say
"something should change" without turning every utterance, screenshot, or
exception into an immediate development task.

A Development Signal is an immutable, scoped observation that may later feed a
user preference, NLU correction, Dev Ticket, Builder repair, AdaOS Issue,
deferred idea, or rejected duplicate. It preserves the original evidence and
artifact version before triage chooses the appropriate lifecycle.

A Dev Ticket is the human- and Codex-visible backlog object built from one or
more Development Signals. People, Codex, and Builder should work with Dev
Tickets; Development Signals remain the lower evidence records.

The model follows established human-AI interaction practice:

- Microsoft Human-AI Interaction Guidelines emphasize uncertainty handling,
  cautious adaptation, and granular feedback during ordinary interaction:
  <https://www.microsoft.com/en-us/research/blog/guidelines-for-human-ai-interaction-design/>.
- Google People + AI Guidebook frames human-AI products as bidirectional
  feedback loops with explicit feedback and control:
  <https://pair.withgoogle.com/guidebook/>.
- NIST AI RMF and the Generative AI Profile call for risk management,
  structured feedback, human review, tracking, and documentation where
  generated outputs or automated decisions affect people:
  <https://www.nist.gov/itl/ai-risk-management-framework> and
  <https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf>.
- Modern agent frameworks use durable human-in-the-loop pauses before risky or
  sensitive actions. AdaOS implements that pattern through Pending Actions,
  not through hidden prompt-only state. Reference examples include OpenAI
  Agents human review and LangGraph interrupts:
  <https://developers.openai.com/api/docs/guides/agents/guardrails-approvals>
  and <https://docs.langchain.com/oss/python/langgraph/interrupts>.
- Conversational systems treat no-match, correction, clarification,
  interruption, cancel, and handoff as explicit repair states. Rasa CALM and
  Dialogflow CX test cases are useful reference patterns:
  <https://rasa.com/docs/learn/concepts/conversation-patterns/> and
  <https://docs.cloud.google.com/dialogflow/cx/docs/concept/test-case>.
- Software-engineering agent benchmarks and papers such as SWE-bench,
  SWE-agent, Agentless, dialogue-driven coding benchmarks, and SWE-EVO point
  to the same product lesson: agents need explicit task records, scoped
  evidence, reproducible validation, and interfaces designed for agents rather
  than hidden chat memory:
  <https://www.swebench.com/original.html>,
  <https://arxiv.org/abs/2405.15793>,
  <https://arxiv.org/abs/2407.01489>,
  <https://arxiv.org/html/2606.13995v1>, and
  <https://arxiv.org/html/2512.18470v5>.
- MCP Resources provide a useful external reference for typed, discoverable
  context available to LLM applications, while AdaOS keeps authority,
  mutation, and lifecycle inside its own resource contracts:
  <https://modelcontextprotocol.io/specification/2026-07-28/server/resources>.
- OpenTelemetry Semantic Conventions are a naming reference for trace,
  metric, log, event, and resource fields; AdaOS should use stable semantic
  attributes for ticket events, Builder operations, and SDK/application
  failures instead of ad hoc log text:
  <https://opentelemetry.io/docs/concepts/semantic-conventions/>.
- Production AI evaluation guidance reinforces that user rejection,
  ambiguity, and repair outcomes should become replayable eval cases when
  they expose model, SDK, or product-contract weaknesses:
  <https://developers.openai.com/api/docs/guides/evaluation-best-practices>.

These are design anchors, not dependencies. AdaOS keeps durable mutation,
approval, validation, and runtime dispatch inside its own governed contracts.

## Position In Governed Evolution

The target flow is:

```text
human, runtime, review, or conversation observation
  -> contextual intake
  -> Development Signal
  -> Dev Ticket when action or tracking is needed
  -> triage, deduplication, and Builder intake qualification
  -> user adaptation | NLU Teacher correction | Builder repair/task |
     SDK understanding signal | Core Dev Ticket | AdaOS Issue |
     deferred idea | rejection
  -> validation, versioning, activation, or closure evidence
```

Development Signals are evidence and routing records. They are not commands,
Tickets, Issues, chat history, or approvals.

Dev Tickets are the first managed work objects for evolution feedback before
the future AdaOS Issue aggregate is admitted. A ticket may aggregate duplicate
signals, carry human-readable state, accept Codex-authored deferred work,
publish Pending Actions, and hand off to Builder. It is still narrower than an
AdaOS Issue: it does not own support SLAs, cross-user collaboration, public
upstream negotiation, or final release authority.

An AdaOS Issue remains the durable work source of truth once support or
development accepts a problem for tracked execution. Until that Issue layer is
implemented, workspace-scoped Development Signals may bridge to the existing
Builder repair and task records.

GitHub Issues are not the primary object for this lifecycle. They may later be
linked through `external_refs`, draft-exported, or mirrored after redaction and
human approval, but internal Dev Tickets remain the private source of truth for
workspace, skill, scenario, runtime, and Builder evolution debt.

## Dev Tickets

Dev Ticket is the user and Codex control surface over Development Signals.
It exists so that development debt is not stored as documentation TODOs,
unstructured chat, or scattered review remarks.

Target commands should follow the existing `adaos dev` surface instead of
creating a separate `dev-signal` noun:

```text
adaos dev ticket new
adaos dev ticket list
adaos dev ticket show <id>
adaos dev ticket defer <id>
adaos dev ticket handoff <id> --mode autonomous|interactive
adaos dev ticket resolve <id> --evidence <ref> --version <artifact-version>
adaos dev ticket verify <id> --evidence <ref>
adaos dev ticket close <id> --reason duplicate|stale|refused|not-design-time-fixable
adaos dev ticket reopen <id> --reason <reason> --evidence <ref>
```

`resolve` means that a candidate fix or decision exists and evidence has been
attached, usually tied to a version, overlay, Builder repair result, or
not-design-time-fixable decision. It is not terminal acceptance. `verify`
means the required validation or human acceptance evidence has passed. `close`
is the terminal state for the current ticket lineage. `reopen` creates a new
lifecycle event and returns the ticket to an active state when regression,
insufficient evidence, or changed scope is found.

The first UI should expose tickets before broad CLI ergonomics:

- a scenario-header entry point for tickets scoped to the current scenario;
- a modal or panel entry point for tickets scoped to that surface;
- a context-filtered ticket list;
- a ticket detail view showing summary, scope, status, target version,
  evidence refs, screenshots, related signals, and available actions;
- actions for postpone, open Builder, start autonomous repair, resolve, verify,
  close, reopen, and preview evidence.

Development Signals remain the immutable evidence underneath. Dev Tickets own
human-readable queue state, dedup grouping, and user/Codex workflow affordance.

Implementation status, 2026-08-31:

- Runtime receiver compatibility guard creates deduplicated
  `runtime_compatibility_debt` Dev Tickets for missing receiver/data-route
  declarations and publishes a Pending Action when the finding is user-visible.
- `/api/development-tickets` exposes list, show, create, respond, defer,
  handoff, resolve, verify, close, reopen, and evidence preview over the same
  ticket service as `adaos dev ticket`.
- The AdaOS client exposes a header Dev Tickets button, ticket list/detail,
  feedback intake, source/materialization options, evidence preview, postpone,
  Builder handoff, autonomous repair, resolve, verify, close, reopen, and
  stage/component filters.
- Builder handoff links the ticket to a repair task, opens the Builder
  workbench with ticket/target context, and records development-source choices
  (`use_existing_dev_source`, `materialize_dev_source`, `create_local_fork`,
  `create_runtime_overlay`, `defer`) when source is absent.
- Ticket resolution currently requires evidence refs; the target verification
  split above keeps final acceptance as a separate `verify`/`closed` step.
  Close without a fix uses the separate `close` lifecycle.
- The UI is still a custom client surface, not yet a declarative Resource
  Workbench rendering, and it still needs stronger context filters from modal
  invocation, comment/edit flows, claim/in-progress, related/duplicate links,
  subscriptions, artifact-open commands, and agent-oriented SDK helpers.

Target lifecycle, 2026-08-30:

```text
captured/proposed
  -> accepted
  -> claimed/in_progress
  -> resolved
  -> verified
  -> closed
```

`reopen` is an operation, not a permanent status. It appends regression or
insufficiency evidence and returns the ticket to `accepted`, `claimed`, or
`in_progress` according to owner policy.

`deferred`, `waiting_for_user`, and `waiting_for_core` are pause states. They
remain active backlog states, not terminal closure, and they must preserve the
reason, owner, wake-up condition, and related ticket refs.

## Codex Producer Boundary

Codex may create or update Dev Tickets while developing AdaOS core, skills, or
scenarios. This is the preferred path for deferred debt discovered during
implementation or review. Codex should not use documentation TODOs as the
managed backlog when a ticket can be created.

A Codex-created ticket must include:

- source: `codex_review`, `core_change`, `skill_review`,
  `scenario_review`, `compatibility_scan`, or `runtime_guard`;
- target: core, runtime component, skill, scenario, WebUI surface, or
  component;
- affected version, digest, file, contract, or runtime ref when known;
- reason why the work is not fixed in the current change;
- evidence refs such as test, log, file, contract, trace, or screenshot;
- dedup key;
- proposed action;
- acceptance hint or validation expectation.

Use compact evidence refs instead of prose-only TODOs:

```text
file:src/adaos/sdk/core/decorators.py
test:tests/test_sdk_subscriptions.py::test_stream_subscription_reports_missing_receiver_policy
runtime_guard:compat.stream_receiver_policy_missing
trace:pending_actions.created:development_ticket.runtime_compatibility.review
```

When Codex finds deferred work during review, core work, skill development, or
scenario development, it should create or update a Dev Ticket through
`adaos dev ticket new|defer|handoff|resolve|close` or the SDK/service helper.
Documentation may explain the decision, but it is not the managed backlog.

Machine-created tickets should start as `captured` or `proposed` unless policy
or deterministic runtime evidence marks them as accepted blockers. A person or
policy gate can later accept, defer, refuse, or route them to Builder.

## Builder Intake, SDK Understanding, And Core Evolution Rails

Builder and Codex operate in a project context. They may change a skill,
scenario, project overlay, or generated development source only inside the
capabilities exposed by public AdaOS SDK/API contracts. They must not mutate
core from a project repair task, and they must not hide a core or SDK gap as a
fragile project workaround.

The Builder intake classifier runs before planning a repair. Its first target
classes are:

- `project_solvable`: the requested change can be implemented within the
  current project, skill, scenario, or overlay contract.
- `needs_source`: source must be materialized, forked, or overlaid before
  implementation can start.
- `needs_core`: the project request is blocked by a missing or faulty core,
  runtime, API, SDK, policy, or lifecycle capability.
- `mixed`: part of the request is project-solvable, but another part requires
  core or SDK evolution.
- `uncertain_sdk`: Builder cannot apply an SDK/API definition confidently.
- `needs_user_clarification`: the user intent is not stable enough to plan a
  repair or capability request.

`uncertain_sdk` is a first-class outcome, not a soft failure. It creates or
links an SDK Understanding Signal so that unclear contracts become systematic
improvements rather than repeated failed patches.

SDK Understanding Signals use specialized kinds:

- `sdk_unclear_definition`: the method, resource, or policy definition is
  ambiguous or underspecified.
- `sdk_application_failure`: Builder tried to apply the documented method and
  the result failed validation, runtime behavior, or user acceptance.
- `sdk_observability_gap`: the public surface did not expose enough state,
  trace, or evidence to make a safe decision.
- `sdk_example_gap`: the contract may be sound, but executable examples,
  counterexamples, or migration notes are missing.
- `sdk_policy_boundary`: the desired behavior crosses an access, approval,
  privacy, or deployment boundary that is not expressible enough.
- `sdk_generalization_pressure`: repeated project-local workarounds suggest a
  reusable SDK/API capability.
- `builder_rejection_learning`: a user rejected a Builder result and the
  rejection must be qualified before it becomes an implementation retry.

User rejection is evidence, not diagnosis. The next qualification must
separate at least these causes:

- `requirement_ambiguity`: the user requirement was underspecified or changed.
- `builder_misread_user`: Builder misunderstood the user but the SDK/API was
  clear enough.
- `sdk_doc_ambiguity`: public SDK/API wording, examples, or constraints led to
  a plausible but wrong implementation.
- `sdk_capability_gap`: the public contract lacks a needed capability.
- `weak_patch`: the chosen implementation was poor even though the route was
  valid.
- `insufficient_validation`: Builder lacked replay, test, preview, or human
  acceptance evidence.

When a project ticket requires platform evolution, Builder creates or links a
Core Dev Ticket instead of attempting a hidden core change. A Core Dev Ticket
has `target_scope.type = "core"`, a stable `component_ref` such as
`core:runtime`, `core:sdk`, `core:router`, `core:builder`, or
`core:client`, and links back to all blocked project tickets.

Core tickets must include:

- motivation in project/user language;
- desired public SDK/API/resource contract;
- observed limitation, error, or ambiguity;
- rejected workarounds and why they are unsafe or insufficient;
- impact classification;
- affected skills, scenarios, projects, subnets, versions, traces, and tests;
- expected validation or adoption evidence.

The first core impact taxonomy is:

- `blocker`: the project cannot be implemented without core evolution.
- `speed`: the project is possible, but cost or latency is materially worse.
- `generalization`: several projects need the same reusable platform shape.
- `contract_gap`: an SDK/API/resource contract is missing or inconsistent.
- `observability_gap`: safe repair is blocked by missing traces or state.
- `lifecycle_gap`: lifecycle states, evidence, activation, verification, or
  rollback semantics are insufficient.
- `policy_boundary`: access, privacy, approval, or ownership rules need a
  product decision.
- `compatibility_debt`: legacy behavior works only through compatibility
  fallback.
- `security_governance`: the change touches trust, secrets, external I/O, or
  destructive operations.

Project tickets that depend on core evolution move to `waiting_for_core` or a
linked active state and add a `blocked_by` relation. They remain visible in
the project backlog so users and subnet Builders can see why work paused.
Core tickets move through their own lifecycle:

```text
captured/proposed
  -> qualified
  -> accepted | deferred | refused
  -> planned
  -> released
  -> verified
  -> closed
```

Core lifecycle events should fan out as signed AdaOS events to linked project
tickets, affected Builders, and user-visible Pending Actions where policy
allows:

- `core_ticket.created`
- `core_ticket.qualified`
- `core_ticket.accepted`
- `core_ticket.deferred`
- `core_ticket.released`
- `core_ticket.verified`
- `core_ticket.reopened`
- `core_capability.available`

This gives subnet Builders a recovery path: pause on a core blocker, resume
when the capability is available, revalidate the original ticket, and notify
the user only with project-level wording. Advanced users may inspect or join
the core ticket flow, but ordinary feedback intake remains project-scoped.

External AdaOS Issues or GitHub Issues are projections from core tickets, not
their authority. Projection requires redaction, ownership checks, and explicit
approval because core tickets may contain private project context, traces, and
user language.

## Core Record

The minimum conceptual record is:

```json
{
  "schema": "adaos.development_signal.v1",
  "signal_id": "dsig_...",
  "kind": "feedback_note | development_request | compatibility_finding | runtime_failure | review_comment | nlu_failure | user_adaptation_request | sdk_unclear_definition | sdk_application_failure | sdk_observability_gap | sdk_example_gap | sdk_policy_boundary | sdk_generalization_pressure | builder_rejection_learning | core_capability_request",
  "summary": "...",
  "original_input_ref": "...",
  "status": "open",
  "severity": "info | low | medium | high | critical",
  "blocking": false,
  "owner_area": "project | skill | scenario | sdk | api | core | builder | user",
  "component_ref": "scenario:web_desktop.modal:nlu_teacher | core:sdk | skill:media_center",
  "classification_confidence": 0.0,
  "owner_scope": {
    "type": "workspace",
    "id": "..."
  },
  "origin_scope": {
    "type": "scenario | skill | webui | component | conversation | runtime",
    "id": "...",
    "surface": "header | modal | panel | widget | voice | chat",
    "component_id": "..."
  },
  "target_scope": {
    "type": "skill | scenario | project | webui | component | runtime | nlu | sdk | api | builder | core | unknown",
    "id": "...",
    "version": "...",
    "digest": "...",
    "source": "dev | workspace | installed | catalog | remote | unknown"
  },
  "artifact_refs": [],
  "conversation_ref": {},
  "nlu_teacher_ref": {},
  "builder_ref": {},
  "issue_ref": {},
  "relation_refs": [
    {
      "type": "blocks | blocked_by | related | duplicate_of | supersedes | caused_by",
      "target_ref": "dticket:..."
    }
  ],
  "policy": {},
  "created_at": "...",
  "created_by": "..."
}
```

`owner_scope` answers where the signal is stored and governed. `origin_scope`
answers where the user or runtime observed it. `target_scope` answers what is
likely to change.

This distinction matters when an installed skill has no DEV checkout. The
signal should still be stored in the workspace inbox with artifact identity,
then linked to a Builder fork, overlay, upstream request, or deferred record
when development becomes possible.

## Storage And Versioning

Development Signals are created in the workspace evolution inbox by default.
They may also be projected into the artifact-local evolution log of a skill,
scenario, or WebUI surface when that source exists in a writable development
space.

Required invariants:

- A signal is immutable except for lifecycle state and relationship links.
- The signal records the artifact version, digest, activation id, and runtime
  context where it was observed.
- A signal created against `skill@1.4.2` does not automatically authorize a
  patch against `skill@1.5.0`; it must be revalidated or marked stale.
- Binary evidence such as screenshots, audio snippets, DOM snapshots, logs, or
  test output is stored as artifact refs with digest, media type, sensitivity,
  origin, and retention policy. The signal stores only references and summary
  metadata.
- Global projections and dashboards are indexes. They are not the source of
  truth for artifact lineage, approvals, releases, or Issues.

Target lifecycle:

```text
captured
  -> classified
  -> needs_clarification | triaged | duplicate | rejected
  -> deferred | adaptation_applied | teacher_candidate |
     repair_created | issue_created
  -> in_progress
  -> resolved_by_version | resolved_by_overlay |
     not_design_time_fixable | stale | superseded
```

## Feedback Skill Boundary

The Feedback Skill owns intake surfaces for user remarks and proposals.

It may:

- collect text, voice transcript, category, severity, and user intent;
- infer the initial scope from where it was opened;
- let the user adjust the scope when the automatic choice is wrong;
- capture a screenshot by hiding its own modal, taking the image, storing it
  as an artifact, and restoring the modal;
- attach selected logs, visible UI state, runtime diagnostics, and conversation
  refs when policy allows;
- ask short clarifying questions needed to make the signal useful;
- create a Development Signal;
- offer "record", "postpone", "open Builder", or "ask Builder to repair
  autonomously" actions through Pending Actions or immediate UI choices.

It must not:

- own Builder planning, staged development, patches, acceptance criteria, or
  release decisions;
- apply NLU Teacher candidates;
- silently mutate skill, scenario, workflow, or conversational source;
- retain raw audio, screenshots, or logs without the retention and sensitivity
  policy attached to the artifact.

Feedback conversations are short intake sessions. Builder conversations are
development sessions. They may share `signal_id`, `repair_id`, or `issue_id`,
but they do not share one state machine.

## Context From Invocation Site

The invocation location supplies the default scope:

| Invocation site | Default origin scope | Typical target scope |
| --- | --- | --- |
| Scenario header | `scenario` | scenario or active application surface |
| Skill panel | `skill` | skill |
| Modal | `skill` or `scenario` plus `surface=modal` | modal owner or component |
| Widget affordance | `component` | component owner |
| Runtime diagnostic | `runtime` | skill, scenario, route, projection, or core component |
| Voice/chat turn | `conversation` | action target, NLU, feedback skill, or Builder |

The UI should show the selected scope in plain language and allow correction.
Scope correction changes routing metadata; it does not rewrite the original
observation.

## Conversational And Voice Boundary

Conversational input must not force AdaOS to guess whether the user expects an
immediate action, NLU correction, feedback note, or development request.

AdaOS should classify uncertain utterances before dispatch:

| Class | Meaning | Owner |
| --- | --- | --- |
| `do_now` | The user expects an immediate action. | NLU/action router |
| `correct_understanding` | The utterance was misunderstood. | NLU Teacher |
| `correct_action` | The intent was understood but the selected target or action was wrong. | NLU Teacher plus router evidence |
| `development_request` | The user asks to add or change capability. | Feedback Skill -> Builder |
| `feedback_note` | The user records a remark without requesting immediate repair. | Feedback Skill |
| `user_adaptation` | The user wants local personal behavior or layout change. | Personalization/adaptation layer |
| `support_question` | The user asks what happened or why. | Support/read model |
| `runtime_failure` | A command failed after dispatch. | Runtime evidence -> repair |

If confidence is low, AdaOS asks a bounded clarification:

```text
Do you want me to perform another action, correct command understanding, or
record a development request?
```

The wording can be localized and shortened by channel. The important
property is that the proposed next step is visible before a durable change,
training example, or Builder task is created.

The system should also teach stable interaction phrases in context, not
through long tutorials. After a relevant ambiguity it may say, for example:

```text
For future improvements, say: "Ada, record an improvement."
If I understood the command wrong, say: "No, I meant ..."
```

These hints are UX affordances, not parser dependencies.

## NLU Teacher Boundary

NLU Teacher owns the lifecycle of understanding corrections:

```text
miss or correction
  -> teacher request
  -> candidate
  -> preview/test
  -> approval
  -> scoped runtime overlay
  -> promotion candidate
  -> Builder patch when reusable source should change
```

Feedback Skill may create a signal that references an NLU Teacher request,
candidate, trace, or promotion candidate. NLU Teacher may create a signal or
Builder task when a repeated miss indicates a descriptor gap or missing
capability. Neither side should copy the other's durable state.

Routing rules:

- "You misunderstood; I meant X" belongs to NLU Teacher.
- "This voice correction flow is confusing" belongs to Feedback Skill with
  target `nlu_teacher` or the owning UI surface.
- "Add a new command/capability" may enter through NLU Teacher but becomes a
  Development Signal or Builder task before source changes.
- Repeated NLU misses can create `nlu_failure` signals, but a missing
  capability must not be hidden as another regex/example candidate.

## Builder Handoff Boundary

The user-facing branch is:

```text
record only | postpone | ask Builder to repair autonomously | open Builder
```

Both Builder options share the same handoff contract:

- signal refs and original user words;
- owner, origin, and target scopes;
- artifact versions and digests;
- evidence artifact refs;
- classification, risk, and policy constraints;
- acceptance expectations and replay phrases when applicable.

`autonomous` means Builder may execute the bounded repair pipeline and report
back later through Pending Actions or notifications. `interactive` means a
Builder conversation opens immediately in the skill or scenario context. In
both cases, Builder owns planning, patching, validation, and closure.

If the target artifact is installed, catalog, remote, or read-only, Builder
first materializes an authorized development context:

```text
existing DEV source | materialize source | fork project | runtime overlay |
upstream proposal | not_design_time_fixable | deferred
```

Development Signals remain in the workspace inbox and are linked to the
materialized development lineage. They are not moved blindly into a checkout.

Builder handoff is an intake pipeline, not just a button:

```text
ticket intake
  -> qualification
  -> optional batch grouping
  -> source strategy
  -> repair planning
  -> implementation
  -> validation evidence
  -> resolved
  -> verified/closed by acceptance gate
```

Batch grouping is allowed when tickets share the same project, source tree,
skill/scenario/modal/component family, or root core blocker. It should reduce
repair overhead without hiding ticket-level evidence. Builder must keep one
result comment and validation evidence per ticket even when the code change is
shared.

Autonomous repair may move a ticket to `resolved` with evidence, comment on
the ticket, and publish a candidate release or runtime overlay according to
policy. It should not silently `verify` or `close` unless the ticket policy
defines a deterministic acceptance gate and the evidence passes.

When a skill or scenario is not yet in development space, the default source
strategy is project-level: materialize or fork the owning project when that is
the distribution unit; otherwise materialize the individual artifact or create
an explicit runtime overlay. Builder must show the choice to the user or
operator as `materialize`, `fork`, `overlay`, or `defer`.

When Dev Tickets are opened from a modal or panel, the initial UI query should
be scoped to the current component ref to reduce noise. The filter must remain
visible and reversible so the user can switch to the wider scenario, project,
or workspace queue.

## Runtime Compatibility Findings

Compatibility findings are Development Signals created by deterministic
runtime or validation checks.

For legacy stream/Yjs receiver declarations, the desired sequence is:

```text
activation, validation, or stream admission detects missing receiver policy
  -> compatibility_finding signal
  -> BuilderRepairService.report with dedup key
  -> Pending Action if blocking or user-visible
  -> descriptor_fix repair or manual review
  -> strict validation and replay evidence
```

The finding should include:

- affected skill/scenario, version, digest, and activation id;
- missing or mismatched `data_routes`, `webio.receivers`, or projection rule;
- the receiver/topic/event that triggered the finding;
- whether compatibility fallback allowed, degraded, or blocked execution;
- `blocking`, `run_policy`, `design_time_fixable`, and
  `autonomous_repair_eligible`;
- validator, runtime guard, import, route-pressure, or projection evidence.

The blocker flag should be computed from contract checks and policy, not
stored as a free-form maintainer assertion. Maintainer-declared debt may be
recorded, but runtime incompatibility must be reproducible from evidence.

## Pending Actions

Pending Actions carry durable human decisions about a signal. They are not
the signal or ticket source of truth.

Typical evolution actions:

- `preview_evidence`
- `record_only`
- `postpone`
- `start_autonomous_repair`
- `open_builder`
- `disable_until_fixed`
- `run_once_with_compatibility`
- `refuse`

High-risk choices require explicit approval according to policy:

- new permissions;
- external I/O;
- credential or secret handling;
- destructive migration;
- broad receiver or data-route expansion;
- public promotion of private feedback or NLU examples.

## External Issue Trackers

AdaOS should support GitHub Issues and similar systems as optional external
projections, not as the primary backlog.

The internal relationship is:

```text
Development Signal -> Dev Ticket -> Builder Change
                         |
                         +-> optional external issue or upstream proposal
```

Create external issues only when policy and ownership make them useful:

- the target skill, scenario, or core component is backed by a GitHub
  repository;
- the work must be sent upstream to a maintainer;
- a team already uses a private repository backlog;
- a public bug report or feature request is explicitly approved;
- a release, pull request, commit, or upstream discussion needs a stable
  external link.

Default behavior is local and private. AdaOS must not automatically publish
user feedback, screenshots, logs, NLU examples, DOM state, local paths, device
names, or runtime traces to a public issue tracker.

Core Dev Tickets follow the same rule. A core ticket may become an AdaOS Issue
or GitHub Issue only after qualification, redaction, ownership checks, and
explicit approval. The public issue should describe the desired SDK/API/core
contract and safe reproduction evidence; private project links, user text,
screenshots, and local traces remain inside the internal ticket.

Target integration modes:

- `none`: only the internal Dev Ticket exists.
- `link_only`: the ticket links to an existing external issue.
- `draft_export`: AdaOS prepares a redacted issue draft for human approval.
- `private_repo_issue`: create an issue in a private repository or
  organization.
- `public_upstream_issue`: create a public upstream issue only after redaction
  and explicit approval.
- `mirror_status`: synchronize status and stable links without copying private
  evidence or comments wholesale.

External issue payloads contain sanitized summaries, affected public versions,
expected and observed behavior, and safe reproduction steps. The internal Dev
Ticket retains the full evidence bundle and privacy policy.

Example external reference:

```json
{
  "external_refs": [
    {
      "provider": "github",
      "repo": "org/media-indexer-skill",
      "issue": 123,
      "path": "skills/media_indexer",
      "privacy": "private",
      "sync": "link_only"
    }
  ]
}
```

## Privacy And Retention

Development Signals may contain sensitive information because they are created
from live UI context, conversation, voice, screenshots, logs, and runtime
diagnostics.

Required policy:

- store summary and transcript by default, not raw audio;
- store screenshots and DOM/context snapshots only as artifact refs with
  sensitivity, redaction status, and retention;
- default to local-only retention unless the deployment profile explicitly
  allows remote support or shared development;
- keep user-authored remarks separate from model summaries;
- treat signals, screenshots, transcripts, logs, and prior Issues as untrusted
  model input;
- require explicit approval before promoting private feedback into reusable or
  public artifacts.

## Metrics

The first metrics should answer operational questions, not build a dashboard
for its own sake:

- signal capture count by source and target type;
- duplicate rate;
- classification confidence and clarification rate;
- autonomous repair eligibility and success rate;
- time from signal to triage, repair, and closure;
- stale-after-version-change rate;
- user adaptation versus shared artifact change rate;
- NLU miss, correction, and wrong-action rates;
- screenshot/log/audio attachment retention and redaction outcomes;
- false-positive and refused-repair rate.
- SDK ambiguity and application-failure rate by method, resource, and example;
- user rejection cause split: requirement ambiguity, Builder misunderstanding,
  SDK ambiguity, capability gap, weak patch, or insufficient validation;
- core blocker count, time waiting for core, release-to-resume latency, and
  resumed project-ticket success rate;
- repeated workaround rate and accepted generalization proposals;
- eval candidates created from rejected repairs, ambiguous SDK use, and
  runtime/core compatibility findings.

These metrics help decide where to improve skills, scenarios, NLU surfaces,
runtime contracts, and user education.

## Ownership

| Contract | Owner |
| --- | --- |
| Development Signal schema, lifecycle, storage, and projections | This document and the Development Signals Roadmap |
| Dev Ticket schema, lifecycle, UI, CLI, and internal backlog state | This document and the Development Signals Roadmap |
| Human choice and deferred response | Pending Actions |
| NLU understanding corrections | NLU Teacher |
| Builder planning, implementation, validation, and release evidence | Builder |
| Builder intake qualification, batch grouping, and SDK/API limitation reporting | Builder plus Development Signals Roadmap |
| SDK Understanding Signals and agent-facing SDK/API product UX | SDK/API owners plus Builder Roadmap |
| Core Dev Tickets, core impact taxonomy, and capability-release events | Core maintainers plus Development Signals Roadmap |
| Optional GitHub or external issue projection | Development Signals Roadmap plus plugin/integration owner |
| Durable accepted work and support lifecycle | Future AdaOS Issue architecture |
| Runtime guard, incident, and operational evidence | Runtime Guarding, Incident Registry, Operational Event Model |
| Artifact versions, release lineage, and activation | Artifact/source/activation architecture |
| User-specific preferences and personalization | Personalization, Identity, And Access |

## Current Reality

Existing foundations include Builder repair tasks, Builder development
feedback, Builder handoff schemas, review anchors, Pending Actions, NLU
Teacher candidates, conversation stories, runtime incidents, projection
diagnostics, artifact refs, and skill runtime declaration checks.

Implemented first slice, 2026-08-20:

- first-class `adaos.development_signal.v1` and `adaos.dev_ticket.v1` ABI
  schemas;
- local/private workspace inbox at runtime state
  `development_tickets/state.json`;
- signal and ticket dedup by stable keys with occurrence counts;
- runtime receiver compatibility producer for
  `compat.stream_receiver_policy_missing` and
  `compat.stream_receiver_not_declared`;
- Pending Action creation and response handling for preview, postpone, open
  Builder, autonomous repair, and refuse;
- Builder repair task handoff with Dev Ticket and Development Signal source
  refs;
- stable `owner_area` and `component_ref` classification for project, skill,
  scenario, modal/component, Builder, SDK/API, and core tickets;
- ticket relationships for core blockers, related tickets, duplicate tickets,
  and caused-by links;
- evidence-gated `resolve`, non-terminal `resolved`, evidence-gated
  `verify`, verified-only normal `close`, and `reopen`;
- `adaos dev ticket` CLI for Codex and developer workflows;
- client Dev Ticket list/detail/actions, screenshot evidence preview, and
  stage/component filters;
- client invocation-scoped filtering from scenario header, modal/panel, and
  Builder context, plus summary edit and ticket comments;
- Builder workbench ticket selection with source options, deterministic intake
  qualification, active related-ticket batch context, and core-blocker guard;
- Core Dev Ticket creation for `core_capability_request` and SDK
  Understanding Signal creation for ambiguous or failed SDK/API use.

Remaining target pieces include:

- artifact-local signal projection;
- declarative Resource Workbench rendering for Dev Tickets instead of only the
  custom client surface;
- Feedback Skill UI/voice intake with screenshot capture;
- conversational disambiguation before feedback/Teacher/Builder routing;
- NLU Teacher and Feedback Signal refs in both directions;
- full agent-grade ticket workflow: artifact-open commands, relevance ranking,
  subscriptions, SDK/MCP helper surface, access-decision traces, duplicate and
  related UI, autonomous cost estimates, delayed completion notifications, and
  batch repair execution;
- SDK Understanding Signal routing to docs/examples, evals, SDK/API work, and
  core capability requests;
- Core Dev Ticket lifecycle events and capability-available fanout to affected
  Builders, subnets, Pending Actions, and users;
- broader compatibility producers for validation, route pressure, projection
  rule misses, and invalid data-route contracts;
- optional redacted GitHub issue draft/link/export integration;
- stale revalidation and not-design-time-fixable closure automation.
