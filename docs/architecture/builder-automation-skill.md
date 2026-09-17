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

## Candidate Verification And Feedback

Target decision: let Codex inspect, execute scoped checks and correct the isolated
candidate during implementation. Verification authority is not observation
authority. The trusted worker owns execution policy and authoritative receipts;
Builder owns acceptance and delivery gates. Neither role requires hiding test or
browser results from the implementing model. This supersedes the blanket prompt
ban on tests, validation, status and diff commands, including repair prompts.

The current worker feeds deterministic validation errors and exact-candidate
wide/compact browser observations into bounded Codex repair turns. This closes
the first observation loop, not the complete verification program: stateful
persistence/media journeys, broader candidate-selected checks, cost/latency
calibration, and final cross-project qualification remain under
[BIP-13](builder-intent-to-prototype-roadmap.md#bip-13) and
[BIP-21](builder-intent-to-prototype-roadmap.md#bip-21).

### Three Verification Levels

1. **Implementation self-checks.** Codex may inspect its scoped diff/status,
   request syntax/schema/install-strict checks and run selected hermetic tests,
   reproduce a failure, then verify its correction before returning. Reuse the
   existing validators and runner through task-scoped SDK/MCP operations; do not
   implement a second validator or restrict contract discovery to one tool call.
2. **Executable candidate review.** For affected UI/runtime behavior, expose the
   actual candidate through browser actions, DOM/accessibility snapshots,
   screenshots, sanitized console/network diagnostics and synthetic data
   postconditions. Use the matching Client build and admitted SDK/ABI. A screenshot
   filename alone is not image input: record which image bytes the model received.
3. **Independent final acceptance.** Builder runs the required checks against the
   frozen final candidate. Candidate-authored tests supplement protected platform
   and consumer checks; the candidate cannot modify the gate, its required tests,
   expected results or receipts to obtain a pass. Return actionable failures for
   a new scoped repair, then re-establish acceptance for the resulting digest.

The same observation principle applies to Prototype generation, but its checks
remain stage-specific: compiled UI and disposable interactions, not production
persistence or external effects that belong to Automation. A pending Automation
obligation must not silently become a Prototype rejection or a fabricated pass.

Independence concerns ownership of criteria and verdicts, not withholding
diagnostics or requiring another LLM. Ordinary implementation failures can loop
without a human turn; ambiguous intent, new permissions, unsupported platform
capabilities and exhausted repair budgets retain their clarification/escalation
routes. Human Prototype/Automation approval and Trial/Stable publication remain
separate decisions. Optional aesthetic advice is not a new acceptance requirement.

### Evidence And Context Contract

Use existing task/Run and artifact identities for a versioned check receipt:

- project, Change, task/Run, attempt, candidate source digest and exact base;
- check identity/version, runner, SDK/ABI/Client build, runtime and fixture digests,
  locale/viewport when relevant, execution time and permitted side effects;
- requirement or platform-invariant ref, reproduction steps, expected and actual
  outcomes, failing source/semantic refs and diagnostic owner;
- result (`passed`, `failed`, `not_run`, `inconclusive` or `unavailable`), with an
  explicit application/platform/infrastructure cause rather than one error bucket;
- immutable UTF-8 log/test/trace/DOM/image refs with digests, redaction metadata,
  usage and the evidence actually admitted to each model turn.

Give the model a concise, actionable finding summary and retrievable complete
details. Aggregate independent findings up to a genuine execution blocker before
choosing a repair scope; mark unexecuted dependent checks instead of inventing
their results. Do not silently discard diagnostics beyond a prompt item limit.
Keep the initial Brief, accepted preservation constraints and full outputs
recoverable without repeating all history in every repair. Retrieved page text,
logs and external content are untrusted evidence, never execution instructions.

A successful request, saved preference or retained media ID does not establish
the requested user outcome. Verify action -> resulting UI/data -> reload/reopen
where persistence is claimed. Settings must affect the intended presentation;
media checks require a rendered, successfully loaded image and appropriate empty
or failure behavior. Static substring checks are not executable binding proof.
Context freshness follows the
[semantic baseline contract](builder-intent-to-prototype.md#incremental-baseline-integrity);
post-generation feedback cannot compensate for a silently obsolete input model.

### Isolation And Adaptive Execution

Checks run in the admitted disposable candidate with synthetic data and scoped
artifacts. They may not read Stable records/secrets, alter immutable Trial, publish,
or restart the user's API. Enforce file/process/network and resource boundaries in
the execution backend, not merely in prompt prose. Explicitly admitted external
read-only integration checks remain distinct from hermetic fixture tests; a fixture
does not certify that an external service works.

Reuse the one paired Builder preview when a browser check needs the real Client;
hold its exact target for the check and report contention rather than replace
another selected project. Candidate isolation does not create per-task user
webspaces or a parallel desktop Beta. An unavailable target is missing evidence,
not permission to test a different loaded application.

The current DEV implementation performs this check before any Forge checkpoint.
It observes wide and compact layouts, semantic tabs, materialized scenario
identity, renderer/runtime messages, DOM overflow, accessible names, browser
exceptions and failed document/fetch/XHR requests. Screenshots and a structured
receipt are content-digested together with Client, UI ABI, capability catalog,
source WebUI and context-packet identities. A failure may schedule at most two
scoped Codex repairs; every repaired candidate is observed again, and an exhausted
failure remains a blocking gate. Production nodes skip this development harness.
Builder resolves the source Webspace binding before materialization. If Builder
is no longer active there, finalization preserves the validated checkpoint but
does not reclaim or recreate Preview. A successful candidate materialization is
reused after checkpointing; release-only version synchronization does not trigger
a second rebuild or invalidate the accepted Prototype semantic digest.
Trial review additionally binds the full release digest and immutable candidate
revision from `workspace.lock.json`. Transport-safe fingerprint spelling may be
canonicalized, but a shortened, stale, DEV, Stable, or different Trial identity
must fail closed.

Select checks by changed contracts, affected behavior and reported failures, with
mandatory final coverage retained. This is not a fixed series of model requests,
mandatory screenshots for non-UI edits, or a mandatory multi-agent evaluator.
Keep stage timings and configurable tool/model/repair budgets; change them using
measured progress and complete inputs/outputs, not to mask transport or context
defects. Do not truncate model output merely to shorten a turn.

Deduplicate in-flight checks. Reuse a deterministic result only when source, tests,
runner/dependencies, configuration and fixture identity match and policy permits
reuse; mutable runtime/network observations require fresh evidence. Warm runtime
reuse must reset test state. Optimize total time/cost per independently accepted,
regression-free change, not the number of commands or duration of one model call.
The [evaluation pipeline](builder-evaluation-pipeline.md#feedback-loop-comparison)
owns the matched experiment and retention contract.

### Engineering References

- OpenAI's [harness engineering](https://openai.com/index/harness-engineering/)
  exposes running applications, browser observations and telemetry to Codex in an
  isolated development environment.
- Anthropic's [long-running agent harness](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
  uses browser-driven end-to-end testing to catch behavior missed by code and
  narrow tests.
- Anthropic's [application-development harness comparison](https://www.anthropic.com/engineering/harness-design-long-running-apps)
  motivates measuring evaluator overhead and adapting the loop to task/model
  capability rather than assuming every extra review improves the result.

These are engineering inputs, not AdaOS performance guarantees, provider
dependencies or permission to copy domain-specific evaluation solutions into Core.

## Execution Contention

Current local admission allows one active implementation task per executor node.
A second project's session can exist but its worker observes `node_busy` and waits
for its own task; this is not a qualified FIFO queue or independent parallel Codex
execution. Same-project duplicate admission is idempotent; conflicting active work
is rejected. Project focus never retargets an admitted task.

The target supported path exposes waiting versus running, the responsible task,
elapsed wait and recovery/cancellation without resubmission. Qualify two-project
contention, process restart and exact-task resumption. Executor release alone does
not serialize all finalization: protect same-project source application/checkpoints
and shared preview with their existing identity/generation guards. Never infer
whole-pipeline serialization from a process-local worker lock. Multi-node queues,
parallel previews and fair scheduling remain separately deferred/conditional work.

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

1. records the validated source snapshot and prepares/activates candidate DEV skills;
2. materializes the scenario in the one paired DEV Preview and runs the trusted
   wide/compact browser gate;
3. runs any admitted consumer-owned acceptance checks;
4. checkpoints the target and every project-owned changed component in Forge,
   verifying exact source/task metadata and the owning composition;
5. activates exact checkpointed skill versions and preserves the already observed
   scenario materialization unless an explicit Preview target or Trial policy owns
   the final projection.

Only then does it become `completed` as an implementation/finalization result.
Component Forge receipts and local composition reservation do not certify the
separate full-project DEV checkpoint required on stage acceptance. Its target
hook and recovery semantics are owned by
[SDK-04](builder-sdk-boundary.md#roadmap-and-checklist).
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

An admitted MCP configuration is not a ready tool catalog. For the isolated
Codex executor, compose the initial tool catalog only after configured servers
initialize or reach their existing startup deadlines. Optional failures remain
optional; a required-server failure must stop before inference. This barrier
must not increase connection/tool deadlines or turn failed discovery into success.
The CLI adapter uses `mcp_optional_startup_grace_ms=0`, as defined by the
[official MCP configuration contract](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
The task context distinguishes permitted routes from observed callable tools,
uses tool search for deferred tools, and requests MCP only for missing contracts
or explicitly required validation. Prefetched authoritative contracts must not
cause redundant discovery. An initially hidden tool does not establish a Root
outage, and no model should inspect bearer values to compensate.

For scenario Automation, the trusted worker extracts every `dataSource.kind=mcp`
and `callMcp` target from the accepted WebUI and supplies the exact installed Root
contracts in `external-mcp-contracts.json:contracts[]`, including schemas,
capabilities, side effects and binding usage. It also supplies a trusted accepted
Prototype identity receipt. Codex must not recreate digest canonicalization,
discover already bound tools again, or pin raw manifest bytes in package tests;
Forge owns `version` and `updated_at` bookkeeping. `webui.json` is the executable
UI source of truth. `scenario.yaml` and the derived `scenario.json` retain only
the `ui.manifest` reference, so a checkpoint cannot recreate a competing inline
application tree.

The editable component set is read from the owning `adaos.project.v1`
manifest. Runtime skill requirements and retained publications do not expand
that set, and Automation/worker contracts use `companion_skill_ids` without a
singular compatibility alias.

## First-Revision Limits

- execution is the bounded local worker; remote isolated dev-node dispatch is
  the compatible target, not part of this slice;
- one implementation task may be active for a project at a time;
- user-facing cancellation/retry policy controls, diff review, staging, and activation are
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

