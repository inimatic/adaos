# Builder Intent-to-Prototype Roadmap

Status: active corrective roadmap. Small generic Prototype and DEV Automation
slices work; target ownership, repeatable reliability and installed lifecycle
acceptance remain incomplete.

Last reviewed: 2026-09-14.

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

This register contains 37 non-deferred packages (27 must, four should, six
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
| Automation | Frozen-Prototype implementation and independent local HTTP/browser plans, owned persistence and bounded attempt archives | Media external scope, repeated latency qualification, installed delegated roles |
| Evaluation | Public CLI, immutable reports and model-input archives, component and browser probes | All stand paths under one lifecycle runner, calibrated holdout and complete cost accounting |

## Current Sequence

1. Stabilize loaded runtime, Client action semantics and requirement provenance
   on retained applications: BIP-03, BIP-10, BIP-16, BIP-27.
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

Implementation boundary: Conversation and governed status projections exist; a worker's completed state is not independent Automation acceptance. A human-authored DEV Builder design specimen covers current work, separate decisions, history, context and existing Dev Tickets. Its local state transitions are not live workflow implementation or model-generation evidence.

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
- [ ] `[must]` Exercise the accepted live Builder on one isolated TEST
  application from conversational creation through Prototype, Automation,
  independent verification and local delivery; record actual limits separately
  from successful steps. Keep the single paired preview topology.
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

Implementation boundary: The AST-generated capability inventory detects drift, but reads handwritten registries/models; it does not generate them from one component contract.

Exit/remaining work: Publish the authoritative contract and derive Client registration/types, compiler/validator indexes, retrieval and docs. Require ABI impact reports, conformance fixtures and matched browser evidence for admitted components, not every future widget.

### BIP-09

- [ ] `[must]` **Capability Discovery And Gaps**. Status: `partial`.

Owner/dependencies: Core capability service + SDK/MCP.

Implementation boundary: Bounded search/get and typed gaps exist in slices. Discovery can return low-relevance results and metadata-only drill-down; not every Prototype request supplies the advertised retrieval tool.

Exit/remaining work: Admit contracts by shape/operation/authority before optional ranking; expose callable bounded read-only retrieval and distinguish unsupported capability, absent index and irrelevant matches. Evaluate retrieval on independent queries without subject rewrites.

### BIP-10

- [ ] `[must]` **Brief Interpretation And Requirement Provenance**. Status: `partial`.

Owner/dependencies: Core intent/Brief + Builder review.

Implementation boundary: capture/compile/merge preserve source evidence, but compile_prototype_brief still sets outcome, actors and entities to unknown and starts with empty assumptions/questions. Model-suggested external scope is not reliably distinguished from user obligations at the handoff.

Exit/remaining work: Implement constrained residual interpretation, semantic-ID deltas and material-only clarification (at most three questions). Distinguish user requirements, explicitly accepted additions, suggestions and external gaps. Generic UI approval must not silently accept speculative integration scope; test partial progress without a green full verdict.

### BIP-11

- [ ] `[must]` **Semantic Authority And Incremental Compilation**. Status: `partial`.

Owner/dependencies: Core semantic compiler + Client mappings.

Implementation boundary: Semantic-v2 multi-resource generation, typed links, structural state proof, source maps and lookup-only resources exist. This is not a total contract for the admitted component set or proof of one-way semantic authority on all managed edits.

Exit/remaining work: Complete the supported semantic ABI and requirement-to-runtime mapping; qualify empty-state reachability, granularity, typed gaps, reference/display capacity and incremental preservation. Retain atomic full-artifact promotion; no lossy fallback or forced collection screen for lookup-only data.

### BIP-12

- [ ] `[must]` **Deterministic Semantic Edits**. Status: `partial`.

Owner/dependencies: SDK semantic UI + Core compiler.

Implementation boundary: Bounded semantic operations and deterministic review transforms exist, alongside legacy renderer edits.

Exit/remaining work: Qualify rename/move/visibility/options through ordinary requests with zero model calls, semantic refs, unchanged unrelated behavior and the declared D0 latency target. Do not equate a literal WebUI patch with semantic cutover.

### BIP-13

- [ ] `[must]` **Scoped Repairs And Preservation**. Status: `partial`.

Owner/dependencies: Core semantic repair + Builder execution.

Implementation boundary: Binding/reference/state repairs, additive visibility, view-only repair, exact invariants and immutable-candidate checks are implemented. Latest local semantic tests pass; full-candidate fallback and complete independent-diagnostic coverage are not eliminated.

Exit/remaining work: Aggregate all independent findings before selecting scope; qualify fixture/value/binding repair, exact replay and unchanged accepted semantics on fresh repeated runs. Compare authoritative slices with full-context/full-regeneration controls before reducing payloads.

### BIP-14

- [ ] `[must]` **Context Routes And Budgets**. Status: `partial`.

Owner/dependencies: Core execution + provider adapter.

Implementation boundary: Semantic requests use typed output and stable/dynamic context separation; selected repairs are scoped. Effective implicit Root profiles, all-route budget enforcement and matched correction-context savings remain unqualified.

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

- [ ] `[must]` **Bounded Browser Review And Feedback**. Status: `partial`.

Owner/dependencies: Evaluation + Builder review/Dev Tickets.

Implementation boundary: DOM/screenshot/task probes exist, including actual media visibility. An integrated DOM/accessibility/screenshot review with bounded owning-layer feedback is not qualified.

Exit/remaining work: Attach exact source/renderer identity, run deterministic/browser checks first and permit at most two targeted review repairs. Convert unresolved platform findings to owning-layer tickets. The optional model reviewer is BIP-34, not an excuse to defer deterministic review.

### BIP-22

- [ ] `[must]` **Preview Topology And Deletion**. Status: `partial`.

Owner/dependencies: Core topology + Client commands.

Implementation boundary: Registered W -> W-dev -> optional W-dev-dev pairing and bounded cleanup were implemented; the historical webspace-per-case runner prose is superseded.

Exit/remaining work: Qualify idempotent deletion admission/completion, absence after reconnect/prewarm and transparent explicit preview recreation. Keep one preview per selected Builder; no new case/session hosts and no private cleanup bypass.

### BIP-23

- [ ] `[should]` **Source Checkpoints And Artifact Retention**. Status: `partial`.

Owner/dependencies: Developer SDK + Root source persistence.

Implementation boundary: Checkpoint validation and actual Automation source receipts exist. Invalid-source quarantine, sparse checkout hydration, attachment GC and durable scenario-checkpoint fault qualification are not all closed.

Exit/remaining work: Qualify version/task-bound durable acknowledgements and recovery without double version bumps; preserve exact source/task identity through transport failures. Add non-promotable diagnostic checkpoints, safe hydration and owner/revision-bound attachment reclamation. Preserve the must-level durable-checkpoint gate in builder-sdk-boundary.md.

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

### BIP-27

- [ ] `[must]` **Frozen Automation Cohort**. Status: `partial`.

Owner/dependencies: Builder + independent acceptance.

Implementation boundary: All seven retained local HTTP/browser workflows have passing evidence; six qualify the agreed DEV-local scope. Media passes locally but external notification remains unresolved, and intermittent runtime stalls remain.

Exit/remaining work: Resolve Media's requirement provenance/recipient/channel scope through BIP-10, not a fabricated notification. Repeat unchanged applications after BIP-03/BIP-16; distinguish local partial success from full acceptance. Do not regenerate accepted Prototype 002 or manually patch application source.

### BIP-28

- [ ] `[must]` **Installed Lifecycle And Delivery**. Status: `blocked`.

Owner/dependencies: Builder lifecycle + delivery + delegated authorization.

Implementation boundary: Local Trial/promotion mechanisms and historical Equipment/Builder receipts exist. The user paused Trial/publication; personal DEV rejecting delegated credentials does not qualify installed reader/writer behavior.

Exit/remaining work: After explicit resumption, qualify isolated Trial against its release lock, source publication, consumer install/update, retained records, real reader/writer use, human EN/RU compact/wide review and live projection identity. Cover node-status/recovery failures. This is blocked non-deferred work, not waived or silently deferred.

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

### BIP-33

- [ ] `[could]` **Catalog Cursor Paging**. Status: `conditional`.

Owner/dependencies: Builder catalog + Client.

Implementation boundary: The current bounded catalog works; no qualifying threshold breach is established.

Exit/remaining work: Implement cursor paging only after the 5,000-match/response-size condition is measured; preserve global search/filter semantics. A false trigger leaves an explicit conditional task, not a completion.

### BIP-34

- [ ] `[could]` **Optional Visual Model Reviewer**. Status: `conditional`.

Owner/dependencies: Builder review + evaluation.

Implementation boundary: Deterministic browser evidence is available; autonomous visual repair remains distinct.

Exit/remaining work: After BIP-21, evaluate an opt-in screenshot model with at most two targeted iterations, exact evidence and ticket routing. Automatic acceptance and unbounded visual loops remain deferred.

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
