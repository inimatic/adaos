# Builder Governed Workflow Verification — 2026-08-01

Status: local implementation and live-channel evidence journal. This record
does not close roadmap items whose mutating, distributed, or human visual gates
remain unproven.

## Accepted local baseline

- Core checkout: branch `rev2026`, implementation commits through `4ec78aa5`
  in this slice.
- DEV Builder skill: `0.3.34`, Forge commit
  `83ccedda048f1afccb6fca9cea36b379ff801a37`.
- Workspace Builder skill: `0.3.28`, Forge commit
  `a5ddf74937cd027f34f26f57c9cd6eb2a0975e1c`.
- `handlers/main.py`, `workflow.json`, and `tests/test_builder_skill.py` are
  byte-identical between DEV and Workspace; independent version counters are
  expected.
- Both DEV and Workspace runtimes were explicitly activated after publication.
- Nested backend `80c5a15` preserves validated Telegram inline keyboards and
  normalizes text/callback relay paths into one canonical exactly-once input.
  Deployment and the final human callback check are recorded separately from
  local contract proof.

## Contract audit

| Boundary | Implemented evidence | Remaining boundary |
| --- | --- | --- |
| Issue graph | Typed reference-only `duplicate`, `depends`, `blocks`, and `related` edges with ownership/cycle validation | Rich split/merge/regroup workbench |
| Change graph | Typed Issue membership plus `alternative`, `supersedes`, and `depends` edges | Multi-user proposal/extraction policy |
| Workflow statechart | One manifest-bound `workflow.json`, compiler, resolver, explanation, and exact command admission | Legacy-reader retirement and immutable cutover proof |
| Artifact lineage DAG | Typed `derived-from`, `candidate-of`, and `published-as` refs | Complete release-lock graph inspection UI |
| Component dependency graph | Scenario/skill/runtime component refs and indirect conflict detection | Complete adapter-contract trust registry |
| Execution graph | Run/attempt/child/retry/recovery refs; terminal result is independent from delivery | Distributed activity admission for every mutating control |
| Conversation/interaction graph | Message/thread/Interaction/Response/ReplyRoute refs and exact action-token ingress | Durable zone-to-hub Telegram inbox |
| Release/deployment graph | Source/package/candidate/channel/WorkspaceLock refs | Atomic code/definition/binding activation proof |

Authority and view/context are cross-cutting reference planes, not substitutes
for any of the eight graphs above. Mutable state is not copied between planes.

The normative `adaos.workflow.transition.v1` descriptor is normalized and
schema-validated as one object. It covers identity/version, source selector,
target, trigger and typed input, actor/authority and context resolution,
guards/reasons/invariants, risk/side-effect and concurrency/generation,
idempotency, effect/transaction, all outcome mappings, timeout/retry/cancel,
compensation/reconciliation, approvals/evidence, emitted events/outbox,
ReplyRoute, capability requirements, explanations, audit/metrics/trace, and
definition migration. Executors may not supply silent semantic defaults.

`adaos.builder.project.v1` is the coordination aggregate: stable identity/type,
source/stable/installed/DEV refs, accepted Prototype/Implementation refs,
Change portfolio and conflicts/dependencies, policy and component boundary,
candidates/Trials, workflow versions, archive state, scoped focus, and an
aggregate explanation without inventing one global project stage.

## Conversation, attention, and asynchronous result proof

- `ChannelCapabilityProfile`, `InteractionRequirements`, and
  `InteractionPresentationPlan` are separate contracts. Capability does not
  grant permission and does not make a blocked business command available.
- Negotiation preserves required commands, risk, confirmation, and target. It
  selects native buttons, numbered text, pagination/deep link, or unsupported;
  required semantics are never silently dropped.
- The attention policy classifies append/update/evidence/projection behavior,
  coalesces progress, retains quiet-hours/channel preferences, and escalates
  `input_required`, failure, and expiry.
- The asynchronous protocol separates accepted, started, progress,
  input-required, resumed, terminal outcome, response materialization,
  delivery attempts, and acknowledgement. A terminal business result is
  written once; redelivery cannot repeat LLM, Codex, Trial, Publication, or a
  tool effect.
- A live Telegram request reached the addressed DEV Builder, returned the same
  five semantic actions as Web, preserved Russian UTF-8, and the user confirmed
  that the real Telegram client rendered the buttons. A mutating callback and
  durable backend DeliveryAttempt receipt remain explicit gates.
- The node conversation ledger retained 3,253 messages with contiguous
  sequence 1..3253 after the Interaction checks. The active
  `test04_recipes` thread retained its own 31-message history. A UI switch from
  an unscoped/other-project thread to the selected Project can therefore change
  the visible slice, but no durable history rows were deleted.

## Builder routing and Preview proof

The following read commands are deterministic and bypass Automation/LLM:

- `Строитель, что выбрано?`;
- `Строитель, покажи проекты` and `Строитель, выбери <id>`;
- `Строитель, помощь`;
- `Строитель, ссылка на Preview`;
- `Показать процесс`;
- `Показать прототип`;
- `Показать реализацию`;
- `Показать публикацию`.

An exact label of a live Interaction action is resolved before Builder/
Automation/NLU routing. It is accepted only against the latest live
presentation and opaque action token; fuzzy text does not grant authority.

Workflow terminology is disambiguated by target. A request to change
`workflow.json`, statechart, guards, invariants, activities, or a
TransitionDescriptor is Automation/Codex work. A request to rearrange the
visible Process panel is Prototype work. `Показать процесс` is inspection.

Exact local Preview materialization produced:

- `proto: builder · UI 058`;
- `active: builder · 0.2.54`;
- `public: builder · 0.2.55`.

The test workspace was then restored to
`proto: test04_recipes · UI 003`. Preview selection did not perform a business
workflow transition.

The limited-channel project list no longer calls two sessions «active». It
projects one `current in this conversation` Project and labels all others as
`available in DEV`; each bounded row has an exact `builder.project.select`
action. Selection changes only the originating conversation focus. It does not
implicitly move a Preview opened by a different Webspace or conversation.

`Строитель, ссылка на Preview` returned the exact local target
`proto: test04_recipes · UI 003` and an `openUrl` action for
`https://inimatic.com/?webspace=dev1-dev`. Telegram presentation preserves the
URL action rather than encoding it as a callback payload.

The stale task `task.01KYXSAT6NKN5Y5M6161YW7MJP`, created when the old path
mistook `Показать процесс` for an Automation request, was cancelled through the
task API with reason `misrouted_read_command`; no state file was edited.
Structured callback ingress now takes priority over callback message text and
uses one `tg:<bot>:<update>` deduplication key across local publish, relay, and
retry. Unknown/expired structured actions and legacy raw `ia:` tokens fail
closed before Builder, Automation, NLU, or an LLM.

The same exact selection was repeated through the one-shot CLI after hardening
the Yjs ownership boundary. UI-navigation tools stay on their caller thread,
nested YMap/YArray values are detached before CPU work, and one-shot
materialization does not enter the persistent executor. The command exited
cleanly without the former `YDoc ... dropped on another thread` diagnostic;
ordinary non-UI tools retain their bounded executor timeout.

## Model and Codex context audit

Prototype LLM receives the complete current `webui.json`, project memory,
revision delta, runtime component contracts, a mechanically generated bounded
WebUI ABI summary, and the governed context packet. The full ABI remains the
post-response validator. The compact ABI intentionally removes descriptions
and limits nested expansion, so it is useful but not semantically complete;
the long hand-written system prompt currently compensates for common patterns.
This remains a maintainability risk and is tracked by GWR1-27/GWR4-24.

Before `5c20e5fe`, Automation gave Codex the full project sparse checkout but
lost the governed context packet when constructing `task.md`. It therefore had
source files without a reliable, explicit projection of Issue scope,
acceptance, semantic refs, base, and coverage. The worker now:

1. retains the exact packet and digest in `packet.json`;
2. emits a bounded deterministic Issue/acceptance/facet projection into
   `task.md`;
3. marks conversation/review text as untrusted evidence rather than authority;
4. tells Codex that manifest-bound `workflow.json` is the sole definition
   authority and that no parallel Python/UI transition table is allowed;
5. compiles every referenced definition before accepting a result.

This makes incomplete context observable and validation deterministic without
copying unrestricted chat history into an isolated executor.

## UTF-8 audit

Current Builder manifests declare only `en` and `ru`; DEV and Workspace source
contain no replacement code point or unexplained four-question-mark run. Test
fixtures intentionally contain such runs to prove fail-closed rejection.

Historical `test03_recipes`/Skill Factory evidence does contain actual lossy
question-mark runs and an older mojibake UI revision. This is stored corruption,
not a console rendering illusion. The original code points cannot be inferred
safely, so the records were not rewritten. New Builder ingress, WebUI output,
Automation brief, and worker source paths reject lossy text before model
submission or authoritative writes. Non-ASCII diagnostic injection must use a
UTF-8 JSON file or ASCII JSON with `\uXXXX`, never a PowerShell text pipeline.

## Executed checks

- DEV Builder skill: 159/159 tests.
- Telegram backend callback/relay contract: 13/13 focused tests.
- Core dialog action-ingress contract: 7/7 focused tests.
- DEV Builder scenario: 14/14 tests; strict skill and scenario validation pass.
- Context/worker/Automation group: 105/105 tests.
- Governed coordination contracts: 44/44 focused tests.
- Conversation store/interaction/dialog history group: 37/37 tests.
- DEV/Workspace handler, workflow, and test SHA-256 parity: exact.
- Live Web semantic interaction: current Project plus contextual actions.
- Live Telegram: text ingress, Builder reply, UTF-8, and human-visible inline
  controls.
- Live Preview: exact Prototype, Automation, Publication, and restoration.
- One-shot Preview ownership regression: caller-thread UI navigation, detached
  resolver inputs, owner-thread one-shot materialization, and a clean real CLI
  process exit.
- Materialization/DEV tool regression: 63/63 checks plus the complete 113/113
  `test_webspace_phase2.py` suite. The run also exposed and closed an unrelated
  stale inventory-name override that hid explicit remote-node labels.

## Open risks and next gates

- GWR1-19 and GWR1-21..27: self-contained authoring ABI, admitted artifact
  records, adapter trust, verified role claims, authoring diagnostics,
  package/binding lock, atomic activation, and full LLM authoring packet.
- GWR2-18: executor readiness must move into the shared resolver before all
  mutating actions can be shown consistently.
- GWR4-21..24: retire legacy readers, shadow migration, immutable cutover, and
  complete workflow authoring/Specification/publication evidence.
- GWR5 mutating cross-channel, registry, package, migration, and authoring
  convergence proofs.
- GWR6-16: durable per-hub Telegram ingress receipt/inbox.
- Human wide/compact browser comparison and one mutating Telegram project
  selection callback after backend deployment.
