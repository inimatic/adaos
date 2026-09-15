# Builder Automation Skill

Status: local SDK-backed runtime slice with chat follow-ups, atomic
finalization, and Forge checkpoint gating. Current qualification is tracked in
[BIP-26 through BIP-28](builder-intent-to-prototype-roadmap.md#bip-26);
worker completion is not independent application or delivery acceptance.

`builder_automation_skill` is the system adapter between the Builder product
surface and the implementation executor. It owns the Automation-stage tool
contract; it does not contain the Codex runner, duplicate Skill Factory state,
or edit project files directly.

## Boundary

The ownership split is:

- Prompt IDE / Builder selects a skill or scenario and produces an approved
  implementation brief.
- `builder_automation_skill` exposes `start`, `chat`, and `get_state` to the UI.
- `BuilderAutomationService` persists the session and normalizes each turn into
  an `adaos.builder.realize_request.v1` task.
- `LocalSkillFactoryWorker` executes the bounded local Codex iteration, validates
  generated artifacts, and reports the task result.
- the normal skill/scenario lifecycle remains responsible for staging,
  activation, publication, rollback, and destructive actions.

The skill lives in `.adaos/workspace/skills`, because it is part of the AdaOS
platform runtime. It is not a user-editable Builder template and must not be
resolved from a project dev space.

## Tool Contract

`start` begins implementation from an explicit `object_type`, `object_id`, and
`implementation_brief`. Repeated starts while the same project is active are
idempotent and return the existing session.

`chat` submits one follow-up implementation instruction. The first revision
serializes iterations per project: while a task is active, the skill reports
`automation_busy` instead of creating an ambiguous concurrent branch.

`get_state` returns the compact
`adaos.builder.automation_projection.v1` projection. The projection contains
only render-safe lifecycle data: selected project, current iteration and task,
busy/terminal/input flags, summary/error, and the stable steps `queued`,
`workspace`, `implementation`, `verification`, and `result`.

Session files and full Skill Factory task evidence remain service state and are
not copied into the Web UI document.

## Stage-Bound Context

The first Automation packet is prepared before the successful start transition.
Its requested execution phase must therefore be explicit, not inferred from the
still-active Prototype presentation. Retain the observed phase separately; context
construction grants no execution permission and does not advance the workflow.
The existing start gate still requires the exact accepted Prototype and Change.

Automation uses implemented resources. The earlier mock binding is retained only
as Prototype provenance, not as an Automation data policy. Every Automation packet
and its model-facing projection carry the bounded SQLite migration/configuration
contract, without installation records, setting values or secret bindings. Audit
the retained model prompt as well as the packet; a correct provider answer cannot
qualify a run whose input prescribed the wrong stage.

## Model Selection And Accounting

Prototype and Automation have separate model preferences. Codex selection is
an application-owned `builder_codex_profile`, not the Prototype's
`builder_llm_model` or a browser-only choice. The public
`builder.model_settings` facade intersects Root's `automation` catalog with
the executing Codex identity's public `model/list` response. Root controls
operator permission; the local catalogue controls executor availability and
supported reasoning efforts. Discovery starts no model turn, reads no account
credentials into application state, and is bounded separately from generation.
An unavailable Root or local catalogue must not create a fabricated fallback.
Admission refreshes local availability before starting a selected model. A
later provider rejection remains an execution failure, not permission to switch
models silently.

The Automation SDK passes the stored profile unless the caller explicitly
supplies an execution profile. The admitted session/task retains that profile;
later settings changes cannot rewrite past runs. The worker separately records
the explicit CLI model/reasoning configuration without credential-bearing
arguments. Usage reports use this execution evidence; missing historical model
identity remains unresolved rather than inferred from today's preference.
An explicit follow-up may adopt a newly selected profile only after the current
iteration terminates. Its predecessor profile, task and iteration remain in
bounded session history; immutable task inputs remain the full execution record.

Root retains per-model fresh input, cached input and output tokens. Subscription
token quotas and provider monetary cost are separate quantities. A monetary
estimate uses only a Root-managed versioned tariff, never a caller-supplied
price. Missing model/tariff means unpriced, not zero cost; deterministic work
has a separate zero-model receipt. Reasoning tokens are included in provider
output and must not be charged twice. UI, live Root configuration and end-to-end
accounting qualification remain in the BIP-02 replacement gate.

Cost rollups aggregate the tariff receipt retained with each event, not today's
price. A mixed priced/unpriced window exposes a known subtotal and coverage, with
the total estimate left unknown. API USD estimates are not ChatGPT subscription
credits or an invoice. Operator tariffs must specify their applicability (model,
service tier and context range); unsupported pricing tiers remain unpriced.
The current bounded Root event log is operational metering, not a financial ledger;
atomic event deduplication/counter reconciliation and long-term receipt retention
must be qualified before using it for invoice settlement.

## Delivery And Recovery

The worker and checkpoint use the same complete skill-manifest schema, including
normalization and nested read-policy rules. Syntax, tool-link and targeted data
route checks are additional checks, not substitutes for that schema. Structural
failures belong in the bounded local repair report before source application or
any checkpoint upload; deterministic checks must not first appear at publication.

Every persisted session update emits `builder.automation.changed`. Builder UI
can update from that event and call `get_state` for first paint or recovery.
The persisted session and Skill Factory task are authoritative if an event is
missed. The local worker mirrors each bounded progress transition into the
Automation session, so `workspace`, `implementation`, `verification`, and
`result` can advance without UI polling. Unchanged state reads do not emit a
new event.

Ordinary UI chat is always dispatched to `builder_skill.chat`. That skill owns
the selected Builder project/thread, checks its workflow state, and uses
`adaos.sdk.builder.automation` for `get_state` / `submit`. The HTTP tool
transport has no Builder-specific service interception and therefore cannot
route a message using an unrelated stale Automation session.

Worker completion is not the terminal Automation state. The session remains
`commit_ready` while it:

1. records the validated source snapshot and prepares/activates the DEV skills;
2. runs any admitted consumer-owned acceptance checks;
3. checkpoints the target and every project-owned changed component in Forge,
   verifying exact source/task metadata and the owning composition;
4. rematerializes the paired DEV scenario.

Only then does it become `completed` as an implementation/finalization result.
This does not certify user outcomes, installed authorization or Trial/release
acceptance. The frozen Prototype, declared implementation scope and independent
HTTP/browser evidence remain separate gates. An unconfirmed checkpoint becomes a
terminal `forge_checkpoint` failure and cannot certify delivery, even if an
isolated DEV activation already succeeded. A follow-up turn moves
the preceding readiness into bounded history and clears summary, failure,
task, progress and finalization start time so navigation/reconnect cannot
resurrect the old terminal projection or inflate the new attempt's duration.

Network request timeouts, asynchronous observation windows and execution/cost
budgets have distinct ownership. Increase an observation/execution budget only
against retained progress, transport diagnostics and complete input/output
evidence. Do not extend connection timeouts to hide an unavailable endpoint,
unsupported model, deterministic validation failure or duplicated context.
An observer expiring does not authorize another paid model submission.

The editable component set is read from the owning `adaos.project.v1`
manifest. Runtime skill requirements and retained publications do not expand
that set, and Automation/worker contracts use `companion_skill_ids` without a
singular compatibility alias.

## First-Revision Limits

- execution is the bounded local worker; remote isolated dev-node dispatch is
  the compatible target, not part of this slice;
- one implementation task may be active for a project at a time;
- cancellation, retry policy controls, diff review, staging, and activation are
  intentionally not invented in the skill before their core contracts exist;
- the projection is a backend contract; the final Builder Automation screen
  may compose it with chat, artifacts, tests, and dev-preview status.
- durable scenario checkpoint acknowledgement is mandatory after any
  transient error. Verify exact commit/task/source metadata, not changed
  archive bytes. The [SDK checklist](builder-sdk-boundary.md#roadmap-and-checklist)
  owns fault/retry qualification; an unconfirmed commit fails finalization.

Primary references:

- [Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Skill Factory and Isolated Dev Nodes](skill-factory.md)

