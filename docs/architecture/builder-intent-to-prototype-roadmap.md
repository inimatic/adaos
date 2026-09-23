# Builder Intent-to-Prototype Roadmap

Status: active corrective roadmap. Small generic Prototype and DEV Automation
slices work; target ownership, repeatable reliability and installed lifecycle
acceptance remain incomplete.

Last reviewed: 2026-09-19.

Architecture: [Intent-to-Prototype](builder-intent-to-prototype.md).
Cross-document owner: [Builder Roadmap](builder-roadmap.md).
Evaluation: [Builder E2E Evaluation Pipeline](builder-evaluation-pipeline.md).
Client owner: [Client Component System Roadmap](client-component-system-roadmap.md).
Measurements and audit increments: [Builder Engineering Journal](builder-engineering-journal.md).

## Closure Rules

This is the single corrective task register, not an experiment diary. Each
BIP identifier is stable. Code/artifact details establish only the stated
implementation boundary; new findings update the owning task rather than add
another dated checklist. Exact measurements belong in the journal and raw
evidence bundles, not parallel review reports.

- `verified`: the narrow exit is evidenced; do not infer broader readiness.
- `partial`: a mechanism exists, but work or acceptance remains.
- `blocked`: a named prerequisite or explicit user pause prevents acceptance.
- `conditional`: a could requires a measured trigger/benefit and explicit decision.
- `deferred`: outside the agreed small-application correction.

`must` gates autonomy/delivery claims; `should` is required for the maintainable
supported path; `could` remains part of non-deferred review but is not an
unconditional feature commitment. A blocked task is not deferred. A task is
not done because a schema exists, an import guard passes, or one model/browser
run succeeds. Historical missing evidence cannot be recreated retrospectively.

This register contains 38 non-deferred packages (27 must, four should, seven
could) and eight deferred packages. Three narrow packages are verified.
These are ownership/acceptance units, not effort estimates or a percentage.
The parent roadmap and SDK migration retain their distinct obligations.

## Implementation Boundary

| Surface | What exists | What is not established |
| --- | --- | --- |
| Generic boundary | Explicit compatibility packs, no implicit subject selection, input-attribution receipts and boundary tests | Exhaustive dynamic reachability and a clean sealed baseline |
| Public SDK | Request/status, semantic compilation, review, preview and Automation APIs | Prototype execution authority fully moved out of the skill |
| Intent | Exact source/provenance, deterministic extraction and Brief merge | Residual interpretation, material clarification and accepted assumptions |
| Semantic Prototype | Typed multi-resource candidates, relations, state proof, requirement bindings, deterministic WebUI/locale/fixture compilation | Total admitted-component coverage, general incremental preservation and reliable unseen tasks |
| Local UI substrate | CRUD, live choices, filtering, editor surfaces, sections, board/tree/chart/accordion/settings and display policies in qualified subsets | Uniform action cancellation/error semantics and complete compact/a11y/i18n acceptance |
| Preview | Explicit production/DEV self-host topology and one paired preview | All deletion/reconnect/idempotency and readiness paths qualified |
| Automation | Frozen-Prototype implementation, deterministic repair feedback, exact-candidate wide/compact browser observations, independent local HTTP/browser plans, owned persistence and bounded attempt archives | Broader candidate-selected checks, stateful persistence/media journeys, cross-stage baseline integrity, full-project acceptance checkpoints, repeated latency qualification and installed delegated roles |
| Evaluation | Public CLI, immutable reports and model-input archives, component and browser probes | All stand paths under one lifecycle runner, calibrated holdout and complete cost accounting |

## Current Sequence

1. Stabilize loaded runtime, Client action semantics and requirement provenance
   on retained applications: BIP-03, BIP-10, BIP-16, BIP-27.
   First qualify semantic/implementation baseline integrity (BIP-11), scoped
   self-checks and executable feedback (BIP-13/BIP-21), with independent final
   gates and matched evaluation (BIP-05/BIP-06/BIP-27). Full DEV acceptance
   checkpoints remain the must-level SDK-04 gate; BIP-26 covers node contention.
2. Meet clean-baseline prerequisites and measure before further tuning:
   BIP-01, BIP-04 through BIP-06; Client C0/C1 plus truthful C2 subset.
3. Complete ownership, Brief, component, semantic and context contracts:
   BIP-07 through BIP-18, with related should work.
4. Qualify repeated tasks and bounded review, then compare/cut over and create
   Applications from a generic scaffold: BIP-17, BIP-21, BIP-29.
5. Resume installed delivery only after an explicit decision: BIP-28.
6. Evaluate conditional could work against measured benefit.

The user accepted DEV Builder design 071 on 2026-09-14 and authorized its
Automation and a complete isolated TEST application journey through the new
Builder. This supersedes the earlier Trial/publication pause for that test
journey only; it does not authorize replacing operational Workspace Builder
before parity qualification or publishing unrelated applications.
The accepted frozen design 071 is evidence, not the Workspace release source.
As of 2026-09-17 the DEV and Workspace Builder scenario manifests are
semantically equal while the DEV control skill is newer. A preview with a poor
071 data projection must not replace the richer operational Workspace
projection. Promotion requires a source, control-skill, live-data and browser
parity receipt against the operational Workspace surface; `dev1-dev` is not a
release authority.
Do not regenerate accepted prototypes or manually repair generated application
code. Use the existing DEV Builder and its single owned preview. Do not feed
rubrics, held-out cases or finished domain solutions into generation. Preserve
legacy rollback until parity evidence permits removal. Human acceptance cannot
be self-certified by the executor.

## Current Task Register

### BIP-01

- [ ] `[must]` **Forensic And Boundary Evidence**. Status: `partial`.

Owner/dependencies: Core/Client + evaluation.

Implementation boundary: The frozen legacy manifest, explicit domain packs, input attribution and generic boundary tests exist. The snapshot is deliberately bounded, not an exhaustive dynamic reachability or full-stage trace.

Exit/remaining work: Complete the reachable-path/ownership inventory and immutable Client build/viewport evidence. Record missing historical bytes as unavailable; never regenerate old evidence under its original identity.

### BIP-02

- [ ] `[must]` **Truthful Status And User Projection**. Status: `partial`.

Owner/dependencies: Builder workflow + Client.

- [x] `[must]` Qualify revision-scoped flat Process stages, separate current-work
  emphasis from inspected selection, and disable unavailable stages without
  replacing server admission. Automation requires an accepted UI Prototype.
- [x] `[should]` Expose the same six real stages in a compact Process dropdown;
  keep current-step emphasis independent of the inspected selection. Generic
  Client menu options support `current`, `disabled` and localized metadata.
  Desktop/mobile browser rendering is verified; this is not a simulated state
  switch or completion of clarification/continuation integration.
- [ ] `[must]` Qualify exact aggregate/scenario/revision Preview identity through
  the live Open Preview command and Result field, including deleted targets,
  follow-active updates and rejection of a replaced Automation snapshot.
  Live retained-Automation Open Preview/Result passes in both desktop/mobile
  layouts; unit tests cover pinned recreation, follow-active primary scenario,
  failed/superseded materialization and replaced snapshots. Interactive deletion
  and transition/recovery qualification remain open.
- [x] `[must]` Qualify scenario-header Changelog and exact local Candidate
  acceptance into Workspace without a Dev Ticket. Keep publisher authority,
  immutable Trial source, subscription selection and public publication separate.
- [x] `[must]` Restore reviewed README generation through the generic content
  SDK; implementation and usage evidence are tracked in
    [CG-01--CG-05](content-generation-roadmap.md), not duplicated here.
- [x] `[must]` Keep the native Automation correction form reachable after an
  implementation checkpoint. Derive visibility from admitted retry/candidate
  invalidation commands, hide the first-start form when unavailable, and retain
  clarification blocking. The owned Reading List browser follow-up qualifies
  this path without changing workflow admission or accepting delivery.

Implementation boundary: Conversation and governed status projections exist; a worker's completed state is not independent Automation acceptance. A human-authored DEV Builder design specimen covers current work, separate decisions, history, context and existing Dev Tickets. Its local state transitions are not live workflow implementation or model-generation evidence.

The DEV candidate now binds the accepted design shell to canonical SDK state.
The legacy application picker (table, TEST/non-test, archive, search, sort,
refresh), creation/template flow, component file tree, Preview/QR and operational
review/start/delivery forms are reused. Capability parity is checked across page
and modal surfaces, not by restoring the obsolete layout. This is partial live
integration, not closure of the remaining interaction/continuation obligations.

The rich Workspace source was refreshed into DEV before the 2026-09-17
checkpoint so a poorer experimental DEV shell could not replace the accepted
Applications/Revisions/Settings surfaces. The exact candidate was promoted to
local Workspace as `builder@0.2.167`; its active scenario is `0.2.93`, control
skill `0.1.120`, and Builder skill `0.3.175`. The operational shell renders all
three populated surfaces without page errors. Explicit user cancellation is
projected as `cancelled`, not `Automation failed`; real provider, validation or
runtime failures retain their distinct failed states. This activation does not
prove recovery from every stale/terminal historical selection or close the
unchecked projection items below.

- [x] `[must]` Exercise retained TEST/non-test picker filters and native template
  creation in the browser; preserve the acknowledged Project/primary selection
  instead of clearing canonical state after the creation response arrives.
- [x] `[must]` Save/reopen distinct Prototype and Codex model preferences through
  the browser at the primary execution identity. Actual worker execution and
  usage accounting remain under the separate full-stage qualification gate.
- [x] `[must]` Align native empty-canvas generation with the semantic E2E route;
  retain explicit legacy compatibility. Protect scoped EN/RU requests from being
  misclassified as creation. The native TEST run produced revision 001, not an
  accepted Prototype: locale/required-field defects were identified in review.
- [ ] `[must]` Qualify asynchronous state refresh and failed-job recovery through
  the live screen without manual reload. Chat invalidation has generic unit
  coverage; the prior run needed rematerialization to update its header.
- [x] `[must]` Separate asynchronous wait exhaustion from provider failure;
  retain remote progress and transport-error counts, preserve the same Root job,
  and verify original semantic context before replay. Unit and retained-response
  validation cover this boundary, not end-to-end recovery acceptance.
- [ ] `[must]` Complete browser recovery/repair qualification without bypassing
  rejected semantic state proofs or confusing original usage with incremental
  repair cost. Required-field rejection needs an invalid-form interaction, not
  a deliberately invalid stored sample. Retained request inventory duplication
  and state/field classification still need context-quality review.
- [x] `[must]` Independently exercise the repaired native TEST Prototype on wide
  and compact viewports: separate create/edit/delete surfaces, required-field
  rejection, optional inputs, cancellation, search and filters. Generic Client
  fixes cover pre-creation focus capture and nullable choice serialization.
  This is one retained candidate, not repeated generation or Automation evidence.

- [x] `[must]` Retain a capability-based migration contract against Workspace
  Builder, including the operational picker and creation flow. Keep file editing
  deliberately read-only except README; do not claim old unrestricted editing
  parity. Contract/schema tests do not substitute for action-level browser tests.
- [x] `[must]` Rebind replaced Client chat data sources and clear previous-source
  messages; ignore emissions from the unsubscribed source. Preserve the draft
  on a descriptor refresh. Nineteen generic chat tests pass; per-project draft
  ownership and asynchronous history/source races remain separate obligations.

- [x] `[must]` Preserve the operational Workspace Builder before developing its
  DEV replacement; retain a fresh source baseline with honest provenance.
- [x] `[should]` Present a native declarative design specimen for human review,
  with distinct review, input-required, checking, partial, failed, offline and
  stopped examples; reuse Dev Tickets rather than another feedback mechanism.
- [x] `[should]` Extend the design with a stable process menu, revision header,
  actor/milestone cues, separate reference materials and a generated-file tree,
  batch clarification and explicit simulated Beta/Stable decisions. This is
  interaction design, not live input ingestion, delivery or acceptance proof.
- [ ] `[must]` Bind input intake to immutable stored references: observable
  upload/error/removal, role/scope and reviewed context inclusion; distinguish
  local selection from bytes stored and bytes actually admitted to a request.
- [ ] `[must]` Bind read-only file inspection to the selected artifact ref and
  content digest; carry file/revision scope into a new conversational change.
- [ ] `[must]` Persist clarification drafts per Project/Change/question set;
  reject stale continuation and resume only after required answers and explicit
  consent. Batch rendering alone does not implement durable continuations.
- [x] `[must]` Implement the Automation prerequisite on canonical conversation
  interactions: exact Project/Change/Run/accepted-digest binding, durable partial
  answers, verified owner, idempotent answers and one continuation task per
  response; keep platform blockers distinct from user decisions. Local worker
  integration proves question batch -> answers -> same-Change continuation,
  while browser interruption and real-model qualification remain open above.
- [x] `[must]` Enforce the Codex outcome before validation and qualify one real
  question -> native answer -> browser reload -> explicit continuation in the
  same Change. Local regressions cover first/repair-turn suspension, malformed
  outcomes and recovery refusal. Reading List `cycle-2/clarification-03` proves
  the live path, including exact Required actions network approval; input audit
  03 retains the exact resolved question and answer. Wider interruption/partial
  batch coverage and completed continuation acceptance remain in the open gate.
- [x] `[must]` Preserve the paused correction alongside clarification answers,
  including repeated rounds without recursively embedding previous prompts.
  Local regressions cover restart, initial/correction instructions, prior
  decisions and refusal of missing or ambiguous paused instructions.
- [ ] `[must]` Qualify correction -> clarification -> resumed correction with a
  real model, checking semantic input completeness as well as answer provenance.
  `cycle-2/tool-clarification-01` exposed a lost correction despite structurally
  valid receipts; it is not positive evidence for this gate.
- [x] `[should]` Restore the full wrapping application title, clarify revision
  labeling and model component selection, development settings, Preview link/QR,
  informal discussion, platform-request consent and public README editing.
- [x] `[should]` Qualify native bidirectional design navigation from original
  messages through versioned Issues, tasks, attempts, partial/corrected results
  and checks. Review separates current Prototype obligations from Automation,
  deferred work and unprocessed messages; no invented tasks or completion score.
  The qualified specimen covers Prototype 003 only; it is not live cross-stage
  coverage or acceptance authority.
- [x] `[should]` Qualify Basic/Detailed presentation in one design schema:
  default Basic, remembered browser choice, optional individual detail views,
  unchanged critical status/decisions and retained revision/selection/drafts.
  Keep this interaction qualification separate from live workflow replacement.
- [ ] `[must]` Bind Scope, Process, Conversation and Review to existing canonical
  Issue/task/Run/evidence/decision refs, not a second requirements tracker.
  Preserve verbatim source versions and exact admitted input snapshots; qualify
  many-to-many links, retries, late messages, source edits and explicit deferral.
- [ ] `[must]` Derive stage/candidate coverage from admitted Issue versions and
  independent evidence. Expose missing/stale refs and material unprocessed
  addenda; navigation cannot retarget Preview or authorize execution/acceptance.
  Recommendations and next-stage work must not become implicit Prototype gates.
- [ ] `[must]` Retain composition-scoped file selection and ownership: Project,
  Scenario, Skill and referenced dependency; validate missing/stale member,
  exact revision and path/digest on reads and proposed changes.
- [ ] `[must]` Bind public README read/edit/save/cancel and LLM patches to one
  artifact with digest preconditions, conflict review and publication boundary.
- [x] `[must]` Supply the document SDK prerequisite: bounded UTF-8 text reads,
  confined project/component paths, atomic writes and shared-lock digest
  preconditions. Concurrent writers cannot silently overwrite a newer edition.
  Live README conflict UX and release qualification remain open above.
- [ ] `[must]` Bind separate informal/formal histories and drafts to canonical
  dialogs. Confirm promotion to an addendum; preserve Web/Telegram authority,
  correlation and delivery identity without a Builder-only chat implementation.
- [ ] `[must]` Restore Development Feedback filtering/provenance and reviewed
  Core/Client escalation with payload consent, delivery receipts, downstream
  task identity and explicit dependency recheck/continuation.
- [ ] `[must]` Verify every development setting has an effective acknowledged
  binding. The old form displayed profile/provider/voice while its submit
  command forwarded only model; visual field parity is insufficient.
- [x] `[must]` Obtain human acceptance of the wide/compact design and its
  interaction semantics. Automated interaction checks do not grant this.
  User acceptance of design 071 is explicit in the 2026-09-14 conversation.
- [ ] `[must]` Bind accepted surfaces to governed state/commands, durable
  conversations and continuations. Verify state-dependent evidence, freshness,
  exact revision identity and scoped screenshot/element feedback end to end.
- [ ] `[should]` Review Client command/status density, conditional-widget gaps,
  compact view switching and full locale coverage against the accepted design;
  evolve generic ABI/components together without Builder-specific renderer logic.
- [x] `[must]` Align generic ABI validation with existing Client support for
  localized action tooltips and state-bound toggle values. No Builder renderer
  specialization; schema and document SDK regression suite: 61 passing tests.
- [ ] `[must]` Qualify separate Prototype and Codex model settings: persisted
  application selection, admitted immutable execution profile, effective CLI
  model and usage receipts must agree. Root and Subscriptions preserve per-model
  fresh/cached/output usage and distinguish quota units from monetary cost.
- [x] `[must]` Intersect Root's allowed models with local Codex `model/list`,
  validate the selected reasoning effort and refresh availability at admission.
  Preserve explicit profile changes between terminal iterations in history;
  never silently substitute another model after an executor rejection.
- [x] `[must]` Run the checkpoint's complete skill-manifest schema in worker
  validation before apply, in addition to targeted contract checks. Retain schema
  paths in repair evidence; do not label partial checks install-strict coverage.
- [x] `[must]` Qualify the retained native TEST Automation independently through
  local HTTP and desktop/mobile CRUD, cancellation, validation, search, stale
  writes and persistence after a real API restart. Keep exact task/source refs;
  this single application does not close repeated-generation or installed-role
  coverage. No generated application code was hand-edited for acceptance.
- [x] `[must]` Verify live Root-to-DEV-Subscriptions model projection against
  the reported task usage, including fresh/cached/output totals and unpriced
  cost coverage. The period aggregate is not an exact per-task financial ledger;
  browser presentation remains under the parent model-setting qualification.
- [x] `[must]` Make Subscription refresh observable before the Root request
  completes and replace that receipt with the authoritative result. Browser
  evidence for `subscription_status@0.1.11` / skill `0.1.24` observes
  `refreshing -> refreshed`, the RU Root authority and effective Codex model;
  the 2026-09-21 rerun projects `gpt-5.5`, including fresh input, cached input,
  output and provider-billable totals. The interaction completes in about 7.5
  seconds and remains a latency target rather than a correctness gap;
  historical `gpt-4.1` evaluation fixtures are not active-model evidence.
- [ ] `[must]` Attach external independent Automation review to canonical
  task/source-bound evidence shown by Review. Distinguish automatic Forge
  checkpointing from reviewer acceptance; an empty consumer requirement set
  must not appear as independently verified user outcomes.
- [ ] `[must]` Project executor waiting, active self-check, automatic repair and
  independent acceptance separately in the existing Process/Result surfaces.
  Show the inspected revision, check coverage/failures and governed evidence refs;
  keep unavailable/not-run results and pending full DEV checkpoint visible.
  Reuse generic Client controls and avoid inventing a second process tracker.
- [ ] `[must]` Qualify OpenSpec-aligned application specification and Change
  deltas with version/digest preconditions, explicit acceptance and preserved
  historical inputs. Do not introduce an independently mutable second tracker.
- [x] `[must]` Implement the canonical specification-delta SDK prerequisite:
  stage-isolated merges, immutable input digests, stale-base refusal, source/Issue
  links and idempotent evidence-backed acceptance. UI and end-to-end qualification
  stay open under the parent acceptance item.
- [x] `[must]` Deploy Root's separate Codex model catalogue and preserve per-model
  usage/cost coverage. Populate the operator policy without changing access rules;
  absent tariffs remain explicitly unpriced. Live Subscriptions/UI qualification
  stays open.
- [x] `[must]` Exercise the accepted live Builder on one isolated TEST
  application from conversational creation through Prototype, Automation,
  independent verification and local delivery; record actual limits separately
  from successful steps. Keep the single paired preview topology.
  Reading List creation `workbench-automation-20260914/journey-01` and managed
  increments `reading-list-migrations-20260915/cycle-1/stable-01` and
  `cycle-2/stable-02` close the bounded local journey at Stable `0.1.4`/`0.1.8`.
  Independent HTTP/browser/migration/restart checks pass without hand-editing
  application source. Multiple repairs and engineering review were needed;
  this is not unattended, first-attempt or general delivery qualification.
- [ ] `[deferred]` Expand read-only artifact inspection into a full file IDE,
  binary/diff editors, bulk asset transformations and collaborative editing.
  This is a BIP-02 sub-scope, not another independent top-level package.

Exit/remaining work: Project generated, structurally qualified, browser qualified, user accepted, Automation ready and blocked/partial outcomes separately. Show material assumptions and useful recovery without exposing internal prompt phases.

#### Replacement Parity Gate

The operational Workspace Builder and its functional-parity manifest remain
the reference until the accepted replacement passes live bindings. The matrix
is a migration boundary, not a declaration that simulated controls implement
the old functionality.

| Existing capability | Target surface | Replacement boundary |
| --- | --- | --- |
| Select/create application; templates | Application picker | Selection specimen exists; creation/template flow not modeled yet |
| Metadata, versions, stability; missing application | Application overview / Settings | Name editing modeled; complete metadata, missing-state recovery and archive/delete/restore not modeled |
| Component list, file tree, protected files | Files | Component/tree/ownership modeled; real scoped reads and errors pending |
| General source-file editing | Files / external development tools | Deliberately narrowed MVP: read-only plus LLM request; full file IDE deferred, README exception modeled |
| Development model/profile/provider controls | Settings | Local fields modeled; effective settings binding/validation pending |
| Preview selection, new window, QR, comparison | Result / Preview / Revisions | Local specimen link/QR and revision inspection modeled; live target comparison/reachability pending |
| Formal task chat and history | Conversation | Native chat plus local composer modeled; durable conversation and workflow commands pending |
| Informal discussion | Conversation scope menu | Separate scope and confirmed proposal modeled; canonical cross-channel binding pending |
| Technical specification and addenda | Scope / Conversation | Versioned requirements and sources modeled; canonical specification/addendum lifecycle pending |
| Prototype approval, Automation start/retry/return | Current work / Process | Separate decisions/recovery modeled; governed implementation bindings and return-to-Prototype pending |
| Process tree, exact ref inspection, history | Process / Revisions | Revision navigation modeled; complete live graph/provenance pending |
| Trial, release, source push, publication | Deliveries | Local Beta/Stable decision specimen; real distribution/source push qualification remains gated |
| Stable subscription update plan/apply | Deliveries / Application overview | Not modeled; retained must gate, no silent omission |
| Development Feedback filters/details | Signals | Finding and consent modeled; full filtering/triage and downstream receipts pending |
| Bound development session / initiator | Scope provenance / Application overview | Real project-owned initiator retained in architecture; not modeled in this fixture |
| Dev Tickets / screenshots | Dev Tickets | Existing panel reused; full capture/upload/global-scope qualification remains open |
| Public user documentation | README | Render/edit/reopen modeled; actual file writes, conflicts and release packaging pending |

### BIP-03

- [ ] `[must]` **Runtime Latency And Stage Accounting**. Status: `partial`.

Owner/dependencies: Core runtime + Client + Root telemetry.

Implementation boundary: Runtime lock profiling and the path-promotion fix are retained; browser fan-out/post-save stalls remain. Live qualification of the loaded fix remains required.

- [x] `[must]` Remove repeated manifest parsing/validation from the catalog hot
  path without stale-title caching. Use a bounded cache keyed by current file
  and schema bytes; isolate returned objects and keep deletion/error checks live.
  Local worker profiling and manifest/SDK regressions qualify this mechanism.
- [ ] `[must]` Requalify cold/warm Select project browser latency after loading
  the catalog fix; do not infer full HTTP/browser performance from worker timings.
- [x] `[must]` Remove per-row authority reads from the Applications hot path.
  One list request now reads the Home snapshot and deployment inventory once;
  one detail request enriches only its selected Application. Local Root MCP
  measurements improved `applications.show` from about 16 seconds to 1.4
  seconds and installed/available lists to 0.6-1.5 seconds on the same node.

Exit/remaining work: Measure the loaded fix on cold/warm browser journeys; separate queueing, import lock, execution, invalidation, readiness and Root phases. Reconcile billed/normalized cost and exact environment digests; do not mask stalls with larger timeouts.

### BIP-04

- [ ] `[must]` **Clean Baseline And Sealed Evaluation**. Status: `blocked`.

Owner/dependencies: Evaluation; depends on BIP-01, BIP-05 and Client C0/C1 plus truthful C2 subset.

Implementation boundary: Visible development suites and immutable run reports exist. No sealed 40-prompt result is established by this audit. Repeatedly inspected development archetypes are not held-out.

Exit/remaining work: Seal at least 40 ordinary EN/RU prompts across eight different archetypes; freeze model/profile and complete the run before remediation. Keep legacy control separately labelled. On sample inspection, reclassify the set as regression and seal a successor.

### BIP-05

- [ ] `[must]` **Unified E2E And Evidence Integrity**. Status: `partial`.

Owner/dependencies: Evaluation runner + SDK.

Implementation boundary: Public CLI/SDK execution, attribution checks, independent HTTP/browser plans and attempt archives exist. Several qualification journeys still use separate stand entrypoints; early overwritten outputs are unrecoverable.

Exit/remaining work: Run adversarial/mutation, accessibility, authority, preservation and task probes through one resolved E2E contract. Verify attribution before grading, unify metrics/comparison/resume, and retain first attempts plus missing-evidence flags. Historical incomplete cohorts cannot become pristine baselines.

- [ ] `[must]` Integrate versioned per-check receipts and model-visible feedback
  into the existing E2E attempt archive. Preserve candidate/environment identity,
  complete retrievable diagnostics, actual multimodal inputs, missing evidence
  and application/platform/infrastructure failure classes.
- [ ] `[must]` Run the matched feedback-loop comparison in the evaluation contract:
  current worker repair, scoped self-checks, then added browser observations.
  Freeze the same task/base/profile and repetitions; report end-to-end accepted
  outcome cost, failed attempts, regression escape and human interventions.

### BIP-06

- [ ] `[must]` **Independent Grader Calibration**. Status: `partial`.

Owner/dependencies: Evaluation.

Implementation boundary: Stage-aware grader rules, negative probes and selected live regrades exist. Agent-authored controls are not a human-labelled calibration set; selected live regrades do not establish general judge reliability.

Exit/remaining work: Broaden human-labelled false-positive/negative and mixed-stage calibration; version any retained-candidate regrade independently without source mutation. Keep grading uncertainty distinct from application failure.

### BIP-07

- [ ] `[must]` **SDK And Core Ownership**. Status: `partial`.

Owner/dependencies: Core services + SDK + Builder skill.

Implementation boundary: prototype.submit_request currently selects LegacyDevSkillPrototypeExecution. The DEV handler still owns provider request/repair orchestration and source/revision writes; ui_capabilities still calls intent interpretation. A public facade and SDK-only imports do not prove target ownership.

Exit/remaining work: Move interpretation/execution, provider lifecycle and transactional artifact writes behind owned Core ports; make Builder a conversation/projection adapter. Enforce dependency direction and parity before removing duplicate transformations. Unrelated large-module decomposition stays deferred.

### BIP-08

- [ ] `[must]` **Single Component Contract**. Status: `partial`.

Owner/dependencies: Client component roadmap C2/C3 + Core ABI.

Implementation boundary: The AST-generated capability inventory detects drift, but reads handwritten registries/models; it does not generate them from one component contract. Adding `visual.metricTile` semantics and icon rendering required coordinated handwritten catalog and Client edits, which is useful coverage but direct evidence that single-source generation is still missing.

Exit/remaining work: Publish the authoritative contract and derive Client registration/types, compiler/validator indexes, retrieval and docs. Require ABI impact reports, conformance fixtures and matched browser evidence for admitted components, not every future widget.

### BIP-09

- [ ] `[must]` **Capability Discovery And Gaps**. Status: `partial`.

Owner/dependencies: Core capability service + SDK/MCP; consumes
`CapabilityContract` and `BindingDefinition` from the Capability, Binding, and
State roadmap.

Implementation boundary: Bounded search/get and typed gaps exist in slices. Discovery can return low-relevance results and metadata-only drill-down; not every Prototype request supplies the advertised retrieval tool.

Exit/remaining work: Admit semantic contracts by shape, operation, effects,
authority, state ports, supported mode and evidence before optional ranking;
expose callable bounded read-only retrieval and distinguish unsupported
capability, absent index, missing production binding and irrelevant matches.
Provider contracts, Root MCP tools and skills are candidate implementation
evidence, not semantic capability identity. Evaluate retrieval on independent
queries without subject rewrites and reject persisted raw tool/skill/provider
dependencies in semantic Application output.

### BIP-10

- [ ] `[must]` Preserve whole behavioral constraints during extraction; do not
  convert verb/noun matches inside negative or conditional clauses into separate
  positive operations. Keep original evidence spans and distinguish the active
  correction from inherited requirements without silently discarding either.

- [x] `[must]` Separate explicit EN/RU process/privacy/preservation instructions
  from widget coverage in model context and compiler, retaining exact Brief IDs
  and constraints in the compiled artifact. Preserve ambiguous application rules.
  This narrow classification is not a general semantic interpreter or review pass.
  Coordinated stage prohibitions, explicit Automation handoff descriptions and
  truthful Prototype-vs-production claims belong to process review, not widgets.
  Keep accepted source history without repeating completed Change directives as
  current execution instructions. Qualify scoped residual interpretation and
  incremental context size; more regex coverage alone cannot close this task.
- [ ] `[must]` Qualify process-constraint review and subsequent Automation handoff
  on the retained application increments; do not count discarded UI bindings as
  evidence that a privacy, preservation or stage restriction was satisfied.
- [ ] `[must]` Qualify the first Automation packet before and after the stage
  transition: explicit requested phase, separate observed phase, Prototype-only
  mock provenance, and the actual model-facing data/configuration contract.
  Reading List cycle 1 exposed the pre-transition inference defect. A packet
  coverage flag alone did not detect it; the affected run remains diagnostic.

- [x] `[must]` Preserve reference punctuation and original source offsets during
  deterministic clause extraction; do not infer UI operations from URL path words.
- [x] `[must]` Stop promoting ambiguous authoring verbs into mandatory resource
  CRUD. Deterministic qualification now emits a conservative confirmed/
  excluded/unknown resource-scope signal; unknown remains model Brief work and
  cannot create a persistence acceptance gate by itself. Retain explicit
  end-user record mutation coverage and negative UI-authoring regressions.
- [ ] `[must]` Qualify accumulated multi-turn context capacity on a released
  application: distinguish lexical hints from admitted behavior and execution
  constraints; retain every source without requiring invented UI for metadata.
  Traceability capacity preflight and bounded repair continuation are prerequisites,
  not proof of resolved intent interpretation.

- [ ] `[must]` **Brief Interpretation And Requirement Provenance**. Status: `partial`.

Owner/dependencies: Core intent/Brief + Builder review.

Implementation boundary: capture/compile/merge preserve source evidence, but compile_prototype_brief still sets outcome, actors and entities to unknown and starts with empty assumptions/questions. Model-suggested external scope is not reliably distinguished from user obligations at the handoff.

Exit/remaining work: Implement constrained residual interpretation, semantic-ID deltas and material-only clarification (at most three questions). Distinguish user requirements, explicitly accepted additions, suggestions and external gaps. Generic UI approval must not silently accept speculative integration scope; test partial progress without a green full verdict.

### BIP-11

- [ ] `[must]` **Semantic Authority And Incremental Compilation**. Status: `partial`.

Owner/dependencies: Core semantic compiler + Client mappings; emits
`ApplicationRequirement` and state-port refs consumed by semantic resolution.

Implementation boundary: Semantic-v2 multi-resource generation, typed links, structural state proof, source maps and lookup-only resources exist. This is not a total contract for the admitted component set or proof of one-way semantic authority on all managed edits.

- [x] `[must]` Record the graph-first stream target across Builder, WebIO and
  streaming architecture docs: `SemanticGraph_0 + semantic ops ->
  SemanticGraph_t -> compiled WebUI`. `/pageSchema` patch streaming is now
  documented as compatibility/debug projection, not semantic authority.
- [ ] `[must]` Bind each incremental input to the actual accepted implementation
  and effective compiled UI, not an old semantic metadata marker. Reconcile
  supported Automation edits through semantic source/declared binding overlays;
  unsupported drift blocks generation with a typed gap or reviewed conversion.
- [ ] `[must]` Qualify Prototype -> Automation -> next Prototype preservation of
  media, settings-driven visibility, layout, interactions and locales. Retain
  actual model inputs and both source identities; never hide drift by regenerating
  from a title or silently reverse-compiling arbitrary renderer JSON.
- [ ] `[must]` Compile semantic activities and state needs to versioned
  `ApplicationRequirement` and state-port refs. Keep UI command refs stable
  while simulation and production bindings change independently.
- [ ] `[must]` Admit only semantic refs supplied through bounded capability
  context. Raw MCP tool IDs, skill IDs, provider IDs, package members, endpoints
  and credential refs fail semantic validation or remain non-authoritative
  candidate annotations.
- [ ] `[must]` Preserve one semantic Application revision across fixture,
  simulation, Trial and production materializations; bind each materialization
  to its own resolution/evidence instead of rewriting semantic source.
- [ ] `[must]` Define the semantic graph operation-log ABI and reducer contract:
  bounded `meta`/`op`/`complete` events, stable semantic refs, `seq`,
  `base_hash`, `transaction_id`, and replayable draft errors.
- [ ] `[must]` Add deterministic replay fixtures proving that semantic ops
  reduce to the expected graph and compile/project to the expected
  `pageSchema` without renderer JSON pointer edits.

Exit/remaining work: Complete the supported semantic ABI and
requirement-to-resolution/runtime mapping; qualify empty-state reachability,
granularity, typed gaps, reference/display capacity and incremental
preservation. Retain atomic full-artifact promotion; no lossy fallback or
forced collection screen for lookup-only data. The linear cross-roadmap proof
is `ASC2` through `ASC6` in the Semantic Application Composition roadmap.

### BIP-12

- [ ] `[must]` **Deterministic Semantic Edits**. Status: `partial`.

Owner/dependencies: SDK semantic UI + Core compiler.

Implementation boundary: Bounded semantic operations and deterministic review transforms exist, alongside legacy renderer edits.

- [x] `[must]` Document deterministic semantic edits as graph operation inputs
  over stable refs, with renderer JSON pointer patches retained only for the
  compatibility/debug path.
- [ ] `[must]` Provide zero-model semantic op fixtures for rename, move,
  visibility and option edits, then prove unchanged unrelated behavior through
  graph replay and compiler output.

Exit/remaining work: Qualify rename/move/visibility/options through ordinary requests with zero model calls, semantic refs, unchanged unrelated behavior and the declared D0 latency target. Do not equate a literal WebUI patch with semantic cutover.

### BIP-13

- [ ] `[must]` **Scoped Repairs And Preservation**. Status: `partial`.

Owner/dependencies: Core semantic repair + Builder execution.

Implementation boundary: Binding/reference/state repairs, additive visibility, view-only repair, exact invariants and immutable-candidate checks are implemented. Latest local semantic tests pass; full-candidate fallback and complete independent-diagnostic coverage are not eliminated.

- [x] `[must]` Present deterministic repair as its own current phase, leading
  with observed failures and preserving the full admitted task as reference.
  Local prompt regressions qualify this ordering and preservation only. The
  former blanket no-tests/no-diff instruction is superseded by the target feedback
  contract below; its removal and interactive verification are not implemented.
- [ ] `[must]` Replace conflicting normal/repair instructions with one scoped
  self-check contract: Codex can inspect its changes and request the existing
  trusted runner's tests/schema/install-strict checks during implementation.
  Enforce admitted paths, fixtures, network and resource policy in the backend;
  the model cannot modify independent gate definitions or certify acceptance.
- [ ] `[must]` Feed actionable, candidate-bound findings into scoped repairs with
  retrievable full evidence and immutable per-turn inputs. Aggregate independent
  failures before remediation; mark blocked/unexecuted checks truthfully. Prove
  repair -> recheck -> independent final acceptance without an operator relay.
- [ ] `[should]` Select checks by affected contracts and hypotheses, deduplicate
  in-flight work and reuse only exact hermetic receipts. Tune configurable repair
  budgets from measured progress; do not impose a fixed model-call sequence or
  claim lower latency merely by forbidding checks or shortening timeouts.
- [x] `[must]` Recheck a retained normalized failed candidate before querying
  Root again. Bind replay to the original request, scenario and current source
  revision, run the current full-document validation and request postconditions,
  and retain zero-token replay separately from paid repair. Unit qualification
  covers valid replay, stale identity and invalid-candidate refusal; matched
  end-to-end benefit remains part of BIP-05.

Exit/remaining work: Aggregate all independent findings before selecting scope; qualify fixture/value/binding repair, exact replay and unchanged accepted semantics on fresh repeated runs. Compare authoritative slices with full-context/full-regeneration controls before reducing payloads.

### BIP-14

- [ ] `[must]` **Context Routes And Budgets**. Status: `partial`.

Owner/dependencies: Core execution + provider adapter.

Implementation boundary: Semantic requests use typed output and stable/dynamic context separation; selected repairs are scoped. Effective implicit Root profiles, all-route budget enforcement and matched correction-context savings remain unqualified.

- [x] `[must]` Add an explicit named-widget correction route that keeps the
  canonical WebUI server-side and sends only target widgets, state-writer
  dependencies, relevant layout/modal/locale fragments and an omitted identity
  inventory. Stable `@id` patches merge into the canonical document and undergo
  full validation. Bound recent history and development-context fields. On the
  retained Web Desktop correction, actual input fell from 34,948 to 8,722 tokens
  (75.0%) and the accepted revision changed only the requested static value plus
  Builder revision metadata; no output cap was reduced.
- [x] `[must]` Make the active turn the final execution authority and history a
  bounded reference. Recap every explicit active acceptance condition, and
  force structural additions/removals, layout-role/cardinality changes, and
  cross-surface redesigns onto the wider stage context even when one existing
  widget is named. Focused prompt regressions cover these routing boundaries.
- [ ] `[must]` Extend measured stage-specific composition to section additions,
  cross-cutting redesigns, semantic planning and repair without misclassifying
  broad work as a local correction. Compare accepted outcomes, repairs, cached
  tokens and wall time against the full-context control before changing defaults.

Exit/remaining work: Use stage-specific authoritative slices and retrievable refs, byte-stable selected bundles, explicit fresh/cached/output/time/attempt budgets and deterministic stop/fallback. Promote efficient/full/visual routes only by matched evaluation; measure schema-cache tradeoffs and the 60% cache target where supported.

### BIP-15

- [ ] `[must]` **Provider Completion And Resume**. Status: `partial`.

Owner/dependencies: Root adapter + E2E + Builder recovery.

Implementation boundary: Partial-output/grader-usage regressions, numbered prompts and worker attempt archives exist. Older late/failed jobs have incomplete totals; durable-operation-first recovery is not qualified across every interruption path.

Exit/remaining work: Reconcile late completion and accounting idempotently; retain complete and partial raw outputs, missing usage and separate reasoning. Resume exact durable operations without duplicate submission or rewriting old failed verdicts. Verify failed grader usage reaches aggregate reports.

### BIP-16

- [ ] `[must]` **Executable Client Contracts**. Status: `partial`.

Owner/dependencies: Client runtime + Core compiler/acceptance.

Implementation boundary: Forms, live related options, ID/display separation, layouts and media surfaces have real probes. item.details named-command sequencing is fixed, but PageActionService can still return success after cancelled non-form confirmations.

Exit/remaining work: Qualify one action success/cancel/failure contract across used widgets, hydration races, failed drafts and create/update/delete. Verify fresh relationship creation/rename/reopen and actual generated wide/compact artifacts; outcomes derive from record effects, not CRUD verb names.

- [ ] `[must]` Reject unsupported expression/binding semantics and prove setting
  change -> intended view visibility -> reload hydration. Verify loaded images in
  cards/details with empty/error states; a retained media ID, HTTP 200 or substring
  assertion cannot replace a rendered outcome. Keep tests/component fixes generic.
  Progress: WebUI action validation now rejects the NLU-only `$ctx.*` namespace,
  the Client prevents unresolved routing references from becoming resource ids,
  and Desktop -> Users & Access is browser-proven against the exact Trial.
  The item remains open for the other expression namespaces, settings hydration,
  rendered media states and generated action outcomes.
- [x] `[must]` Reject conflicting state writes, unreachable controlled values,
  unreachable layout/widget guards, JavaScript-like action expressions, malformed
  structured expressions and unresolvable static detail selections before
  Preview. Resolve details `$state.*` values through the shared declarative
  resolver and expose stable repeated-row command ids for browser feedback.
  Core capability suites and focused Client list/details tests qualify this
  generic subset; settings reload and media outcomes remain open above.
- [x] `[must]` Retrieve and emit Layout & Interaction ABI v2 rather than relying
  on area-name inference. The model chooses a task pattern, semantic regions,
  wide/compact disclosure, and explicit collection/detail interaction from the
  authoritative component contracts; it never writes Taiga directives or a
  parallel YAML layout.
- [ ] `[must]` Add layout conformance findings to Prototype and Automation
  evidence. Capability gaps and optional UX richness remain review feedback;
  malformed regions, unreachable primary actions, overlap, clipping, or a
  missing compact path are correctness failures.

### BIP-17

- [ ] `[must]` **Repeated Small-Prototype Reliability**. Status: `partial`.

Owner/dependencies: Evaluation + Builder.

Implementation boundary: The visible cohort has complete passing runs and selected repeated/capability probes, but profiles, changes and browser coverage differ. Their union or best candidates is not a matched reliability baseline.

Exit/remaining work: Freeze one candidate/profile and predeclare repetitions, failure budgets and task coverage. Repeat all eight visible archetypes with full actual inputs/outputs and browser probes, distinguishing first-pass, repair, platform and grader failures; keep held-out claims in BIP-04.

### BIP-18

- [ ] `[must]` **Locale Contract Qualification**. Status: `partial`.

Owner/dependencies: Core compiler + Client + Builder.

Implementation boundary: Current-locale generation and scenario-owned dictionaries are implemented. Qualified EN and RU examples do not alone establish the explicit bilingual-change/preservation route.

Exit/remaining work: Verify EN-only, RU-only and explicit bilingual requests plus preservation of existing translations, fallback/interpolation/key parity and viewport changes. Builder's own EN/RU UI requirement remains separate from generated application locale selection.

### BIP-19

- [ ] `[must]` **Presentation And Local Runtime Integration**. Status: `partial`.

Owner/dependencies: Client component roadmap + Core runtime.

Implementation boundary: Wide/compact journeys pass in bounded cases; clipped commands, raw values, label-key reuse, compact detail navigation and some theme/readiness findings remain. BIP-25's storage evidence is narrower than end-to-end latency.

Exit/remaining work: Resolve owner-specific rendering defects and measure compact task completion/readability, not merely page width. Preserve exact preview identity and declared text policies. Optional UX richness stays non-blocking; no archetype-specific widgets or new layout restrictions on accepted prototypes.

### BIP-20

- [ ] `[should]` **UX Guidance And Feedback Corpus**. Status: `partial`.

Owner/dependencies: Builder guidance + evaluation.

Implementation boundary: Generic editor/query/layout guidance exists; no measured, versioned golden-rule promotion process is established.

Exit/remaining work: Curate applicable, explained recommendations with alternatives from sanitized failures and accepted fixes. Evaluate paired simple/richer designs and human feedback across domains. Keep recommendations separate from explicit requirements and platform safety gates.

### BIP-21

- [ ] `[must]` **Bounded Browser Review And Feedback**. Status: `in progress`.

Owner/dependencies: Evaluation + Builder review/Dev Tickets.

Implementation boundary: Automation now materializes the candidate in the one
paired DEV preview before Forge checkpointing and runs a generic wide/compact
browser gate. Its UTF-8 receipt binds the source WebUI, context packet, Client,
UI ABI, capability catalog, runtime URL, DOM/console/network findings and
screenshot digests. A failed receipt is projected into the implementing Codex
context for at most two independently rechecked repairs. This path is DEV-only;
it does not create case-specific webspaces or grant the model final acceptance.
Applications and Users & Access now qualify this integrated route on wide and
compact layouts. The gate exercises semantic tabs and compact region
disclosure, rejects renderer error/unsupported states and invalid semantic
toolbar order, and retains per-view evidence. The local Client now serves a
neutral runtime bootstrap and authenticates its first loopback status probe, so
those former background `404`/`401` exceptions are no longer accepted warnings.
Other HTTP failures, including retryable runtime `503`, remain failures.
For Trial review, the gate derives the exact candidate revision and complete
release digest from `workspace.lock.json`, canonicalizes only the admitted
transport separators in `trial:sha256:<digest>`, and rejects a different or
stale materialization. Visible nonempty detail content is checked through the
semantic details component rather than a private DOM class.
Users & Access additionally qualifies the complete trusted ordering through
Automation: exact accepted-Prototype and Root MCP contracts are bound before the
Codex turn; candidate materialization and six-tab wide/compact review precede
Forge; the live reversible Root MCP journey passes 8/8. Finalization now checks
that the Builder host is still active before touching Preview and reuses a
successful browser materialization instead of rebuilding it after checkpoint.
Acceptance must inspect the materialized active semantic view. Source-only
checks over legacy `pageSchema.widgets` are insufficient when `semantic.views`
owns rendering; the Taiga UI Demo regression demonstrates this boundary.
Web Desktop beta qualification extends this rule to data authority: required
bindings publish loading/ready/error plus authoritative-value presence, and the
gate waits for settled real projections before evaluating content. It uses an
explicit Workspace target for Trial, snapshots primary semantic tabs before
dynamic nested navigation, records bounded per-phase timings, and rejects a
compact region whose usable width collapses even when the document itself does
not overflow. Optional cancelled loopback probes are warnings only after all
required sources settle. Candidate fixtures cannot satisfy an installed
Applications or identity projection owned by node authority.
Automation materialization now strips Prototype fixture payloads and bindings
from the runtime projection while preserving the accepted source revision.
Browser repair context explicitly requires Root MCP/runtime diagnosis before a
source edit and forbids restoring fixtures. Repeated live Applications evidence
after this correction remains part of the open gate.

- [x] `[must]` Expose candidate browser actions, DOM/accessibility, image bytes,
  console/network and synthetic data evidence through scoped check operations.
  Bind source/Client/ABI/runtime/fixture identities; reject stale/wrong targets,
  redact private data and preserve one paired preview without new case webspaces.
- [x] `[must]` Return independent runtime/UI failures to the implementing Codex
  without manual copying. Protect final gates and revalidate the corrected digest;
  optional visual taste must not become a hidden requirement. Qualify selected
  wide/compact journeys, persistence and image loading, not screenshots alone.

Exit/remaining work: Add stateful persistence and loaded-image journeys and
prove that stale/wrong-target evidence fails closed. Route exhausted or
platform-owned findings to Dev Tickets instead of repeatedly editing the
Application. Two targeted review repairs are the initial review profile, not a
universal fixed track; adjust only with recorded progress/budget evidence.
BIP-34 is a separate optional model reviewer, not a prerequisite for Codex to
observe its own result.

### BIP-22

- [ ] `[must]` **Preview Topology And Deletion**. Status: `partial`.

Owner/dependencies: Core topology + Client commands.

Implementation boundary: Registered W -> W-dev -> optional W-dev-dev pairing and bounded cleanup were implemented; the historical webspace-per-case runner prose is superseded.

Exit/remaining work: Qualify idempotent deletion admission/completion, absence after reconnect/prewarm and transparent explicit preview recreation. Keep one preview per selected Builder; no new case/session hosts and no private cleanup bypass.

### BIP-23

- [ ] `[should]` **Source Checkpoints And Artifact Retention**. Status: `partial`.

Owner/dependencies: Developer SDK + Root source persistence.

Implementation boundary: Prototype local revision/hash receipts and Automation remote component receipts plus local composition reservation exist. They do not establish full remote DEV ProjectRelease checkpoints after each stage acceptance. Invalid-source quarantine, sparse checkout hydration, attachment GC and durable scenario-checkpoint fault qualification are not all closed.

Scenario checkpoints now preserve `webui.json` as the sole executable UI source:
derived `scenario.json` retains only `ui.manifest`. Worker validation rejects
generated tests that pin checkpoint-owned versions, timestamps or raw manifest
digests; canonical Prototype semantics explicitly ignore release bookkeeping.

Exit/remaining work: Qualify version/task-bound durable acknowledgements and recovery without double version bumps; preserve exact source/task identity through transport failures. Add non-promotable diagnostic checkpoints, safe hydration and owner/revision-bound attachment reclamation. Preserve the must-level durable-checkpoint gate in builder-sdk-boundary.md.

The full-project acceptance hook and its failure/recovery gate are owned once in
[SDK-04](builder-sdk-boundary.md#roadmap-and-checklist); this should-level retention
package does not downgrade that must requirement or equate a local hash with VCS.

### BIP-24

- [ ] `[should]` **Declared SDK Read Policies**. Status: `partial`.

Owner/dependencies: Builder SDK data routes + Client invalidation.

Implementation boundary: Declared frequency/invalidation policies still have warnings in retained promotion evidence.

Exit/remaining work: Execute declared request limits and causal invalidation tags exactly; test no-op/read/write behavior and turn proven policy conformance into a promotion gate. Link duplicate reads to BIP-03.

### BIP-25

- [x] `[should]` **Bounded Prototype Storage**. Status: `verified`.

Owner/dependencies: Core resource service.

Implementation boundary: Keyed lookup and append-oriented relational traces replace enumeration/whole-journal writes, retaining legacy data. Resource regressions and retained local qualification cover this storage mechanism.

Exit/remaining work: This closes the bounded storage mechanism only. HTTP/browser fan-out and runtime import-lock latency remain BIP-03; no inference from in-process timings to end-to-end performance.

### BIP-26

- [ ] `[must]` **Automation Admission And Recovery**. Status: `partial`.

Owner/dependencies: Core Automation + SDK/MCP + worker.

Implementation boundary: Frozen Equipment and the remaining cohort exercise explicit briefs, owned source, no fixture seeding, binding contracts and partial recovery. First attempts/repairs are now archived.

Exit/remaining work: Complete stage-specific acceptance, fixture/installed authority, source presentation and exact recovery identity across all routes. Validate real declared capabilities and scope before implementation; preserve accepted prototypes. Trial obligation closure and delegated authority remain BIP-28.

- [ ] `[must]` Qualify two projects contending for one local executor: exact-task
  `node_busy` wait, no duplicate paid submission, same-project idempotency/conflict,
  cancellation and restart/resume. Preserve project/Change/input identity while
  the user switches focus. Do not describe retry polling as a fair FIFO queue.
- [ ] `[must]` Prove source/checkpoint generation guards and shared-preview target
  protection during finalization after the executor becomes free. A node's active
  Codex lock is not whole-pipeline serialization; contention must not replace
  another application's preview or source.

### BIP-27

- [ ] `[must]` **Frozen Automation Cohort**. Status: `partial`.

Owner/dependencies: Builder + independent acceptance.

Implementation boundary: All seven retained local HTTP/browser workflows have passing evidence; six qualify the agreed DEV-local scope. Media passes locally but external notification remains unresolved, and intermittent runtime stalls remain.

Exit/remaining work: Resolve Media's requirement provenance/recipient/channel scope through BIP-10, not a fabricated notification. Repeat unchanged applications after BIP-03/BIP-16; distinguish local partial success from full acceptance. Do not regenerate accepted Prototype 002 or manually patch application source.

- [ ] `[must]` Requalify retained Reading List settings/view and cover preservation
  on synthetic data with independent outcome checks. Prior lifecycle passes do
  not cover these escaped regressions. Diagnose model input freshness separately
  from model output and Client/runtime defects; implement fixes through Builder.
- [ ] `[must]` After the feedback pilot, repeat the frozen small Automation cohort
  with one matched profile and protected final checks. Report repairs and human
  interventions, not a union of historical green tests or best revisions.

### BIP-28

- [ ] `[must]` **Installed Lifecycle And Delivery**. Status: `partial`.

Owner/dependencies: Builder lifecycle + delivery + delegated authorization.

Implementation boundary: Two native local Reading List increments complete at Stable `0.1.4` and `0.1.8`, with independent execution, migration, browser and restart evidence. A separate Volunteer Roster reaches Stable `0.1.5` and `0.1.7` through two Builder cycles. Its second cycle adds owned photos, forward relational/blob adoption, exact runtime provenance and delegated viewer/coordinator role checks in both Trial and Stable. Multiple repairs and engineering review prevent an unattended-success claim. Consumer installation, all-room/background cutover and public prerelease remain open.

- [x] `[must]` Reconcile an exact retained placement without repeating activation
  after a stale workflow response. Changed candidate, target, runtime or safety
  evidence still fails the generation precondition; preserve the first failure.
- [x] `[must]` Fail closed when an exact Trial has no admitted executor.
  Qualify no DEV/stable fallback through both HTTP routing forms. The historical
  Preview-based denial is a containment fix, not the target delivery route.
  An unavailable informer is not functional Trial acceptance.
  Evidence: `trial-navigation-02`, `trial-boundary-before/after`, and 264 Core
  boundary/recovery/materialization tests. See the qualified local executor
  slice below; delegated and distributed execution remain open.
- [x] `[must]` Before first Workspace publication, Builder's publication command
  reconciles the local beta RuntimeSelection and the running production desktop
  catalog, including replay without another activation. Trial/Publication no
  longer enter the Preview materialization/selection API.
  Evidence: `trial-builder-delivery-02`, `trial-browser-03`; native desktop and
  mobile CRUD use the same retained Reading List Candidate.
- [x] `[must]` Qualify local beta navigation from Builder Process and desktop,
  return home and reopen on desktop/mobile. Evidence: `trial-builder-navigation-05`
  and `trial-browser-04`; exact Trial execution headers and unchanged DEV data.
- [ ] `[must]` Qualify automatic local Builder Beta cutover for installed
  Applications without a second Applications approval. Reflect the local Beta
  flag while preserving the separate external prerelease subscription. No new Webspace or home-scenario
  replacement is permitted. Consume the exclusive cutover in `AP4-20` and
  `APP1-13`: one effective channel/desktop representation, no parallel
  verification Beta, including through old links or background execution.
- [x] `[must]` Qualify the local foreground cutover subset through the native
  Builder command, without a separate Applications approval: one desktop tile,
  local Beta flag, unchanged external prerelease subscription and exact replay
  without reseeding data. Reading List `cycle-2/beta-replay-01` retains the
  `0.1.8` Candidate and all six Beta records. All-room/background authority stays
  under the preceding open gate.
- [x] `[must]` Qualify one first-release Web Desktop foreground delivery from an
  exact accepted Prototype through Automation, complete DEV checkpoint,
  immutable Candidate and automatic local Beta placement. The earlier Candidate
  `web_desktop-0-3-28-ffba58b12ff6` established placement but not data authority.
  Accepted Candidate `web_desktop-0-3-33-afc508c5ef2b` (`0.3.33`, acceptance
  receipt `web-desktop-beta-authoritative-final/attempt-05`, restart replay
  `attempt-06`, post-supersession replay `attempt-07`) resolves `desktop` through
  `application_trial`; nine-section wide/390px review proves authoritative
  projections, host page navigation and non-collapsed compact regions without a
  modal/legacy fallback. Older `0.3.28`, `0.3.31` and `0.3.32` Trial activations
  are rejected and detached through the governed lifecycle; exact rollback does
  not remove the selected `0.3.33` Beta. This does not close installed-Stable
  upgrade, all-room/background or public delivery.
- [ ] `[must]` Include versioned data schemas, semantics and invariants in
  Automation context without real user records. Require algorithmic forward
  migrations tested on synthetic data and clarification of ambiguous mappings;
  migration execution itself must not call an LLM or export records. Every new
  Beta must prove the complete path from Stable, not only the previous Beta.
- [x] `[must]` Execute declared SQLite initialization and exact reopen with the
  real Core executor during Automation validation, independently of candidate
  test doubles. Use disposable empty stores without importing candidate Python
  or accessing installed records; record per-manifest timing. This establishes
  initialization compatibility, not migration correctness on existing records.
- [x] `[must]` Reject unreachable submit actions in forms with explicit named
  buttons. Shared command steps use the same action/button ID and ordered,
  fail-stopping execution; validate skills and scenarios independently of
  candidate tests. Keep this dispatch rule in the bounded Automation capsule.
- [ ] `[must]` Expand that independent check to immutable published migration
  history and synthetic fixtures for every supported prior schema. Current
  Reading List E2E checks cover these cases outside the generic worker; keep the
  broader gate open until the worker consumes a generic fixture contract.
- [ ] `[must]` Qualify repeated Automation against the same immutable Prototype
  handoff after later Preview demo edits. Partial: admission and worker bind and
  verify a previous Core/model-input snapshot in the same session/acceptance;
  19 focused tests cover identity and tampering. Reading List continuation now
  qualifies retained Prototype `006` through repair, independent verification
  and Stable `0.1.8`. Broader first-admission snapshot lifecycle remains open.
  Never weaken live freshness
  checks to reconstruct an older acceptance from current mutable records.
- [ ] `[must]` Bind Beta review to `AP4-21` migration/data evidence and preserve
  Beta writes on acceptance (`Keep data=true`); distinguish immutable code from
  mutable working data. Qualify explicit recovery-point/loss confirmation for
  snapshot rollback, including cancellation and restart of that decision.
  Also expose and qualify possible loss when a new Beta starts from Stable
  again instead of carrying forward records entered in the preceding Beta.
  Backward migration remains deferred under `APP1-12`/`APD-11`.
- [x] `[must]` Qualify local native changelog acceptance of the retained Beta
  with its working data and configuration. `cycle-2/stable-02` and
  `stable-data-http-01.json` prove adoption before removing only the three
  E2E-owned records; all three user records and three settings remain, including
  the original two records from cycle 1. Read-only post-restart observation and
  private comparisons pass. This application has no credential slots; successive
  Beta replacement, rollback loss consent and real secret transfer remain open.
- [x] `[must]` Qualify a second Application's permission-aware local delivery
  across two complete Builder cycles without hand-editing generated source.
  Volunteer Roster records the reviewed permission profile and access matrix,
  automatically provisions the publisher's exact-digest owner grant, enforces
  viewer/coordinator boundaries, preserves relational rows and photo blobs, and
  passes wide/compact Stable browser persistence with `workspace` provenance.
  Evidence: `permissions-volunteer-20260916-cycle2-automation-retry1`,
  `cycle2-permission-runtime-v3`, `cycle2-stable-permission-runtime-v2`, and
  `cycle2-stable-browser-acceptance-v2`. This does not close consumer, child,
  guest, background-worker or public-distribution scope.
- [x] `[must]` Admit local `empty` Trial data to native skill execution with its
  own context, declarations, runtime slots and store, using original node policy.
  Verify package/lock/release identity; preserve DEV data across Trial writes and
  an API restart. Evidence: `trial-http-02` (15 checks), `trial-restart-01`,
  `trial-browser-03` (desktop/mobile CRUD). No application-specific executor.
- [ ] `[must]` Admit the immutable Candidate to the existing skill engine with
  all supported data modes and delegated caller scopes. Record executed
  package/lock/data-root/caller identity; prove DEV/stable records and sources
  unchanged. Qualify same-named installed skills, external dependencies,
  long-running services, expiry/revocation and recovery. Unsupported data modes
  and remote forwarding fail closed; local owner CRUD is not this broader proof.

Exit/remaining work: Move independent migration/fixture and review checks into common acceptance, then qualify successive Beta replacement/loss consent, source publication and consumer install/update, real credentials and delegated readers/writers, all-room/background authority and node-status/recovery failures. About portability remains CG-06. Two repaired local journeys do not close these non-deferred obligations.

### BIP-29

- [ ] `[must]` **Cutover And Applications Product Proof**. Status: `blocked`.

Owner/dependencies: Builder + Core; depends on BIP-04, BIP-07 through BIP-18 and BIP-21.

Implementation boundary: Semantic generation is used, but the execution adapter remains legacy and no new uncontaminated Applications full-lifecycle proof is established.

Exit/remaining work: Shadow-compare matched routes, retain per-project rollback/immutable accepted revisions, then remove generic calls to legacy text/recipe compilation. Recreate Applications from scenario_default using ordinary requirements, with domain grading only after generation. Human acceptance and delivery additionally depend on BIP-28; archive compatibility only after its rollback window.

### BIP-30

- [x] `[must]` **Exclusive State Operands**. Status: `verified`.

Owner/dependencies: Core semantic provider contract.

Implementation boundary: Provider/canonical state operand alternatives and tagged-field compilation are implemented and covered by contract tests plus typed-generation evidence.

Exit/remaining work: Reopen on a concrete provider/canonical mismatch; broader state meaning, diagnostic aggregation and repairs remain BIP-11/BIP-13.

### BIP-31

- [x] `[must]` **Required Reference Inventory**. Status: `verified`.

Owner/dependencies: Core Prototype context + compiler.

Implementation boundary: Model context supplies exact requirement refs and object-aware operations, with provider constraints and omission/unrelated-binding regressions.

Exit/remaining work: This does not establish residual intent understanding or eliminate every first-call invariant gap (BIP-10/BIP-13).

### BIP-32

- [ ] `[could]` **Live Prototype Shadow Projection**. Status: `conditional`.

Owner/dependencies: Root progress + Builder transaction + Client draft renderer.

Implementation boundary: Root currently exposes bounded phase/patch metadata
and the client has a compatibility `/pageSchema` patch reducer. The target
graph-first stream contract is now documented, while canonical Preview changes
only after full validation. This is sufficient for progress visibility and
debug projection checks but cannot render authoritative model changes as they
arrive.

Exit/remaining work: Compare an authenticated, non-executable private-shadow
projection with the existing phase UI after BIP-11/BIP-12 provide semantic op
replay and graph-to-WebUI projection fixtures. Require complete typed semantic
operations, per-operation structural checks, explicit draft identity, terminal
full validation, atomic promotion and rollback. Measure first useful frame,
invalid frames, final equivalence and transport/token cost. Never stream raw
provider deltas or compatibility pageSchema patches into canonical Yjs, and do
not make this route a prerequisite without measured benefit.

### BIP-33

- [ ] `[could]` **Catalog Cursor Paging**. Status: `conditional`.

Owner/dependencies: Builder catalog + Client.

Implementation boundary: The current bounded catalog works; no qualifying threshold breach is established.

Exit/remaining work: Implement cursor paging only after the 5,000-match/response-size condition is measured; preserve global search/filter semantics. A false trigger leaves an explicit conditional task, not a completion.

### BIP-34

- [ ] `[could]` **Optional Visual Model Reviewer**. Status: `conditional`.

Owner/dependencies: Builder review + evaluation.

Implementation boundary: Deterministic browser evidence is available; autonomous visual repair remains distinct.

Exit/remaining work: After BIP-21, compare an opt-in additional visual model reviewer with the implementing agent's existing browser feedback, initially at most two targeted review iterations. Require measured benefit, exact evidence, calibrated judgments and ticket routing. This could is not the implementing Codex's permission to see screenshots. Automatic acceptance and unbounded visual loops remain deferred.

### BIP-35

- [ ] `[could]` **Confirmed Feedback Memory**. Status: `conditional`.

Owner/dependencies: Project memory + Builder context.

Implementation boundary: No qualified preference-learning path is established by this audit.

Exit/remaining work: Add inspectable provenance, confirmation, supersession/forgetting, user/subnet isolation and stage-specific retrieval with token accounting. Cross-application reuse requires approval; keep holdouts inaccessible.

### BIP-36

- [ ] `[could]` **Alternative Design Candidates**. Status: `conditional`.

Owner/dependencies: Builder evaluation.

Implementation boundary: No matched benefit/cost case is established.

Exit/remaining work: Compare two alternatives only for high-value ambiguity under an explicit budget. Sequential candidates can be evaluated first; parallel execution must wait for the separately deferred queue/topology design. Do not make large orchestration an implicit prerequisite.

### BIP-37

- [ ] `[could]` **Learned Capability Ranking**. Status: `conditional`.

Owner/dependencies: Capability retrieval + evaluation.

Implementation boundary: Deterministic retrieval quality must first be established in BIP-09.

Exit/remaining work: Learn only from attributable accepted traces and compare with deterministic fallback under equal budgets and anti-leakage controls. Parent roadmap context ranking remains a separate broader contract.

### BIP-38

- [ ] `[could]` **Governed Domain Packs**. Status: `conditional`.

Owner/dependencies: Domain pack registry.

Implementation boundary: Explicit Applications/Research compatibility packs exist, not a governed reusable distribution registry.

Exit/remaining work: Add explicit install/trust/attribution/version/removal contracts without implicit subject activation. Generic and held-out modes remain pack-free.

### BIP-D01

- [ ] `[deferred]` **Large Prototype Orchestration**. Status: `deferred`.

Owner/dependencies: Core planning.

Implementation boundary: Adaptive DAGs, shared contracts, work-unit recovery and scale evaluation remain outside the small lifecycle correction.

Exit/remaining work: Revisit only after the small lifecycle gate; no new deferral is introduced by this audit.

### BIP-D02

- [ ] `[deferred]` **Dedicated Test Workbench**. Status: `deferred`.

Owner/dependencies: Builder evaluation UI.

Implementation boundary: The dedicated adaos_tests/latest-run desktop remains deferred.

Exit/remaining work: Keep existing TEST-labelled projects and previews; future projection requires ENV_TYPE=dev, owned cleanup, exact evidence and truthful beta labels.

### BIP-D03

- [ ] `[deferred]` **Queues And Preview Leasing**. Status: `deferred`.

Owner/dependencies: Execution + topology.

Implementation boundary: Concurrent Builder work and preview leasing remain deferred.

Exit/remaining work: Sequential work reuses one owned preview; applications/revisions isolate evidence, not extra webspaces.

### BIP-D04

- [ ] `[deferred]` **Generated Client Renderer Code**. Status: `deferred`.

Owner/dependencies: Client.

Implementation boundary: Autonomous renderer-code generation during Prototype remains deferred.

Exit/remaining work: Use admitted component contracts and report genuine capability gaps.

### BIP-D05

- [ ] `[deferred]` **General Multi-Agent Orchestration**. Status: `deferred`.

Owner/dependencies: Execution.

Implementation boundary: General multi-agent orchestration remains deferred.

Exit/remaining work: Require matched quality and total-cost benefit over one orchestrated agent.

### BIP-D06

- [ ] `[deferred]` **Legacy Intent Reverse Engineering**. Status: `deferred`.

Owner/dependencies: Builder interpretation.

Implementation boundary: General intent extraction from arbitrary legacy code/screenshots remains deferred.

Exit/remaining work: Do not reinterpret this as blocking current bounded prototype review.

### BIP-D07

- [ ] `[deferred]` **Unbounded Visual Iteration**. Status: `deferred`.

Owner/dependencies: Builder review.

Implementation boundary: Unbounded autonomous visual iteration and automatic acceptance remain deferred.

Exit/remaining work: Human approval is not replaced by the bounded reviewer.

### BIP-D08

- [ ] `[deferred]` **Concurrent Semantic Editing**. Status: `deferred`.

Owner/dependencies: Collaboration.

Implementation boundary: Multi-user concurrent semantic editing remains deferred.

Exit/remaining work: Keep immutable revisions and explicit single-user authority.
