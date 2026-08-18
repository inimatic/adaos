# AdaOS Research Fabric Roadmap

Status: domain roadmap for the proposed AdaOS Research Fabric.

Last reviewed: 2026-08-18.

This roadmap sequences the implementation of the
[AdaOS Research Fabric](research-fabric.md). TLP is the first transparent
reference case and conformance fixture for deterministic, assisted, and later
autonomous research. A PaperBench-like replication suite follows TLP to
measure autonomous performance on frozen external tasks. Neither case is a
reason to put domain-specific semantics into AdaOS core.

The next authoring iteration is governed by
[Research Compilation and Autonomous-Science Evaluation Program](research-compilation-and-sota-program.md):
TLP first calibrates the source-artifact-to-executable-experiment bridge under
matched typing ablations, then a multi-task suite determines whether the
result generalizes. The program target is measurable progress in AI-driven
science, not a positive TLP result.

## Outcome

A clean AdaOS node can install and activate a general research manager plus an
optional MLflow provider through the existing skill/scenario lifecycle, run a
locked and paired TLP study on the current or selected AdaOS member node, survive restart
and provider failures, and export a portable evidence bundle from which the
declared primary analysis can be reproduced.

The target then extends the same substrate so a user can open one shared
Research Workbench, create and focus a research Project plus its primary
direction skill through Builder SDK, add local manifested artifact groups,
develop and accept a typed Research Prototype with an LLM, create an explicit
Builder Development Session, let Codex adapt only its admitted targets,
publish the Project, and instantiate a governed Experiment Campaign.
With an explicit Research Mandate, the same project can run unattended through
exploration, bounded software adaptation, confirmation, ClaimSet, and
ResearchRelease. A later writer may derive a neutral draft essay from that
release, but external publication remains separately authorized.

TLP proves the mechanism without requiring a positive TLP result. The later
ResearchCompilerBench track and AdaOS Research Replication Benchmark (`ARRB`,
working names) supply frozen artifact-to-experiment and paper-replication
tasks, target-level rubrics, matched budgets, and comparable scores so
autonomous-science claims are measured rather than inferred from a single
demonstration.

## Priority and Maturity Rules

- `[must]`: blocks the proof gate of the current milestone;
- `[should]`: required before broad, repeated, or unattended use;
- `[could]`: useful experiment that must not delay the current gate;
- `[deferred]`: deliberately postponed until the named admission condition.

Checkboxes report only the exact statement beside them:

- `[x]` means the documented artifact or evidence exists at the stated
  maturity;
- `[ ]` means it is not complete or has not passed its evidence gate.

Priority is independent from maturity. Use the repository-wide progression:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

A schema draft is not an implemented capability. A successful run is not a
scientific conclusion. A local demo is not production acceptance.

## Planning Authority

1. [AdaOS Research Fabric](research-fabric.md) owns the research-domain
   boundary, invariants, provider roles, storage topology, and TLP reference
   acceptance.
2. This roadmap owns implementation order and evidence gates for the research
   framework.
3. [Governed Data-Driven Workflow Model Roadmap](governed-workflow-runtime-roadmap.md)
   owns the generic workflow metamodel and executor semantics. This roadmap
   owns the research workflow definition that consumes them.
4. [Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md)
   owns package, publication, activation, and rollback semantics. Research
   skills and scenarios consume that path without inventing another installer.
5. [Model Runtime Roadmap](model-runtime-roadmap.md) owns promoted model
   artifacts, model backends, and generic `ModelJob` direction. This roadmap
   owns trial/checkpoint evidence before model promotion.
6. MLflow and Ray provider roadmaps, if later split out, own provider-specific
   delivery details but cannot redefine the provider-neutral research model.
7. A future general storage-capability architecture may take ownership of
   relational/blob provisioning after the ARF conformance slice. Until then,
   ARF2 owns the minimum requirements proven by research.
8. The Issue Tracker owns concrete implementation runs and evidence links; it
   does not redefine the architecture or mark gates complete by itself.
9. [Builder](builder.md) and the
   [Builder Roadmap](builder-roadmap.md) own the generic Project, Prototype,
   Preview, Automation/Codex, Trial, Publication, source-capsule, and re-entry
   contracts. This roadmap owns the `research_direction` skill profile,
   pre-Codex formulation contracts, and scientific artifacts that consume them.
   [Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
   owns the cross-domain Project distribution definition, presentation
   resolution, Development Session, local artifact-context SDK, and registry
   classification boundaries used by both roadmaps.
10. The LLM provider, agent topology, and model profile are replaceable. This
    roadmap owns Research Mandate, autonomy, evaluation, and evidence semantics,
    not a provider-specific autonomous-agent loop.
11. [Research Compilation and Autonomous-Science Evaluation Program](research-compilation-and-sota-program.md)
    owns the scientific-problem-to-engineering bridge, stage visibility,
    controlled typing ablation, primary comparative endpoint, and evidence bar
    for any competitive claim. This roadmap owns their implementation order.

## Guardrails

- No `.adaos/research` top-level directory.
- No `adaos research ...` install/runtime command family.
- No direct access to MLflow or any future Ray private database schemas.
- No separately installed DBMS per integration.
- No claim that changing the current SQL connection string is PostgreSQL
  support.
- No retry that silently becomes another scientific sample.
- No confirmatory use of the test set before its workflow gate.
- No acceptance based only on a dashboard screenshot or notebook output.
- No promotion of TLP-specific entities into core.
- No autonomous action outside a signed Research Mandate, exact risk class,
  budget, data-access policy, and workflow gate.
- No use of one repeatedly observed holdout as fresh confirmatory evidence.
- No completion based on an agent's final message or self-review score.
- No direct mutation of installed packages by aResearcher; experimental-base
  changes use Builder/Codex and exact candidate releases.
- No generated scenario per research direction by default. The shared
  Research Workbench orchestrates direction skills; a custom scenario requires
  an explicit UI/workflow need.
- No research-management tab in Builder. Domain portfolio, intake,
  formulation, and acceptance belong to Research Workbench; Builder owns the
  linked development session.
- No MCP or external artifact store as an admission dependency for local
  pre-Codex work. Codex receives exact native paths under manifested
  `artifacts/partN` groups; later providers must preserve the same ArtifactRef.
- No inference of write authority from UI focus. Development targets and
  read-only context are explicit session policy.
- No automatic external paper, repository, or community publication in the
  initial A4 profile.
- No hidden evaluator or review requirement may be used to fail Codex unless
  the requirement was compiled into its visible accepted contract. Otherwise
  the failure belongs to formulation/evaluation design.
- No reporting of the historical review-assisted TLP path as a clean from-raw
  formulation result.
- No SOTA or cross-domain claim from one TLP task, one successful trace, an LLM
  self-score, or unmatched model/tool/compute budgets.

## Current Baseline

| Capability | Repository baseline | Assessment |
| --- | --- | --- |
| Skill/scenario lifecycle | Package, install, activate, service-skill supervision, A/B runtime buckets | useful implemented foundation |
| Workflow governance | Versioned governed workflow plus explicit Experiment aggregate and package-bound lifecycle | E002 completed review/lock/start/reconcile/finalize and survived package/API/Desktop reload |
| Core persistence | SQLite under `.adaos/state`; repositories remain SQLite-shaped | deliberately unchanged; legacy core repositories are outside ARF0.5 |
| Skill persistence | Versioned runtime `data/db`, `data/files`, and `data/internal` ownership | research-manager control metadata and TLP primary data have separate owner buckets and migrations |
| Database capability | Negotiated `storage.relational` requirement/binding, owner migrations, backup/restore, retention, lifecycle SDK, and SQLite provider; legacy `SQL` remains | ARF2 local contract validated; legacy repositories deliberately unchanged |
| PostgreSQL | Isolated database and least-privilege owner/login role per skill, bounded pools, health, credential refresh, backup/restore | ARF2 conformance validated locally on `postgres:16-alpine`; the login is exposed only to its owning service process |
| Artifacts/models | Generic `ContentRef`, checkpoint ABI, and portable research evidence manifest; model promotion rules unchanged | E002 fixed a verified result, tracker export, and eight content-addressed artifact references |
| Execution | Immutable spec/attempt/checkpoint ABI, bounded local process provider, and optional digest-pinned OCI provider | ARF3 core and E002 start/reconcile/result integration validated across restart |
| Tracking | Frozen contract `1.0`, bounded durable journal/outbox, local reference provider, conforming supervised/external MLflow adapter | identity, outage/replay, acceptance/deletion, storage binding, remote auth, UI proxy, and browser matrices validated locally |
| Distributed execution | Current/selected member-node execution through existing AdaOS semantics; no Ray provider | deliberately deferred until the complete local research loop is proven |
| Research domain | Versioned Study/Experiment/Protocol/Trial/Run/Attempt/Observation/Evidence/Claim contracts, runner-provider boundary, ResearchSpace owner projection, and governed workflows | reusable control plane is separated from domain runner/data ownership |
| Scenario guidance | Versioned README, modal binding, workflow-aware state/action projection, deterministic EN/RU text and voice intents | implemented first in `tlp_research`; cross-scenario rollout is incremental |
| TLP | E002 conditions, separate TLP runner/data-owner skill, real deterministic CPU runner, native Workbench, clean fixtures, sanitized exploratory provenance | Builder-published direction skill `0.1.5` passes bounded realization acceptance; clean from-raw research compilation, real-data workflow, and confirmatory scientific proof remain ARF7.3/ARF6 work |
| Research authoring | Shared Workbench portfolio/focus, atomic Project + direction-skill creation, direction-owned manifested artifacts, typed formulation, private local checkpoint, least-write Development Session, consumer-owned contract requirements, exact system specification, scope gate, and canonical preview | ARF7.2 validated locally on a review-assisted TLP handoff; the earlier `initial-review` performed part of the scientific-to-engineering bridge, so stage visibility, from-raw compilation, controlled typing ablation, ProjectRelease, and accepted-workflow execution remain open |
| Agent assistance/autonomy | Root LLM jobs, durable Builder Runs/context packets, governed workflows, conversations, and exact action admission exist | no Research Mandate, autonomy profile, autonomous campaign controller, agent budget ledger, or TLP autonomous proof |
| Scientific release | EvidenceBundle and ClaimDecision contracts exist | no ClaimSet synthesis projection, distinct ResearchRelease, external review loop, or writer input contract |
| Comparative evaluation | TLP supplies one internal transparent implementation case and hidden legacy oracle | no clean C0-C4 typing ablation, frozen multi-task ResearchCompilerBench/ARRB package, target rubric evaluator, matched-budget baseline, or benchmark release |

## Milestone Sequence

| Milestone | Outcome | Current maturity | Horizon |
| --- | --- | --- | --- |
| ARF0 | Architecture, ownership, exclusions, and reference case are discoverable | `specified` | now |
| ARF0.5 | Generic storage/binding/content/execution seams exist before research code | `validated-local` | now |
| ARF1 | Minimal research manager works with local storage, local tracking, and local execution | `validated-local` by E002 | complete locally |
| ARF2 | Relational storage is provisioned as a scoped capability with a PostgreSQL path | `validated-local`, including research-manager integration | complete locally |
| ARF3 | Logical runs and physical attempts are durable and provider-neutral | `validated-local`, including real CPU attempt integration | complete locally |
| ARF4 | MLflow is a conforming optional tracker service skill | `validated-local` | complete locally |
| ARF5 | Ray is a conforming optional executor service skill | `deferred` | resume only after the complete local/member-node loop |
| ARF6 | TLP passes the deterministic scientific and operational reference proof | `hypothesis` | next scientific gate |
| ARF7.0 | Builder-embedded precursor proves SourceBundle, ResearchPrototype acceptance, and AutomationBrief contracts | `validated-local` | technical precursor complete |
| ARF7.1 | Research Workbench creates/focuses a local research Project, admits local artifact groups, exposes provenance/coverage and a deterministic formulation review, and creates a scoped pre-Codex Development Session | `validated-local`, including strengthened TLP admission; authenticated browser reload receipt pending | active UX acceptance gate |
| ARF7.2 | Codex realizes the exact brief as a validated and published TLP direction skill | `validated-local`; direction-skill checkpoint published, ProjectRelease not yet created | complete locally |
| ARF7.3 | Clean from-raw research compilation is calibrated under matched C0-C4 TLP arms; the published direction then instantiates and runs the accepted local workflow | `hypothesis` | next integrated authoring/scientific gate alongside ARF6 |
| ARF8 | aResearcher completes a mandate-bound autonomous TLP loop through ResearchRelease | `hypothesis` | after ARF7 |
| ARF9 | ResearchCompilerBench/ARRB measures artifact-to-experiment compilation and replication on frozen multi-domain tasks and matched budgets | `hypothesis` | compilation track after ARF7.3; autonomous replication track after ARF8 |
| ARF10 | Multi-domain evidence, scale, security, and operations justify broader claims/core promotion | `deferred` | long-term |

Milestones are cumulative. TLP supplies fixtures and acceptance pressure from
ARF1 through ARF8; it is not postponed until one late integration phase. ARF9
changes the evaluation task, not the governance, Builder, execution, evidence,
or release contracts proven by TLP.

Delivery snapshot (2026-08-10): the published and locally activated packages
are `research_manager_skill` `0.9.0`, `tlp_experiment_skill` `0.1.1`,
`mlflow_tracker_skill` `0.2.2`, and `tlp_research` `0.3.3`. The accepted E002
result remains immutable from the earlier release. The current packages add
the runner/data-owner boundary, owner-qualified ResearchSpace projection,
bounded cross-skill tool invocation, data migration, and channel-neutral
scenario guidance. Versions `0.9.0`/`0.3.3` additionally validate safe
Markdown help, a persistent workflow-command footer, explanatory command
titles, an MLflow Model training deep link, and scenario-admitted semantic
actions so guidance does not propose controls absent from the current surface.
Native package installation, scenario validation, package self-tests, Desktop
rematerialization, migration, and guidance invocation all passed on the
reference machine.

Authoring precursor snapshot (2026-08-10):
`research_orchestrator_skill` `0.0.1` and Builder scenario `0.2.57` are
published locally; the skill is installed/active and the Desktop webspace was
rematerialized. The dynamic template catalogue includes `research_direction`.
Generic Builder upload created immutable SourceBundles, and the orchestrator
proved private formulation state, grouped LLM activity, candidate validation,
and exact optimistic human acceptance.

Operator review on 2026-08-11 reopened ARF7.1. At that point the research flow
was embedded in Builder instead of starting in a Research Workbench; generic Project
creation did not provide a clear template selection/validation experience;
skill preview retained an unrelated prior scenario; Open Preview and QR did
not share one complete development navigation destination; and direction
artifacts were not represented as native local Project context for Codex. These
were architecture/UX gaps, not evidence against the validated formulation ABI.

The local TLP proof attached the original 3.44 MB notebook and 35.7 KB review,
accepted SourceBundle
`sha256:e3dc926450ec58291a85242a5925c1d7743041c2c3ac6f448f8e8f85f60e43e7`,
rejected earlier structurally insufficient LLM candidates, then replayed the
human-reviewed content through the published orchestrator and accepted
ResearchPrototype revision 1
`sha256:18cbbbe33b1755328762f0ddd73e11650281e702809326c8c84d81b1d25d0578`.
The resulting AutomationBrief is
`sha256:1b1a149c841a6598b9d58ccad1f0fe800117b1ffc4252e49f7348efb28b48c29`;
repeated acceptance preserved generation 3 and `codex_started=false`. Native
AdaOS source tests, strict skill validation/tool probes, package tests,
scenario validation, and the full Ionic/Angular client build passed on this
machine. Those facts closed ARF7.0 only. The subsequent ARF7.1 proof is recorded
in `research-project-pre-codex-walkthrough.md` and supersedes the former
Builder-embedded operator path without erasing its contract evidence.

ARF7.1 delivery snapshot (2026-08-11): the published and activated authoring
set is `research_orchestrator_skill` `0.1.0`, `builder_sdk_control_skill`
`0.1.59`, `research_workbench` `0.0.1`, `skill_preview` `0.0.1`, and Builder
`0.2.59`. The released orchestrator reconstructed the accepted TLP handoff
through its public tools without copying the private DEV database. Exact
digests, session scope, navigation destinations, registry commits, and test
commands are recorded in
[Research Project pre-Codex walkthrough](research-project-pre-codex-walkthrough.md).

ARF7.1 operator hardening (2026-08-11) makes portfolio and selected-direction
detail independent full-width Workbench layouts. New direction creation is a
reset-on-success modal, canonical selection occurs from the refreshed
portfolio, and Workbench focus is no longer derived from delayed Builder
context. Discussion is the explicit artifact-aware human/LLM consensus tab.
Builder opens in a reusable named window and consumes a canonical
`builder.context.selected` event projected by the API host rather than relying
on the isolated skill process to own live UI state. Its renderer tolerates
legacy singleton collection shapes without losing the project header or
project-picker contents. The hardened published set is
`research_orchestrator_skill` `0.2.0`, `research_workbench` `0.0.3`, Builder
`0.2.60`, and AdaOS client `0.0.310`. These are ARF7.1 usability and
identity-consistency requirements, not ARF7.2 Codex realization.

ARF7.1 reliability and review hardening (2026-08-11) makes artifact intake a
bounded `local_write` declared in the released tool manifest, so a normal
Workbench upload does not enter the destructive/filesystem approval class.
The generic approval gate now publishes through the asynchronous live-room
owner; genuinely high-risk operations therefore appear in the global Pending
Actions surface instead of failing with
`sync_get_ydoc_live_room_requires_owner_handoff`. Discussion uses a split
focus-detail layout: chat remains primary and a compact, typed consensus
projection derived from the current ResearchPrototype or accepted
AutomationBrief remains visible alongside it. Direction diagnostics move to a
title-adjacent Details modal. Manifested Markdown/text/PDF artifacts open in a
bounded modal; PDF bytes are streamed by an authenticated Builder artifact
endpoint and native paths never enter the browser contract. Builder handoff
combines the canonical Yjs selection with a declared one-shot URL address, so
the exact direction skill is visible on first paint without making query state
authoritative. Route tunnelling no longer invents the historical `8788`
fallback; it uses runtime topology/state candidates only.

ARF7.1 runtime data-route hardening (2026-08-12) used the slow/false-empty
Workbench portfolio as a core acceptance test. The defect was systemic rather
than a Workbench-private transport problem: every tool call used POST and was
therefore gated as a mutation; lifecycle 503 responses were then amplified by
timer retries, and a terminal failure was rendered as a valid empty list. The
core now routes validated `tool/details` reads through `live_reads`, verifies
the hint against trusted active-manifest side effects at the execution node,
waits for lifecycle capabilities by event, executes last-value/rate policy,
and exposes stale/unavailable/error separately from domain data. Research
Workbench opts into strict manifest/WebUI policy conformance. See
[Runtime Data Route Reliability](runtime-data-route-reliability.md) and its
[2026-08-12 evidence receipt](runtime-data-route-reliability-evidence-2026-08-12.md).

ARF7.1 formulation/layout hardening (2026-08-12) reopens the scientific-quality
portion of the gate without undoing the already proven Project/artifact/session
mechanics. `webui.v1` now has state-selected full-surface `layout.variants`;
page state is scoped by webspace/scenario/page; loose null comparisons have
explicit nullish semantics; desktop and modal renderers consume the same layout
plan. Workbench uses portfolio and direction variants, so an absent/staged
selection deterministically falls back to the portfolio instead of showing an
empty detail view. The shared Markdown renderer normalizes CRLF before legacy
formula delimiter recognition and the exact imported TLP equation is covered
by a KaTeX regression.

The artifact SDK now parses notebook source cells before bounding, omits raw
output payloads, emits stable fragment refs, and discloses coverage. The later
semantic-preparation pass may retain bounded output summaries only as explicitly
exploratory/untrusted context. ResearchPrototype
1.1 records core-owned `context_coverage`, source-grounded claims, typed paired
design/inference/requirement/check structures, and an unforgeable deterministic
`admission_review`. Bounded LLM repair now receives semantic quality failures;
exhausted repair remains a visible draft. The existing `tlp_research_03`
revision is intentionally not grandfathered into readiness: it lacks the new
contract and remains insufficient for autonomous Codex until reformulated.

ARF7.1 strengthened TLP admission receipt (2026-08-12): ordinary Root-LLM
drafts for `project:tlp_research_03` were retained but rejected when they
promoted hypotheses to observations, omitted predeclared paired units, copied
template placeholders, or lacked execution/data/reproducibility/observability/
evidence coverage. Provider-native JSON output, bounded repair payloads, and
conservative shape normalization improved recovery without granting the model
admission authority. A human-reviewed revision 5 then passed all 23
deterministic checks. It binds SourceBundle
`sha256:c1bd548e2e8a2cdfa661263aea5962bab72f823b49f33349612f7d01762092a4`,
ResearchPrototype
`sha256:50d83bb5896697ca112925a6931436a858eb9afc4690737a2d0f4e3d7000c47c`,
AutomationBrief
`sha256:8679e07b69980cb95b614b425dbb13baf5c409b3648f61fa11544bc89aacc2d7`,
and ready Development Session `dev_tlp_research_03_8679e07b69980cb9`.
The direction is `handoff_ready`, generation 9; Codex has not started and no
scientific run was created.

The same pass exposed an ambiguity in the ordinary lifecycle CLI: `--local`
selects the execution location but the default install source remains the
published registry. Core now adds the explicit, general
`skill install NAME --source workspace` path, validates that exact tree, runs
its tests, and prepares/activates it without a registry sync. The reference
skill `0.10.2` passed this path in slot A. The state-selected layout and CRLF
KaTeX regression passed 33 focused browser tests and a clean production build;
the deployed live client is `0.0.329+6aee45f`. An isolated headless browser
could not complete the final authenticated portfolio/back/reload receipt
because it had no WebAuthn session and the pairing websocket returned 404.
That remaining operator check is recorded explicitly below rather than being
reported as a layout success.

ARF7.1 source/inference hardening receipt (2026-08-14) treats formulation as an
evaluated pipeline rather than one schema-shaped answer. The generic artifact
SDK now turns notebook Markdown/code, imports, definitions, literal config,
near-duplicate revisions, relevant code windows, and bounded historical-output
summaries into query-selected provenance units before applying the context
budget. Historical summaries remain exploratory/untrusted. Plain text uses the
same selection/coverage envelope; PDF page/section/OCR extraction is the next
adapter on that boundary, not a separate prompt path.

The published orchestrator decomposes formulation into typed `problem_frame`,
`protocol_design`, and `implementation_contract` artifacts. Every stage keeps
input/output/schema digests, exact payload, all Root job attempts, resolved
provider/model, aggregate usage, and bounded stage-local repair. A portable
provider schema is projected from the richer local contract. Protocol decisions
explicitly resolve early questions as source-derived, policy-default, proposed,
or unresolved; only unresolved decisions block automation. AdaOS compiles refs,
ids, readiness, user-facing lifecycle text, checkpoint selection, and interval
decision logic from typed semantics.

The controlled TLP acceptance direction `tlp_formulation_eval_01` used the same
original 3.44 MB notebook and review across iterations. The globally routed
`gpt-4o-mini` path either timed out/fell back or produced an unsafe protocol
(one confirmatory seed, outcome-dependent stopping, invalid pairing), showing
that schema-shaped output was not an adequate model-selection criterion. The
Root `development` profile resolved to `gpt-5`. Successive real outputs exposed
and drove fixes for stale question propagation, contradictory two-sided
decision prose, and per-epoch final-test leakage. Final run
`formulation-tlp_formulation_eval_01-7-97644b6085` completed all three native
Structured Output stages with zero repairs, 37,503 aggregate tokens, no provider
fallback, and produced admitted ResearchPrototype revision 5
`sha256:55cf7344346e2ac68132a30ffbb5e5038b3452a95814b0a2108471de63619960`.
All 24 deterministic checks pass. The plan has no blocking questions, compiles
a coherent two-sided practical-equivalence decision at 0.5 percentage point,
uses 10 paired confirmatory seeds, separates validation selection from sealed
one-shot test access, and contains no per-epoch final-test obligation. It is
ready for human acceptance and bounded Codex implementation, not accepted by
the model and not evidence that TLP works.

ARF7.2 realization receipt (2026-08-16): the accepted
`project:tlp_research_03` handoff was exercised through ordinary Builder
Automation, an isolated local Codex worker, trusted scope/test/package
finalization, DEV activation, and the Forge checkpoint path. No target source
was edited outside Builder. The five Builder tasks were deliberately retained
as evidence rather than collapsed into a false one-shot success:

- `0.1.1` passed superficial generation while remaining a stub;
- the next candidate exposed undeclared heavy-dependency isolation;
- `0.1.3` passed target-owned tests but did not match the real manager result
  paths, exact source architecture, dataset acquisition, or crash semantics;
- the corrected candidate exposed a core installer defect that reserved disk
  for Torch even though the selected shared interpreter already satisfied the
  graph, plus source-fidelity and archive-streaming defects found by an
  independent audit;
- task `task.01M03VG08VK5565HMHN62TMPEM` produced `0.1.5`, which passed the
  trusted finalizer and independent native acceptance.

The final direction skill declares and exports the four real
`adaos.research.runner.v1` operations consumed by `research_manager_skill`,
uses the notebook-grounded `3 -> 32 -> 64 -> 128` architecture with the sole
pool2 intervention, identical Adam `lr=0.001`/no-scheduler training policy,
paired initialization and random streams, three-epoch non-inferential smoke
and fixed ten-seed confirmatory profiles, real checkpoint failure/resume,
manager-normalized observations/results, digest-bound environment and
artifacts, and a hard test seal. STL-10 acquisition hashes incrementally and
streams only `train_X.bin`/`train_y.bin` from a path-safe archive; it never
materializes test, unlabeled, or fold content.

The independent machine receipt is: strict DEV validation with tool probing
passed; seven runtime package tests passed inside the native lifecycle budget;
`dataset_status` honestly reported `ready=false`, sealed test data, and zero
test access because no real dataset was downloaded or scientific run started.
The published skill checkpoint is Forge commit
`954ce66745f4e035b9b7fb8e95d7e6f0dfbb776c`, package digest
`sha256:447ad35bbd78ec51ffba97e417da6e9c107a10fb14d1013d46be7d0478a3465a`,
active DEV version `0.1.5`. Builder publication remains `not_started`: this
receipt closes direction-skill realization and validation, not ARF7.14
ProjectRelease, ARF7.3 real-data execution, or ARF6 scientific evidence.

The failures also hardened the generic rails. AutomationBrief now projects
consumer-owned contract requirements; ResearchPrototype projects an exact
source-grounded system specification; core validation checks required provider
operations, dependency declarations, and heavy-runtime policy; installation
reuses an already satisfying shared distribution graph before applying disk
budget; and Codex receives a bounded prompt projection while the exact context
packet remains the audit artifact. Future isolated workers are explicitly told
to leave install/activation/publication to the trusted finalizer and to keep
native suites within their lifecycle budget.

Readiness update (2026-08-08): E002 completed the packaged three-epoch STL-10
CPU run, immutable result fixation, independent artifact verification,
published-package reinstall, repeated service restart, AdaOS API restart, and
Desktop snapshot reload. This accepts ARF1 through ARF3 locally. Tracker
contract 1.0, the local/MLflow conformance suite, process-only relational/blob
bindings, authenticated external service binding, governed UI proxy, and
Chrome iframe tests complete the ARF4 local gate. ARF6 remains the independent
scientific proof.

## ARF0. Architecture and Decision Baseline

**Outcome:** one authoritative target design and one sequencing owner describe
the research framework without claiming implementation.

**Exit gate:** the documents are in navigation and the authority map, name
current limitations honestly, and preserve existing AdaOS lifecycle and state
ownership.

- [x] `[must]` `ARF0-01` Define the Research Fabric target architecture,
  provider boundaries, scientific invariants, and TLP reference acceptance in
  [AdaOS Research Fabric](research-fabric.md).
- [x] `[must]` `ARF0-02` Adopt `AdaOS Research Fabric` as the working
  architecture name and reserve `aResearcher` for a future product/assistant
  surface; defer final public identifiers until the first package slice.
- [x] `[must]` `ARF0-03` Record that research uses existing skill, service
  skill, scenario, governed-workflow, package, and activation paths without a
  research-specific CLI or `.adaos/research` tree.
- [x] `[must]` `ARF0-04` Define MLflow as an optional typed tracker and Ray as
  a deferred optional executor, with AdaOS retaining protocol, workflow, and
  evidence authority and member-node execution sufficient for the current
  loop.
- [x] `[must]` `ARF0-05` Define SQLite as the local default and PostgreSQL as a
  future scoped capability/provider rather than one DBMS per component.
- [x] `[must]` `ARF0-06` Register the architecture and roadmap in the
  architecture navigation and planning authority map.
- [x] `[should]` `ARF0-07` Record the preliminary TLP notebook limitations and
  the clean-room protocol requirements without treating historical outputs as
  confirmatory evidence.
- [x] `[must]` `ARF0-08` Keep autonomous implementation behind deterministic
  workflow and evidence gates while allowing the target mandate, evaluation,
  and release contracts to be designed early.

## ARF0.5. Core Readiness

**Outcome:** research code starts on narrow provider-neutral core seams rather
than creating private database, job, endpoint, and artifact abstractions.

**Admission gate:** ARF0 is complete.

**Exit proof:** packaged ABI validates, two active skill contexts cannot see or
reuse each other's relational bindings, the SQLite provider runs from the
public SDK, a live PostgreSQL provider passes the same minimal data contract,
and a local execution attempt is idempotent and reconcilable after provider
object restart.

The implementation and convergence inventory are recorded in
[Research Fabric Core Readiness](research-fabric-core-readiness.md).

- [x] `[must]` `ARF05-01` Inventory legacy `SQL`, skill data paths,
  `ResourceTicket`, `Process`, `OperationManager`, workflow activities,
  sandbox execution, artifact kinds, and ModelJob direction with an explicit
  keep/converge disposition.
- [x] `[must]` `ARF05-02` Publish versioned ABI and Python contracts for
  relational requirements/bindings, `ContentRef`, `ServiceBinding`,
  `ExecutionSpec`, and physical `ExecutionAttempt`.
- [x] `[must]` `ARF05-03` Implement `storage.relational` as an SDK-accessible
  requirement/binding capability that uses the existing capability gate,
  derives the owner from the active skill context, and never accepts an owner
  or physical path from skill code.
- [x] `[must]` `ARF05-04` Enforce one private binding per skill/logical name,
  migration-owner equality, stale-handle rejection after context switch,
  redacted locators, and fail-closed provider negotiation.
- [x] `[must]` `ARF05-05` Implement the SQLite provider under the owning
  compatibility bucket's `data/db` and validate isolation, rollback, named
  parameters, JSON probing, unsupported requirements, and path traversal on
  this machine.
- [x] `[must]` `ARF05-06` Implement the PostgreSQL provider behind a core-owned
  administrator secret/URL with one isolated logical database per binding and
  an environment-gated destructive-safe conformance test.
- [x] `[must]` `ARF05-07` Run the PostgreSQL conformance test against a live
  local/test server and retain the result as acceptance evidence. The
  2026-08-07 local run passed against `postgres:16-alpine` with `psycopg` and
  removed the exact isolated test database/container afterward.
- [x] `[must]` `ARF05-08` Define cross-skill sharing through a specialized
  provider skill and typed API/projection/logical-view contracts, never by
  lending one skill's SQL binding to another.
- [x] `[must]` `ARF05-09` Implement an owner-scoped local execution provider
  with deterministic idempotency, atomic receipts, stdout/stderr content refs,
  cancellation, wall timeout, lost-state semantics, and restart
  reconciliation.
- [x] `[must]` `ARF05-10` Reject CPU, memory, GPU, secret, and working-directory
  requirements that the local provider cannot enforce; keep the process
  adapter explicitly outside the hostile-code trust boundary.
- [x] `[should]` `ARF05-11` Bind governed workflow activity dispatch to the
  executor port and generic operation reference without replacing or
  duplicating `OperationManager`. The generic activity adapter now submits an
  immutable execution spec and returns separate workflow, attempt, and
  operation references.
- [x] `[should]` `ARF05-12` Define provider health/status projections and
  protocol-version negotiation for relational and execution bindings. Both
  advertise protocol `1.0` and redacted feature/status records.
- [x] `[deferred]` `ARF05-13` Do not migrate existing core SQLite repositories,
  add cross-skill SQL grants, implement MLflow/Ray, or introduce research-domain
  entities in this preparation milestone.
- [x] `[should]` `ARF05-14` Replace the baseline `core` capability fallback
  with authoritative manifest/profile-driven per-skill admission. A profile
  may narrow but cannot invent a capability, and binding ownership remains
  independent of admission.

## ARF1. Local Research Kernel and TLP Skeleton

**Outcome:** a minimal research-manager skill can describe and govern a study
end to end using only local providers.

**Admission gate:** ARF0.5 `[must]` items are complete, including live
PostgreSQL conformance, or an explicit local-only exception is recorded for an
ARF1 development slice that makes no PostgreSQL support claim.

**Exit proof:** from the TLP Desktop scenario, without using CLI as the normal
operator path, a user creates one experiment within a study, edits and reloads
its versioned conditions, locks them, starts bounded execution, observes and
cancels/reconciles attempts, inspects typed results, and finalizes an immutable
result/evidence record that survives AdaOS and browser restart. Contract tests
remain necessary but are not sufficient for this exit.

- [x] `[must]` `ARF1-01` Define versioned schemas for `Study`, `Hypothesis`,
  `Protocol`, `AnalysisPlan`, `TrialGroup`, `Trial`, `Run`,
  `ExecutionAttempt`, `Observation`, `EvidenceBundle`, and `ClaimDecision`.
- [x] `[must]` `ARF1-02` Define immutable identity and digest rules, including
  protocol, analysis plan, code/package, environment, dataset, split,
  operator, trial, run, attempt, and evidence identities.
- [x] `[must]` `ARF1-03` Define protocol amendment semantics that create a new
  version, preserve lineage, and explicitly invalidate or retain prior trials.
- [x] `[must]` `ARF1-04` Implement the research lifecycle as a package-bound
  governed workflow with review, lock, smoke, execution, QC, test-unblind,
  analysis, and claim-review gates.
- [x] `[must]` `ARF1-05` Implement a research-manager service skill through the
  existing skill lifecycle; keep mutable state in its versioned runtime data
  area.
- [x] `[must]` `ARF1-06` Implement a minimal local tracker that satisfies the
  initial typed tracker contract without requiring MLflow.
- [x] `[must]` `ARF1-07` Implement a local fixture executor sufficient to prove
  scientific run identity separately from physical attempt identity.
- [x] `[must]` `ARF1-08` Define named RNG stream descriptors for initialization,
  data ordering, augmentation, operator initialization, and analysis; reject
  undeclared stochastic inputs in confirmatory mode.
- [x] `[must]` `ARF1-09` Implement sealed test bindings and auditable unblind
  transitions. Validation and robustness suites must not alias the sealed test
  source accidentally.
- [x] `[must]` `ARF1-10` Define a portable, versioned evidence manifest with
  content-addressed references and a deterministic verification command or SDK
  operation.
- [x] `[must]` `ARF1-11` Package a TLP study scenario skeleton with protocol,
  analysis-plan, trial-matrix, and evidence fixtures; do not copy executable
  notebook state into the scenario.
- [x] `[should]` `ARF1-12` Add schema migration fixtures and backward/forward
  compatibility policy for research-manager runtime buckets.
- [x] `[should]` `ARF1-13` Add property/model-based tests for illegal workflow
  transitions, stale generations, duplicate commands, amendments, and evidence
  finalization.
- [x] `[could]` `ARF1-14` Import selected notebook cells as explicitly marked
  exploratory provenance artifacts after sanitization and digesting.
- [x] `[must]` `ARF1-15` Define `Study 1:N Experiment` explicitly. An
  experiment owns versioned conditions, lifecycle, trials, runs, attempts,
  results, and fixation state; a study remains the research-question/series
  container.
- [x] `[must]` `ARF1-16` Provide a Desktop editor for one experiment's typed
  dataset/split, operator, seed, determinism, resource, budget, and analysis
  conditions with optimistic revision checks and immutable history.
- [x] `[must]` `ARF1-17` Provide review/lock, start, cancel, retry, and
  reconcile actions through declared skill tools and the execution provider;
  do not bypass protocol, sealed-test, or evidence gates.
- [x] `[must]` `ARF1-18` Show durable experiment state, trial/run/attempt
  progress, failures, logs/artifact references, paired metrics, and result
  summaries in the scenario.
- [x] `[must]` `ARF1-19` Finalize an immutable, verifiable experiment result
  and evidence reference from the scenario, then prove reload after skill,
  AdaOS, and browser restart.
- [x] `[must]` `ARF1-20` Execute at least one bounded non-mocked TLP smoke
  experiment from a clean package. The deterministic no-training fixture may
  remain a conformance test but cannot be the sole operator acceptance run.

Acceptance evidence (2026-08-08): `research_manager_skill` owns the
versioned research schemas, checksum-pinned migrations, local tracker,
event-derived lifecycle, sealed split/unblind audit, deterministic fixture,
and content-addressed evidence verifier. `tlp_research` supplies a
package-bound governed workflow plus protocol, analysis, trial, evidence, and
sanitized exploratory-provenance fixtures. Strict tool probing, workflow
compilation, six isolated skill tests, five scenario tests, restart
rehydration, and the native package lifecycle validate the implemented
components. E002 closes `ARF1-15` through `ARF1-20`: its two first-attempt CPU
runs produced 24 typed observations, eight artifact references, a fixed
result, and a normalized tracker export. Verification remained `ok` after
installing `research_manager_skill` 0.5.0 into a new compatibility bucket and
restarting AdaOS; the live Desktop retained `scenario:tlp_research` and its
E002 Workbench. No notebook output is promoted as confirmatory evidence, and
the E002 metric delta is explicitly workflow-validation evidence only.

## ARF2. Relational Storage Capability

**Outcome:** components request relational persistence by requirements and
receive isolated bindings; provider lifecycle and schema ownership are
explicit.

**Admission gate:** ARF0.5 has exposed concrete provider requirements and ARF1
has supplied real research-manager migration/lifecycle pressure.

**Exit proof:** the research-manager and tracker conformance fixtures run
against provisioned SQLite bindings; the same binding contract provisions an
isolated PostgreSQL test scope and passes migration plus backup/restore tests
without cross-owner table access.

- [x] `[must]` `ARF2-01` Inventory current `SQL`, SQLite repository, path,
  transaction, migration, and backup assumptions before changing the port.
- [x] `[must]` `ARF2-02` Write the provider-neutral storage decision record
  for `RelationalStorageRequirement`, `RelationalBinding`, provider readiness,
  secret references, ownership, and lifecycle.
- [x] `[must]` `ARF2-03` Include durability, transaction, concurrent-writer,
  JSON, locality, capacity, backup/restore, retention, migration-owner,
  rollback, and role requirements in the binding contract.
- [x] `[must]` `ARF2-04` Implement a local SQLite provider that places
  component-owned files or binding metadata under the owning skill's
  `data/db`, with safe locking and atomic initialization.
- [x] `[must]` `ARF2-05` Make schema migrations owner-supplied, versioned,
  idempotent where required, staged before activation, and covered by rollback
  or restore policy.
- [x] `[must]` `ARF2-06` Keep credentials behind AdaOS secret references and
  expose only scoped bindings to the owning component.
- [x] `[must]` `ARF2-07` Add provider conformance tests for isolation,
  transactions, contention, migration failure, disk exhaustion, backup,
  restore, and deletion/retention policy.
- [x] `[should]` `ARF2-08` Implement a PostgreSQL provider using one
  operator-managed cluster per deployment profile and isolated logical
  database/schema plus role per migration owner.
- [x] `[should]` `ARF2-09` Add PostgreSQL health, connection-pool limits,
  credential rotation, backup/restore, and upgrade evidence to the service
  lifecycle.
- [x] `[should]` `ARF2-10` Run research-manager and local-tracker repository
  tests against both SQLite and PostgreSQL; document any deliberately
  unsupported dialect behavior.
- [x] `[could]` `ARF2-11` Define a companion blob/object-storage requirement
  and binding for large immutable evidence and checkpoints.
- [ ] `[deferred]` `ARF2-12` Do not migrate all existing core SQLite
  repositories until provider-neutral repository boundaries and measured
  concurrency or operations needs justify it.
- [ ] `[deferred]` `ARF2-13` Do not offer one shared writable SQL schema across
  core, research manager, MLflow, and other skills.

Implementation evidence (2026-08-07): the public SDK derives the current
skill owner and returns a redacted binding with its negotiated requirements.
SQLite and PostgreSQL share migration, transaction, backup/restore, health,
retention, and deletion semantics while rejecting unsupported requirements.
PostgreSQL uses an isolated database and no-login owner role per skill, bounded
pools, and an operator-only credential refresh path. The full focused suite
passed against `postgres:16-alpine`, including research repository/tracker
migrations and backup/restore; the exact test databases and container were
removed afterward. TTL is deliberately rejected because no retention
scheduler exists yet. Deferred repository migration and shared-schema items
remain intentionally unchecked.

## ARF3. Durable Execution and Local Provider

**Outcome:** AdaOS can distinguish a scientific run from provider attempts and
reconcile long-running work without inventing a second workflow authority.

**Admission gate:** ARF1 contracts exist; ARF2 supplies durable local bindings
or an explicitly temporary compatibility repository.

**Exit proof:** a local training fixture can be submitted idempotently, report
heartbeats/logs/observations, checkpoint, be interrupted across AdaOS restart,
reconcile an unknown outcome, resume as a new attempt of the same run, and
cancel with an auditable terminal state.

- [x] `[must]` `ARF3-01` Align `ExecutionSpec`, `Run`, `ExecutionAttempt`,
  `ResourceRequest`, lease, heartbeat, cancellation, checkpoint, and failure
  contracts with governed workflow activity semantics and the model-job
  direction.
- [x] `[must]` `ARF3-02` Define immutable entrypoint, package, input, output,
  environment, resource, network, secret, and determinism fields in an
  execution specification.
- [x] `[must]` `ARF3-03` Implement submission idempotency and an explicit
  `unknown` outcome that requires provider reconciliation before retry.
- [x] `[must]` `ARF3-04` Persist provider binding, status history, heartbeat,
  logs, failure classification, cancellation handshake, timestamps, and
  resource observations per attempt.
- [x] `[must]` `ARF3-05` Preserve logical run/trial identity across
  infrastructure retries; record when policy intentionally requests a new
  scientific sample.
- [x] `[must]` `ARF3-06` Define checkpoint manifests with parent digest,
  producer attempt, code/environment compatibility, RNG state, and resume
  policy.
- [x] `[must]` `ARF3-07` Implement a local provider adapter with bounded CPU,
  memory, wall time, logs, cancellation, and declared outputs.
- [x] `[must]` `ARF3-08` Add recovery and fault-injection tests for crash before
  and after submit, lost heartbeat, duplicate callback, partial artifact,
  cancellation race, and AdaOS restart.
- [x] `[should]` `ARF3-09` Add accelerator inventory and allocation contracts
  for GPU count/type, memory, exclusivity, and readiness without leaking a Ray
  object into skill APIs.
- [x] `[should]` `ARF3-10` Add a stronger container/OCI-backed provider before
  running third-party or agent-generated code unattended; document the process
  runner as non-hostile isolation.
- [x] `[should]` `ARF3-11` Define admission budgets for attempts, compute time,
  storage, and monetary cost with fail-closed enforcement.
- [x] `[could]` `ARF3-12` Add preemption-aware scheduling once checkpoints and
  attempt identity are proven locally.

Implementation evidence (2026-08-07): immutable spec, attempt, checkpoint,
resource, network, determinism, budget, accelerator inventory/allocation, and
preemption contracts are exposed through the skill SDK. The local provider
persists receipts and status history, enforces attempt/CPU/memory/wall/log/
storage/compute budgets, verifies declared outputs, and reconciles ambiguous
outcomes before retry. Fault tests cover restart, duplicate submission and
callback, missing/partial output, heartbeat loss, cancellation race, and
preempted-run resume without changing scientific run identity. The optional
OCI adapter requires a digest-pinned image and rejects unsupported network and
secret handling; the process provider remains explicitly non-hostile.

## ARF4. Tracker Contract and MLflow Provider

**Outcome:** MLflow can be installed and activated as an ordinary service skill
and swapped with the local tracker without changing study semantics.

**Admission gate:** satisfied locally by E002 for ARF1 through ARF3.

**Exit proof:** one paired fixture produces equivalent normalized observations
and evidence exports with the local tracker and MLflow. MLflow may be stopped,
restarted, and reconciled without losing or silently accepting confirmatory
observations.

- [x] `[must]` `ARF4-01` Freeze the provider-neutral tracker contract for
  experiment/run registration, parameters, metrics, tags, artifact refs,
  finalization, query, export, health, and provider links.
- [x] `[must]` `ARF4-02` Define normalized metric identity including name,
  value type, unit, split, step/epoch, aggregation, timestamp role, producer
  attempt, and provenance.
- [x] `[must]` `ARF4-03` Package an `mlflow-tracker` service skill using the
  existing install, activate, status, restart, and rollback lifecycle.
- [x] `[must]` `ARF4-04` Use MLflow's supported REST API or SDK only; prohibit
  direct queries and migrations against MLflow backend tables.
- [x] `[must]` `ARF4-05` Map AdaOS study, protocol, trial-group, trial, run,
  attempt, source, environment, data, trace, and evidence identities to
  documented MLflow tags/artifacts.
- [x] `[must]` `ARF4-06` Start the local provider with backend metadata under
  the service skill's `data/db` and artifacts under `data/files`; do not add a
  top-level research or MLflow directory.
- [x] `[must]` `ARF4-07` Implement bounded buffering, backpressure, duplicate
  handling, degraded status, flush, and explicit terminal failure for required
  observations.
- [x] `[must]` `ARF4-08` Export all evidence-required MLflow data into a
  versioned, content-addressed AdaOS evidence bundle and verify the export
  independently of the live server.
- [x] `[must]` `ARF4-09` Add tracker conformance tests covering ordering,
  duplicate steps, retries, missing provider, restart, large artifact,
  finalization, export, and deletion after evidence acceptance.
- [x] `[should]` `ARF4-10` Support a provisioned PostgreSQL backend binding and
  a provisioned object/blob artifact binding without exposing either schema to
  the research manager.
- [x] `[should]` `ARF4-11` Support externally managed MLflow through an
  authenticated service binding and capability/version probe.
- [x] `[should]` `ARF4-12` Register an optional generic service UI surface for
  MLflow behind AdaOS routing, access policy, origin/CSP controls, health, and
  lifecycle handling.
- [x] `[could]` `ARF4-13` Embed the advanced MLflow UI in an iframe only after
  the governed same-origin/proxy path passes authentication and browser tests.
- [x] `[must]` `ARF4-15` Separate the reusable research control plane from the
  TLP runner and primary-data owner through `adaos.research.runner.v1`; the
  manager stores logical provider/output references and does not read the
  provider's private data binding.
- [x] `[must]` `ARF4-16` Add bounded `skills.invoke` SDK mediation with an
  explicit caller capability, ordinary target tool/schema/timeout checks,
  target-identifier validation, and nesting protection.
- [x] `[must]` `ARF4-17` Persist and project the experiment's
  `data_owner_skill_id` as an owner-qualified ResearchSpace while retaining a
  provider-neutral manager database for governance metadata.
- [x] `[must]` `ARF4-18` Move legacy TLP primary data through skill lifecycle
  migration: the manager excludes the former dataset subtree, the TLP owner
  adopts it by hardlink or copy, and the old bucket is retained until its
  runtime can be retired safely.
- [x] `[should]` `ARF4-19` Define `adaos.scenario.guidance.v1` for a versioned
  README, localized overview, Help modal, and workflow-aware next-action
  source across web, text, and voice.
- [x] `[should]` `ARF4-20` Implement the guidance contract in `tlp_research`
  with deterministic EN/RU help and next-step intents, including a voice
  story, without requiring an external LLM.
- [ ] `[deferred]` `ARF4-14` Do not use MLflow Model Registry as the automatic
  AdaOS model-promotion authority; integrate promotion with the owning model
  runtime contract later.

Acceptance evidence (2026-08-08): the machine-readable
[Research Tracker Contract 1.0](research-tracker-contract-v1.md) freezes the
session, observation, artifact, provider-link, export, query, health, and
delivery surface. The MLflow adapter maps the complete AdaOS identity set,
uses the local journal as a bounded transactional outbox, rejects conflicting
duplicates, exposes degraded delivery state, replays after restart, and fails
required terminal delivery explicitly. The conformance suite covers ordering,
duplicate steps, outage/restart, capacity, large artifact references,
finalization, export, authenticated TLS binding/version probe, and deletion
only after immutable evidence acceptance.

Core service supervision negotiates owner-scoped relational and blob bindings,
injects physical locations only into the owning process, and supports
provisioned PostgreSQL/object providers without lending their schemas to the
research manager. The generic service UI publishes a redacted surface behind
an authenticated same-origin proxy with lifecycle, origin, CSP, framing, and
request-size controls. `visual.serviceFrame` derives only that governed
bootstrap route. The focused native AdaOS run passed 71 Python checks with two
PostgreSQL skips, followed by a live 2/2 PostgreSQL run that included the
least-privilege service login. The tracker conformance run passed four provider
tests; ChromeHeadless passed six registry/frame tests, and a live Chrome load
rendered the MLflow React application through the gateway without an upstream
URL leak or CSP violation. E002's normalized export was accepted immutably;
result verification now uses `accepted-export` and verifies all eight
content-addressed artifact references independently of MLflow. Because E002
started before the freeze, its immutable session tag remains `1.0-rc1`; new
sessions use contract `1.0` and historical evidence is not relabelled. MLflow
therefore remains a query/projection provider rather than evidence authority.

Universal-control-plane evidence (2026-08-10): TLP runner code no longer lives
under `research_manager_skill`; arbitrary arm identifiers and execution
profiles pass the generic condition validator, result fields are selected by
declared analysis paths, and TLP data/artifact access is mediated by its owner
provider. The scenario README and modal are equality-tested, while the same
`describe_experiment` projection supplies state-sensitive text and speech.
Published manager `0.8.1` and TLP provider `0.1.1` both passed native install
self-tests in active slot B. Manager migration copied only `db` and `internal`
and explicitly excluded `files/datasets`; TLP activation adopted eight STL-10
entries by hardlink under its own runtime data root and reports the binding
ready. Published scenario `0.3.1` passed clean validation and all seven package
tests, then rematerialized the Desktop composition with 13 widgets. A live
Russian voice projection for finalized E002 returned evidence verification
and tracker inspection as the current next actions.
The general contract is documented in
[Scenario Guidance and Help Contract](scenario-guidance.md).

## ARF5. Ray Executor Provider (Deferred)

This lane is intentionally paused. It resumes only after the full
source-to-formulation-to-Codex-to-local-experiment-to-analysis loop is proven
on an AdaOS member node. None of ARF6, ARF7, or the first autonomous TLP loop
may cite Ray as a prerequisite.

**Outcome:** the same immutable trial/run can execute through Ray while AdaOS
retains identity, workflow, and evidence authority.

**Admission gate:** ARF3 executor conformance and attempt recovery are proven
locally.

**Exit proof:** a multi-worker fixture executes on Ray, streams normalized
status/logs/observations, survives worker and AdaOS restart scenarios,
reconciles an unknown submission, cancels correctly, and produces the same
evidence analysis as the local provider.

- [ ] `[must]` `ARF5-01` Package a `ray-executor` service skill or external
  provider adapter through the existing skill lifecycle and service discovery.
- [ ] `[must]` `ARF5-02` Integrate through the supported Ray Jobs API/client;
  keep Ray task, actor, and object references behind the adapter boundary.
- [ ] `[must]` `ARF5-03` Bind AdaOS attempt idempotency keys to stable provider
  submission metadata and reconcile before retry after ambiguous responses.
- [ ] `[must]` `ARF5-04` Map provider states into normalized queued, running,
  cancelling, succeeded, failed, cancelled, lost, and unknown semantics with
  raw diagnostic details retained.
- [ ] `[must]` `ARF5-05` Implement log/status collection, cancellation,
  heartbeat/liveness, terminal outputs, and checkpoint references without
  relying on the Ray Dashboard as an API.
- [ ] `[must]` `ARF5-06` Map CPU, memory, GPU, accelerator, placement, and
  environment requirements with explicit rejection for unsupported requests.
- [ ] `[must]` `ARF5-07` Ensure workers receive only scoped secrets and
  preassigned AdaOS/tracker identities; do not let workers invent study/run
  identity.
- [ ] `[must]` `ARF5-08` Add fault-injection tests for duplicate submit, head
  loss, worker loss, network partition, delayed status, checkpoint failure,
  cancellation race, and tracker outage.
- [ ] `[should]` `ARF5-09` Support authenticated external Ray clusters through
  an executor binding before automating a local managed cluster lifecycle.
- [ ] `[should]` `ARF5-10` Support Ray Tune only for an explicitly exploratory
  search space, scheduler, resource/time budget, and lineage export; chosen
  candidates require a new locked confirmatory protocol.
- [ ] `[should]` `ARF5-11` Expose Ray Dashboard only as an access-controlled
  operator/debug service UI surface.
- [ ] `[could]` `ARF5-12` Add data locality and gang-placement optimizations
  after correctness and recovery evidence exists.

## ARF6. Deterministic TLP Scientific Reference Proof

**Outcome:** TLP validates scientific reproducibility, provider portability,
failure recovery, evidence integrity, and useful inspection across the full
fabric.

**Admission gate:** ARF1 and ARF3 are validated locally. ARF4 and ARF5 are
required for their respective provider portability sub-gates, not for the
first local scientific pilot.

**Exit proof:** a clean install executes the locked paired study locally; the
evidence verifier independently reconstructs the primary analysis; native
AdaOS views resolve to the accepted identities; and a reviewer records an
accepted, rejected, inconclusive, or follow-up decision without relying on
notebook state. A claimed MLflow or Ray sub-gate additionally proves that its
provider views/exports resolve to those identities and that provider changes
preserve the declared semantics; these optional sub-gates do not block the
local proof.

### Operator and package

- [ ] `[must]` `ARF6-01` Extract one canonical TLP implementation into a
  versioned package with no notebook-defined runtime operator variants.
- [ ] `[must]` `ARF6-02` Use a centered spatial-kernel parameterization and an
  explicit scalar level term if scientifically required; document the
  identifiability rationale.
- [ ] `[must]` `ARF6-03` Prove zero shape parameters reproduce ordinary MaxPool
  within declared tolerance across supported shapes, strides, padding, dtypes,
  and devices.
- [ ] `[must]` `ARF6-04` Add forward, gradient, tie, serialization,
  determinism, CPU/GPU parity, invalid-input, and property tests.
- [ ] `[must]` `ARF6-05` Package data preparation, model definitions, training,
  evaluation, analysis, and visualizations as testable components referenced
  by the study scenario.

### Protocol and statistics

- [ ] `[must]` `ARF6-06` Create immutable dataset and split manifests with
  train/validation/test separation and digest verification.
- [ ] `[must]` `ARF6-07` Freeze a deterministic evaluation suite and a
  separately named, fixed robustness-transform suite; prohibit stochastic
  transforms in ordinary test evaluation.
- [ ] `[must]` `ARF6-08` Declare the primary hypothesis, estimand/contrast,
  metric, effect size, uncertainty interval, exclusion rule, multiplicity
  treatment, failure/missing-data policy, and stop rule before execution.
- [ ] `[must]` `ARF6-09` Justify confirmatory sample size from pilot variance,
  power/sequential policy, and compute budget; treat ten paired seeds only as
  an initial engineering floor when appropriate.
- [ ] `[must]` `ARF6-10` Pair TLP and MaxPool variants by split,
  initialization lineage, data order, augmentation, and named RNG streams.
- [ ] `[must]` `ARF6-11` Include ordinary MaxPool, parameter-count controls,
  and scientifically relevant fixed/constrained morphological baselines.
- [ ] `[must]` `ARF6-12` Seal test access until protocol, smoke, completeness,
  and QC gates pass; record every unblind and protocol version.

### Mechanism and evidence

- [ ] `[must]` `ARF6-13` Record layer/seed kernel shapes, winner-position
  distributions, entropy, activation/gradient statistics, and shift
  sensitivity with versioned metric definitions.
- [ ] `[must]` `ARF6-14` Run predeclared centering, freezing, permutation, and
  removal ablations that can distinguish learned phase bias from parameter
  count or optimization effects.
- [ ] `[must]` `ARF6-15` Produce a portable evidence bundle containing protocol,
  analysis plan, trial matrix, normalized observations, provider exports,
  code/environment/data/operator digests, artifacts, exclusions, and analysis
  output.
- [ ] `[must]` `ARF6-16` Recompute the primary comparison in a clean verifier
  environment without a live MLflow or Ray dependency.
- [ ] `[must]` `ARF6-17` Demonstrate restart/reconciliation and one deliberate
  worker failure without changing the scientific sample identity.
- [ ] `[must]` `ARF6-18` Present protocol, paired progress, QC, comparisons,
  mechanisms, evidence, and claim actions in a native AdaOS Research Workbench.
- [ ] `[must]` `ARF6-19` Record the reviewer decision as accepted, rejected,
  inconclusive, or follow-up-required with evidence and rationale; do not make
  framework acceptance depend on a positive TLP result.
- [ ] `[should]` `ARF6-20` Repeat a representative subset on a second hardware
  profile and record numerical/performance portability limits.
- [ ] `[should]` `ARF6-21` Compare local-tracker/local-executor results with
  MLflow/Ray normalized exports through provider conformance assertions.
- [ ] `[should]` `ARF6-22` Define the machine-readable ClaimSet and
  ResearchRelease input required by later synthesis/writing, with every table
  and figure linked to an analysis digest; do not implement the writer here.
- [ ] `[deferred]` `ARF6-23` Do not import existing notebook outputs as
  confirmatory trials; retain them only as exploratory provenance.

## ARF7. Research-Direction Authoring and Assisted TLP Design

**Outcome:** Research Workbench can create/focus a local research Project and
primary direction skill, admit manifested local source artifacts, let a human
and LLM accept an exact ResearchPrototype, create a bounded Builder Development
Session, adapt only admitted targets through isolated Codex, and publish the
Project through the ordinary lifecycle. One shared Workbench and orchestrator
replace both a scenario per direction and a research-management tab in
Builder.

**Admission gate:** ARF4 contracts are locally valid. ARF7 may proceed
alongside ARF6. Ray is not an admission dependency; Automation and Trial use
the current or selected member node.

**ARF7.0 precursor proof (accepted locally):** starting from the original TLP
notebook and review, direct Builder/orchestrator calls create
`tlp_direction_skill`, inventory a content-addressed SourceBundle, discuss
typed candidates, reject invalid or stale revisions, and accept an exact
ResearchPrototype and AutomationBrief while `codex_started=false`. No raw chat
or notebook output becomes canonical state.

**ARF7.1 operator exit proof (accepted locally on 2026-08-11):** starting from
the Research Workbench, a user creates and focuses a TLP Project/direction skill, uploads the
notebook and review into `artifacts/part0`, inspects their manifest/extraction,
conducts and accepts formulation, and obtains a linked Builder Development
Session. The exact direction skill is the sole read/write target; Workbench and
orchestrator contracts plus artifacts are read-only; Preview resolves the
declared/fallback presentation through one complete navigation destination;
the session is credible for Codex but records `codex_started=false`. The
reference proof used the same public skill tools wired to the Workbench without
calling an external LLM, and then verified the materialized Workbench/Builder
projection on the reference machine.

**Full ARF7 exit proof:** isolated Codex implements the exact brief, native
validation and a bounded CPU Trial pass, the direction skill is published, and
the resulting Study/Campaign seed refs match the accepted prototype. A custom
scenario is created only if a declared post-publication UI need requires it.

### ARF7.0 technical precursor: source intake and direction template

- [x] `[must]` `ARF7-01` Define minimum `SourceBundle` ABI with project ref,
  immutable payload/source/bundle digests, MIME/type, role, origin, deterministic
  analysis, generation, and count/size bounds. Store objects in CTX-derived
  Builder state rather than a research-specific tree.
- [x] `[must]` `ARF7-02` Add first-class Builder API, UI upload, SDK, and generic
  `builder source-add/source-list` intake for bounded individual files,
  including `.ipynb` and UTF-8 text. Private payloads are not copied into the
  published direction package.
- [x] `[must]` `ARF7-03` Extract notebook cell/code/Markdown/output/import and
  kernel metadata plus UTF-8 text previews deterministically. Mark notebook
  outputs as untrusted source material; do not promote them to evidence.
- [x] `[must]` `ARF7-04` Add a dynamic `research_direction` skill template and
  shared Builder Research view with source upload, formulation state, chat,
  durable activity, next-step guidance, acceptance, and AutomationBrief. Do
  not create a direction-specific scenario.
- [x] `[must]` `ARF7-05` Make the direction skill the future runner and primary
  data owner. Reuse `research_orchestrator_skill`, `research_manager_skill`,
  tracker, and execution capabilities; do not scaffold shared-service copies.

### ARF7.1 current milestone: Workbench to pre-Codex Project

The following is the active ordered checklist. Checked precursor items above do
not substitute for this operator path.

#### General contracts required by research

- [x] `[must]` `ARF7.1-01` Define and validate `adaos.project.v1` as a
  distribution-only composition of owned skills/scenarios, external
  dependencies, profiles, entry points/presentations, and lifecycle policy. A
  one-skill Project is valid.
- [x] `[must]` `ARF7.1-02` Define
  `adaos.builder.development_session.v1` with independent focus, primary and
  secondary write targets, filtered read-only context, artifact inputs,
  scratch policy, exact base/checkpoint, and scope-expansion request.
- [x] `[must]` `ARF7.1-03` Define skill `presentations`, Project entry-point
  binding, separate presentation-verification evidence, and deterministic
  resolution through explicit entry point, default presentation, or the
  generic system skill-preview host.
- [x] `[must]` `ARF7.1-04` Define Skill SDK local artifact groups and
  provider-neutral `ArtifactRef`: `artifacts/partN/manifest.yaml`, bounded
  list/resolve/read/extract, exact digest/staleness rules, trust/media/role,
  and include/manifest-only/exclude publication policy.

#### Research Workbench and direction lifecycle

- [x] `[must]` `ARF7.1-05` Publish and activate one shared
  `research_workbench` scenario backed by `research_orchestrator_skill`. Remove
  the full Research workflow/tab from Builder; retain only a compact origin and
  "Return to research" link when a session came from Workbench.
- [x] `[must]` `ARF7.1-06` Project an orchestrator portfolio read model with
  direction id/title, current formulation/development stage, blocker/next step,
  last activity, active automation status, and one session-local focused
  direction. Durable direction state remains with the direction aggregate;
  filters/focus are not scientific truth.
- [x] `[must]` `ARF7.1-07` Make "Create research direction" collect minimum
  identity metadata and call Builder SDK atomically to create a local Project
  from the builtin research profile plus its primary direction skill from
  `research_direction`; select it in focus only after both are valid.
- [x] `[must]` `ARF7.1-08` Make the Workbench direction detail expose Overview,
  Artifacts, Formulation, Development, and Activity/next-step surfaces without
  requiring the user to understand raw skill/scenario selection.
- [x] `[must]` `ARF7.1-09` Expose one shared Research Workbench Application.
  Individual directions use focus/deep links, not generated scenarios/icons.
  A compact general-slot activity widget is a non-blocking follow-on after the
  autonomous-process projection has a stable contract.

#### Local artifacts, formulation, and acceptance

- [x] `[must]` `ARF7.1-10` Upload the TLP notebook and review through Workbench
  into the new direction skill's `artifacts/part0`; write/validate the manifest,
  show exact files/digests/types/roles and bounded notebook/text extraction,
  and make the paths directly readable on disk by Codex.
- [x] `[must]` `ARF7.1-11` Adapt the existing formulation ledger/chat/activity
  tools to a focused Project/direction ref and local artifact group revision.
  Changing accepted inputs makes the current candidate stale; switching focus
  cannot leak chat, artifacts, or state between directions.
- [x] `[must]` `ARF7.1-12` Accept one exact ResearchPrototype through Workbench
  and emit an AutomationBrief containing Project ref, primary target, artifact
  refs/digests/native paths, prototype, implementation requirements,
  acceptance checks, prohibited actions, and `codex_started=false`.

#### Builder handoff, preview, and UX

- [x] `[must]` `ARF7.1-13` Create and link a Builder Development Session from
  the accepted brief. Admit the direction skill as the sole read/write primary
  target; expose artifact groups read-only and shared Workbench/orchestrator
  only at contract/docs level. Enforce scope in tools and final patch review,
  not only prompt text.
- [x] `[must]` `ARF7.1-14` Open the linked session in Builder with the correct
  Project/title/target already selected. Builder Artifacts shows the same
  manifested source files and Development shows the exact scope; no duplicate
  upload or formulation state is created. The Workbench action uses a named
  browser window and an API-host `builder.context.selected` projection, so a
  delayed worker event cannot replace either Workbench focus or Builder
  identity.
- [x] `[must]` `ARF7.1-15` Make skill preview resolve the Project entry point or
  generic skill-preview host. Missing icons/widgets render explicit empty
  states; an unrelated previously materialized scenario is never retained.
- [x] `[must]` `ARF7.1-16` Make Open Preview and QR consume one canonical
  destination with `desktop-dev`, scenario/presentation bindings, zone,
  subnet, and applicable auth/auto-login policy. Runtime projection evidence
  must show that both routes resolve the same target.
- [x] `[must]` `ARF7.1-17` Repair generic New Dev Project UX even though the
  normal research path starts in Workbench: Taiga searchable combo-box with
  visible selection, version/description/source, builtin/recommended templates
  first; required Project ID validation reports `Укажите ID проекта`; created
  Project title/state is updated atomically or rolled back.

#### Milestone evidence

- [x] `[must]` `ARF7.1-18` Add schema/SDK/orchestrator/Builder tests for Project
  creation rollback, owner/focus isolation, artifact traversal and bounds,
  staleness, acceptance idempotency, context/write policy, presentation
  fallback, and canonical navigation.
- [x] `[must]` `ARF7.1-19` Run the complete TLP walkthrough on the reference
  machine starting at Desktop Research Workbench and ending at a credible
  unopened Codex session. Record exact Project, skill, artifact manifest,
  ResearchPrototype, AutomationBrief, Development Session, package versions,
  package versions and rematerialized preview evidence. Exact receipts are in
  `research-project-pre-codex-walkthrough.md`.
- [x] `[must]` `ARF7.1-20` Publish/install the shared Workbench and updated
  orchestrator/template components through ordinary AdaOS lifecycle, update
  help/next-step text so every instruction names a visible control, and verify
  the flow from a clean focused direction without private DEV state.
- [x] `[must]` `ARF7.1-20b` Keep ordinary direction-owned artifact attachment
  inside declared `local_write`; publish genuine runtime approvals through the
  asynchronous Pending Actions live-room owner and prove the global review
  surface can observe them.
- [x] `[must]` `ARF7.1-20c` Keep a human-readable consensus beside Discussion,
  move auxiliary direction diagnostics behind an explicit Details control,
  and provide authenticated modal preview for manifested Markdown, text, and
  PDF artifacts without exposing native paths.
- [x] `[must]` `ARF7.1-20d` Address the exact Builder target on first paint
  through a schema-declared one-shot query mapping, then converge on the
  canonical Yjs selection; never keep URL query state authoritative after
  initial addressing.
- [x] `[must]` `ARF7.1-20e` Prove the Workbench portfolio over the ordinary
  AdaOS `tool/details` path: trusted read capability, event-driven lifecycle
  suspension, bounded retry/rate behavior, preserved last value, explicit
  unavailable/error presentation, strict route-policy validation, and an
  idle/reconnect fault matrix. The read intent is preserved and re-verified on
  both local and routed member execution, and explicit retry is scoped to one
  semantic source. No Workbench-private transport is admitted.
- [x] `[must]` `ARF7.1-20f` Add reusable state-selected full-surface layout
  variants to `webui.v1`; isolate page state across webspace/scenario/page,
  heal staged missing defaults, define nullish comparison semantics, and prove
  portfolio/detail/back/reload fallback without a Workbench-private renderer.
- [x] `[must]` `ARF7.1-20g` Make Markdown math parsing line-ending neutral and
  cover the exact CRLF legacy equation block imported by the TLP review.
- [x] `[must]` `ARF7.1-20h` Extract notebook source cells before bounding,
  omit raw outputs, expose fragment provenance and coverage, and prevent generated
  source claims from citing context that the formulation model did not receive.
- [x] `[must]` `ARF7.1-20i` Strengthen ResearchPrototype with typed comparator,
  pairing, RNG, data-seal, estimand, uncertainty, stopping, multiplicity,
  negative-result, requirement, and acceptance structures. AdaOS owns the
  deterministic admission review; LLM readiness is never sufficient.
- [x] `[must]` `ARF7.1-20j` Present drafts honestly, disclose coverage and gate
  blockers, enable acceptance only for an admitted revision, and coalesce
  durable LLM progress while retaining detailed grouped chat updates.
- [x] `[must]` `ARF7.1-20k` Run fresh ordinary Root-LLM formulation for
  `tlp_research_03` from both original artifacts, retain and explain rejected
  drafts, human-review a corrected exact revision, and pass the strengthened
  deterministic gate before creating AutomationBrief/Development Session.
  The model is not required or allowed to self-certify admission.
- [x] `[must]` `ARF7.1-20m` Persist every caller-visible formulation directive
  with stable actor, invocation origin, bounded text and digest. Project
  API/CLI/Codex directives into the same research chat while avoiding a
  duplicate of an ordinary conversation message. Never journal the hidden
  system prompt or source excerpts as a caller directive.
- [x] `[must]` `ARF7.1-20n` Make formulation completion state deterministic:
  an admitted candidate is ready only for human acceptance; a blocked
  candidate is explicitly a reviewable draft; an invalid candidate exhausted
  after bounded repair is rejected without creating a revision. Do not trust
  the model-authored `assistant_message` as lifecycle truth.
- [x] `[must]` `ARF7.1-20o` Harden the generic source-backed skill loader
  against failed and concurrent first imports and collisions between identical
  short local package names in different skills; expose categorized
  runtime-data failures in the shared widget host; and keep the experiment
  control-plane snapshot available when runner/tracker health dependencies
  degrade.
- [x] `[must]` `ARF7.1-20p` Replace raw notebook-prefix context with semantic,
  query-aware prepared-source units, near-duplicate compaction, bounded
  explicitly untrusted output summaries, stable provenance, and disclosed
  selected/omitted coverage. Keep plain text on the same ABI and reserve PDF as
  an adapter with page/OCR evidence.
- [x] `[must]` `ARF7.1-20q` Split rich formulation into durable typed
  problem/protocol/implementation stages; keep the authoritative local schema
  distinct from the provider subset; repair only one failed stage; expose full
  stage artifacts, job attempts, resolved model, aggregate usage, and digests
  through the skill API.
- [x] `[must]` `ARF7.1-20r` Resolve early uncertainty through a typed decision
  ledger, compile effect/threshold decision logic and checkpoint selection in
  AdaOS, seal final-test access, reject per-epoch test observation, and prove
  the path with a fresh zero-repair TLP Root-LLM run. Model output remains
  subject to human acceptance.
- [ ] `[must]` `ARF7.1-20l` In an authenticated operator browser, record the
  published portfolio -> direction -> back -> reload fallback and the exact
  CRLF Markdown/KaTeX preview. Automated layout/math regressions, production
  build, and hosting deployment are complete; a clean headless profile reached
  pairing rather than the authenticated Desktop and therefore is not accepted
  as this black-box receipt.

#### Non-blocking follow-ons

- [ ] `[should]` `ARF7.1-20a` Add the compact Desktop general-slot projection
  once autonomous activity, pause/approval, and blocker signals have a stable
  read model; do not invent a second direction-specific application.
- [ ] `[should]` `ARF7.1-21` Add directory/archive intake, secret/malware
  scanning, full license/sensitivity editing, and artifact retention/deletion
  UX before unattended or arbitrary-corpus ingestion.
- [ ] `[should]` `ARF7.1-22` Add backward-compatible registry Project entries,
  machine profiles/capabilities, localized categories, free tags, and separate
  deployment scopes; Catalog leads with Projects/Applications and keeps raw
  components in advanced view.
- [ ] `[should]` `ARF7.1-23` Add portable artifact-group export/import and
  verification receipts for research Projects intended for reproduction on
  another node.
- [ ] `[deferred]` `ARF7.1-24` Resolve `additional_artifacts` through object
  storage or remote repositories after local path/digest semantics pass the
  TLP proof.
- [ ] `[deferred]` `ARF7.1-25` Expose the same ArtifactRef resolver as MCP only
  when a real agent/runtime lacks native filesystem or Skill SDK access.
- [ ] `[deferred]` `ARF7.1-26` Complete remote Project Catalog publication and
  transactional multi-component install/remove/reference counting; the local
  Project definition and existing ProjectRelease path are sufficient for this
  pre-Codex gate.

### ResearchPrototype and consensus

- [x] `[must]` `ARF7-06` Define digestible `ResearchPrototype` with background,
  question, falsifiable hypotheses, staged experiment plan, evidence classes,
  execution profiles/budgets/stops, evaluation rules, assumptions, risks, open
  questions, implementation requirements, acceptance checks, readiness, source
  bundle, revision, and lineage.
  Version 1.1 additionally binds disclosed context coverage, provenance-only
  source grounding, typed causal/reproducibility and inference contracts, and
  a deterministic core-owned admission review.
- [ ] `[must]` `ARF7-07` Extend the current ordered experiment stages into a
  validated Campaign DAG with dependencies, branch predicates, evidence gates,
  budget aggregation, and cycle/conflict rejection before adaptive campaigns.
- [ ] `[should]` `ARF7-08` Add a research semantic-patch profile so later LLM
  turns can propose bounded field operations against stable refs. The first
  slice safely records complete schema-valid candidate revisions instead;
  arbitrary prose is never accepted as a mutation.
- [x] `[must]` `ARF7-09` Persist direction generation, immutable candidate
  revisions, actor/parent lineage, grouped chat progress, durable activity,
  blockers, and workflow-aware next steps. Acceptance names one exact digest
  and expected generation.
- [x] `[must]` `ARF7-10` Keep SourceBundle, conversation, current candidate,
  accepted candidate, Builder checkpoint, and AutomationBrief as distinct
  identities. Source changes make the candidate stale; LLM self-readiness does
  not bypass schema and semantic admission.

### Automation, publication, and re-entry

- [x] `[must]` `ARF7-11` Emit an immutable AutomationBrief with exact source,
  prototype, and Builder checkpoint digests; source inventory; scientific
  objective; implementation requirements; acceptance checks; and prohibited
  actions. Acceptance is idempotent and does not start Codex or an experiment.
- [x] `[must]` `ARF7-12` Make isolated Codex materialize the direction skill's
  operator, data preparation, runner, schemas, migrations, observations,
  analyses, and tests from the exact handoff. It may report blockers but cannot
  amend the accepted objective or analysis to fit implementation results. The
  TLP receipt records every failed/intermediate task and exact final Forge
  identity; no scientific workload ran during realization.
- [x] `[must]` `ARF7-13` Validate generated TLP code with ARF6 operator,
  determinism, split, sealed-data, evidence, workflow, migration, and package
  admission tests before Trial/Publication. The bounded real-path fixture also
  exercises the published manager consumer, native package/install/activation,
  crash/resume, and shared heavy-dependency resolution.
- [ ] `[must]` `ARF7-14` Publish the direction Project as one exact
  ProjectRelease containing its owned direction/experiment components and
  instantiate Study/Campaign seed state from the accepted prototype without
  retaining two mutable copies.
- [ ] `[must]` `ARF7-15` Add runtime-to-Builder re-entry: a typed CapabilityGap
  or defect creates a linked Issue/Change with direction, Study/Campaign,
  evidence, handoff, and installed-release refs. Ordinary experiment/campaign
  edits remain Research Fabric operations and do not invoke Codex.
- [ ] `[should]` `ARF7-16` Add source-grounded literature attachments and
  citation links before autonomous retrieval; retrieved text remains untrusted
  evidence with snapshot, license, quotation, and claim-link metadata.
- [ ] `[should]` `ARF7-17` Measure source-to-prototype target coverage,
  clarification/correction rate, unsupported assumptions, context sufficiency,
  direct-Markdown baseline quality, Codex handoff fidelity, Trial failures,
  elapsed time, model usage, and human interventions. The first controlled TLP
  receipt now records source coverage, model/profile, stage repairs, latency and
  aggregate usage, and identifies concrete semantic corrections; direct-
  Markdown baseline, Codex fidelity, and a multi-task evaluation set remain.

### ARF7.3 research-compilation calibration and accepted workflow proof

**Outcome:** Research Workbench makes the scientific-problem-to-engineering
bridge explicit, performs one clean from-raw TLP compilation with the
historical review and implementation hidden, compares controlled degrees of
typing under matched budgets, and then publishes and executes the accepted
direction through generic Research Fabric contracts.

**Admission gate:** ARF7.2 realization evidence remains available, but its
review-assisted handoff is labelled as such. The historical `initial-review`,
legacy TLP packages/scenario, E002 receipts, and evaluator rubric have declared
stage visibility and cannot enter a clean model or Codex context.

**Exit proof:** a frozen calibration package contains the visible inputs,
hidden/evaluator inputs, source-analysis and formulation revisions,
traceability graph, exact AutomationBrief, Builder/Codex traces, ProjectRelease,
generic Workbench execution receipts, costs, interventions, and per-stage
failure attribution. C0-C4 use the same task, model class, tools, environment,
and declared budgets. The result is reported as a TLP calibration, not a TLP
efficacy result or cross-domain SOTA claim.

- [x] `[must]` `ARF7.3-01` Add versioned artifact stage visibility for
  `formulation`, `implementation`, `execution`, `evaluation`, and human-only
  access. Materialize every model/agent context from this policy and record its
  exact included/excluded refs and digest.
- [x] `[must]` `ARF7.3-02` Persist a source-analysis projection with inventory,
  extraction coverage, stable fragment refs, observations, interpretations,
  claims, assumptions, contradictions, ambiguities, environment hints, and
  explicit untrusted-output status.
- [x] `[must]` `ARF7.3-03` Extend staged formulation with an auditable
  ResearchProblem facet covering primitives, tension/gap, primary question,
  assumptions, alternatives, falsifier, minimal decisive test, expected
  observations, failure-update rule, and prohibited claims.
- [x] `[must]` `ARF7.3-04` Compile an ExperimentalProtocol facet that maps the
  problem to population, intervention/comparator, controlled invariants,
  outcomes/estimand, allocation/pairing, random streams, profiles, seals,
  stopping, uncertainty, decision regions, and evidence requirements.
- [ ] `[must]` `ARF7.3-05` Produce and validate a traversable chain from source
  or human decision through scientific requirement, protocol element,
  engineering obligation, runtime observation/artifact, and acceptance/claim
  decision. Classify expert-review points as compiled, deferred,
  rejected-with-reason, or unresolved.
- [x] `[must]` `ARF7.3-06` Project the accepted protocol into the existing
  AutomationBrief and direction manifest as a narrow task envelope,
  provider-operation bindings, artifact flow, result/observation schemas,
  ownership/recovery constraints, forbidden scientific mutations, and neutral
  conformance fixtures. Do not prescribe internal code structure without a
  contract reason.
- [x] `[must]` `ARF7.3-07` Let Codex return typed clarification, feasibility,
  capability-gap, and protocol-conflict results. Require a reviewed new
  formulation/protocol revision instead of silent scientific mutation.
- [ ] `[must]` `ARF7.3-08` Render Source Analysis, Problem, Protocol,
  Engineering Contract, coverage, and traceability as related Workbench
  revisions. Preserve free narrative beside typed decisions and keep model
  readiness separate from human acceptance.
- [x] `[must]` `ARF7.3-09` Implement a matched C0 raw, C1 reviewed-prose, C2
  staged, C3 typed-execution, and C4 over-specified evaluation harness. Freeze
  model/tool/environment/time/token/compute/retry policy, prevent cross-arm
  workspace leakage, and blacklist legacy TLP implementation sources. Report
  both fixed downstream-Codex budget and fixed total end-to-end budget views;
  charge formulation and expert-review effort rather than hiding it.
  Task schema v1.1 freezes the exact Codex profile, host/runtime environment,
  usage accounting policy and explicitly distinguishes workload seeds from
  unavailable model-randomness control. The v1 C3 diagnostic is a retained
  budget failure; it is not silently reclassified after the budget was seen.
- [x] `[must]` `ARF7.3-10` Measure evidence-valid completion, pass@1, protocol
  drift, unsupported assumptions, source/review coverage, conformance,
  runtime/result validity, reproducibility, leakage, interventions, human
  repair time, tokens/cost/compute, and portability. Attribute every failure to
  source understanding, formulation, operationalization, compilation, Codex,
  runtime/platform, or scientific evaluation. The complete frozen v5 C0-C4
  pass records `0/5` evidence-valid completions, exact usage, no human
  interventions, and immutable per-arm failures. Portability is measured as
  not demonstrated in this single-host run rather than inferred from it.
- [ ] `[must]` `ARF7.3-11` Run the clean TLP calibration with a preregistered
  repeated paired design sufficient to expose run variance. Keep
  `initial-review` visible only in C1 and evaluation; use it and legacy TLP
  solely as coverage/semantic oracles, never as hidden Codex requirements.
- [ ] `[must]` `ARF7.3-12` Complete ARF7-14 ProjectRelease and instantiate/run
  the accepted local workflow through `research_manager_skill` and the shared
  Workbench. The direction supplies bindings and artifacts, not a TLP-specific
  scenario or management UI.
- [ ] `[must]` `ARF7.3-13` Freeze a machine-recomputable calibration package,
  document negative and inconclusive outcomes, and record which candidate
  fields remain profile-local. No contract enters core from TLP alone.
  Partial evidence: evaluator `0.1.13` exports the frozen task, five packets,
  five results, and recomputed summary under package digest
  `sha256:44dab2cab3bba2705e59264bd6ddddca1030e51b49b8c29a2bd54d094a110be9`.
  The package is score-recomputable, but admitted source bytes, licenses,
  runtime images, and an external verification recipe are not yet a portable
  release, so this item remains open.

## ARF8. Autonomous TLP Closed Loop and Scientific Release

**Outcome:** after one human-approved Research Mandate, aResearcher may operate
TLP unattended through adaptive exploration, bounded Builder/Codex adaptation,
fresh confirmation, evidence review, ClaimSet, and ResearchRelease without
becoming a privileged source of scientific truth.

**Admission gate:** ARF7's exact authoring/handoff/re-entry path and the relevant
ARF6 scientific gates are valid. Agent-generated code uses a digest-pinned,
resource/network/secret-bounded execution environment; the local process
adapter alone is not a hostile-code isolation claim.

**Exit proof:** a frozen TLP task is run in A0 and A4 modes under matched
scientific and resource contracts. The A4 session performs at least one real
evidence-driven Campaign branch and one isolated experimental-base adaptation,
survives restart and an unknown outcome, preserves the sealed confirmation
boundary, reaches an honest terminal state, and exports an independently
verifiable ResearchRelease. It neither exceeds its mandate nor requires a
positive TLP result.

### Mandate, controller, and budgets

- [ ] `[must]` `ARF8-01` Define `ResearchMandate` and immutable revisions for
  objective/scope, source refs, permitted hypothesis/protocol/code mutations,
  data/unblind policy, provider/tool/network authority, output contract,
  budgets, stops, escalations, actor, and expiry.
- [ ] `[must]` `ARF8-02` Define `AutonomyProfile` A0-A5. Implement no higher
  than A4; A5 external publication is denied by default and remains separately
  authorized/deferred.
- [ ] `[must]` `ARF8-03` Implement a durable
  `AutonomousResearchSession` controller over governed workflow activities. It
  selects only admitted semantic actions and never owns provider leases,
  retries, state transitions, or package activation itself.
- [ ] `[must]` `ARF8-04` Add a transactional budget ledger for model tokens and
  cost, wall time, CPU/GPU/resource time, experiments, attempts, storage, and
  external requests. Reservations, usage, release, exhaustion, and policy
  expansion are durable and restart-safe.
- [ ] `[must]` `ARF8-05` Define `AgentDecision` with observed generation,
  alternatives, rationale, uncertainty, selected action, mandate/budget
  preconditions, validators, result, and model/prompt/tool/context provenance.
  LLM notes and experiment journals do not advance state.
- [ ] `[must]` `ARF8-06` Define terminal decisions for accepted, rejected,
  inconclusive, follow-up, budget-exhausted, blocked, unsafe, and insufficient-
  confirmation outcomes. The controller must not optimize until positive.

### Scientific and engineering autonomy

- [ ] `[must]` `ARF8-07` Implement an exploration/confirmation firewall. The
  planner and Builder contexts cannot obtain sealed data bindings; promotion
  freezes candidate, implementation, protocol, AnalysisPlan, and stopping rule
  before a fresh or still-hidden evaluator is released.
- [ ] `[must]` `ARF8-08` Represent linear, branching, tree-search, and
  tournament proposals as ordinary Campaign nodes with identity, parentage,
  cost, evidence, selection/rejection rationale, and declared objective/
  validity constraints.
- [ ] `[must]` `ARF8-09` Let a typed CapabilityGap create an autonomous Builder
  Change only when mandate policy admits the exact risk class, target packages,
  paths, permissions, dependency policy, and Trial checks.
- [ ] `[must]` `ARF8-10` Activate autonomous Builder outputs only as
  session-scoped content-addressed Trial candidates with rollback and retention;
  public/stable component promotion remains a separate decision.
- [ ] `[must]` `ARF8-11` Reconcile uncertain LLM, Codex, provider submission,
  package activation, unblind, and external-request outcomes before another
  action is selected. Recovery must not duplicate side effects.
- [ ] `[must]` `ARF8-12` Execute deterministic AnalysisPlan operations before
  LLM interpretation. New result-dependent analyses and hypotheses are marked
  exploratory and cannot rewrite the completed confirmatory family.
- [ ] `[must]` `ARF8-13` Provide isolated planner, implementation, analyst,
  critic, and writer context profiles with minimum necessary refs. One model may
  fill several roles, but no mutable transcript acts as shared authority.
- [ ] `[should]` `ARF8-14` Enable parallel/multi-agent planning only after a
  matched-budget single-agent comparison shows benefit for a decomposable TLP
  subtask; retain centralized validation/admission and measure coordination
  overhead and error amplification.

### Evidence, release, and evaluation

- [ ] `[must]` `ARF8-15` Define ClaimSet as an immutable projection over Claim
  Decisions with predeclared/exploratory/computed/interpreted status,
  supporting and contradicting evidence, uncertainty, limitations, negative
  results, and unresolved alternatives.
- [ ] `[must]` `ARF8-16` Define and verify `ResearchRelease` separately from
  Builder ProjectRelease. Bind mandate, campaign, claims, evidence, sources,
  code/environment/data/model/agent identities, tables/figures, deviations,
  attribution, license, and release policy.
- [ ] `[must]` `ARF8-17` Gate autonomous completion on target/evidence coverage,
  validation, and terminal workflow state rather than final response, report
  fluency, or an LLM self-review score.
- [ ] `[must]` `ARF8-18` Run TLP A0 versus A4 under matched task, tool, data,
  model, and compute ceilings. Report scientific validity, evidence coverage,
  leakage, invalid implementation/result rate, reproducibility, recovery,
  cost, elapsed time, intervention/clarification rate, and terminal outcome.
- [ ] `[must]` `ARF8-19` Threat-test prompt injection and authority escalation
  through notebooks, papers, repositories, logs, tracker artifacts, citations,
  and generated code; prove source text cannot change mandate, capabilities, or
  sealed-data access.
- [ ] `[should]` `ARF8-20` Add an `ExternalReview`/FollowUpProposal import path
  against an exact ResearchRelease so criticism can create a new Campaign or
  Builder Issue without mutating the release.
- [ ] `[deferred]` `ARF8-21` Implement `research_writer_skill` only after
  ClaimSet/ResearchRelease evidence is stable. It may create a neutral Markdown
  DraftEssay with evidence links but cannot reanalyse, add claims, adapt to a
  journal, or submit externally.
- [ ] `[deferred]` `ARF8-22` Autonomous open-web literature exploration,
  unrestricted dependency acquisition, public software promotion, model
  promotion, and external scientific publication require separate later gates.

## ARF9. AdaOS Research Compilation and Replication Benchmark

**Outcome:** versioned `ResearchCompilerBench` and PaperBench-like replication
tracks measure how well AdaOS and alternative agent configurations compile raw
scientific artifacts into executable evidence and reconstruct/reproduce
external research claims under matched task, tool, model, and resource
contracts.

**Admission gate:** the artifact-to-experiment compilation track may begin after
ARF7.3 has a frozen TLP calibration and metrics. Autonomous replication requires
ARF8's independently verified TLP ResearchRelease and frozen process metrics.
The first replication release may start at R0-R2; R3-R5 require corresponding
implementation and evaluator evidence.

**Exit proof:** frozen benchmark releases contain multiple licensed
computational research tasks and expert/author-reviewed source-to-protocol and
target-claim rubrics. The compilation release runs C0-C4 plus one declared
external or no-AdaOS baseline; the autonomous replication release runs at least
two AdaOS autonomy profiles and one declared external or no-AdaOS baseline.
Every comparison uses matched budgets. Per-stage, per-target, aggregate, cost,
intervention, safety, and reproducibility evidence can be independently
recomputed from immutable result packages.

- [ ] `[must]` `ARF9-01` Define a versioned benchmark/task manifest with source
  snapshots, paper/supplement/repository/data/environment refs, licenses,
  visibility/masking, domain, difficulty, expected artifacts, budget, model/tool
  policy, contamination/cutoff metadata, and evaluator version.
- [ ] `[must]` `ARF9-02` Define expert-curated hierarchical `TargetClaimSet`
  rubrics with weights, tolerances, evidence requirements, partial credit,
  critical-failure overrides, report coverage, and hidden evaluator refs.
- [ ] `[must]` `ARF9-03` Define source-to-experiment rubrics and the matched C0
  raw, C1 reviewed-prose, C2 staged, C3 typed-execution, and C4 over-specified
  delivery arms. Use evidence-valid completion as the primary endpoint and
  report the reliability/autonomy/context-cost Pareto frontier under both
  fixed implementation and fixed total-system budgets rather than assuming
  the most typed arm is best.
- [ ] `[must]` `ARF9-04` Define R0 artifact audit, R1 original reproduction, R2
  minimal compatibility repair, R3 independent replication, R4 robustness, and
  R5 follow-up as separate task profiles and result families.
- [ ] `[must]` `ARF9-05` Isolate original, compatibility-repair,
  method-correction, and independent-implementation workspaces. Every patch is
  classified and linked; one track cannot silently replace another.
- [ ] `[must]` `ARF9-06` Record `ProtocolReconstruction`, `AmbiguityLog`,
  per-target generated evidence, numerical comparison, successful/failed Runs,
  provenance, and exact report locations before a target is complete.
- [ ] `[must]` `ARF9-07` Classify results as reproduced,
  reproduced-with-repairs, partially reproduced, not reproduced, or
  indeterminate with explicit artifact/environment/ambiguity/budget/validity
  reason; never infer paper invalidity from execution failure alone.
- [ ] `[must]` `ARF9-08` Implement deterministic rubric checks where possible
  and calibrated, versioned LLM/expert judging only for residual semantic
  criteria. Measure judge agreement and prevent the research agent from seeing
  hidden scoring evidence.
- [ ] `[must]` `ARF9-09` Report weighted target coverage, numerical fidelity,
  protocol/evidence match, claim calibration, invalid result rate,
  reproducibility, intervention/clarification, time/compute/model cost, safety,
  and report evidence coverage with uncertainty across repeated runs.
- [ ] `[must]` `ARF9-10` Run matched A0-A4, model, and agent-topology ablations;
  publish exact task/tool/budget/evaluator versions and normalized result
  packages so improvements are attributable rather than anecdotal.
- [ ] `[must]` `ARF9-11` Select the first tasks for public artifacts, manageable
  local/stand compute, clear primary claims, safe data, durable licenses, and
  evaluator feasibility. Do not copy PaperBench materials without compatible
  licensing; compatibility means comparable contracts and metrics.
- [ ] `[should]` `ARF9-12` Include progressive source/code masking and at least
  one post-model-cutoff or privately held evaluation split to estimate
  contamination and memorization effects.
- [ ] `[should]` `ARF9-13` Include multiple domains and at least one non-neural
  computational task so generalization is not inferred from another image-
  classification study.
- [ ] `[should]` `ARF9-14` Export benchmark tasks/results and ResearchReleases
  in a portable profile, with an RO-Crate mapping after internal contract
  verification.

## ARF10. Generalization and Operational Hardening

**Outcome:** TLP plus multiple replication domains demonstrate generality,
security, scale, and operational reliability before research-domain contracts
are promoted into core or autonomous operation is broadened.

**Admission gate:** ARF9 has a reproducible benchmark release and documented
contract/domain gaps.

**Exit proof:** multiple non-TLP tasks use stable contracts, backup and restore
pass on a PostgreSQL-backed deployment, provider/agent failure and load tests
meet declared SLOs, autonomous security gates pass, and every proposed core
promotion has cross-domain compatibility evidence.

- [ ] `[must]` `ARF10-01` Review every proposed core promotion against TLP and
  multiple replication cases, provider conformance, migration ownership, and
  overlap with workflow, Builder, model, artifact, conversation, event, and UI
  authorities.
- [ ] `[must]` `ARF10-02` Exercise PostgreSQL backup/restore, credential
  rotation, migration failure, owner isolation, and accepted-evidence
  protection on a representative autonomous deployment.
- [ ] `[must]` `ARF10-03` Define SLOs and run soak/load/failure tests for agent
  decisions, budgets, Builder re-entry, tracker ingestion, scheduler
  reconciliation, artifact/release export, restart, and verification.
- [ ] `[must]` `ARF10-04` Add access-control and audit evidence for design,
  autonomy mandates, study roles, unblinding, provider admin, secrets, remote
  execution, candidate activation, and research release.
- [ ] `[should]` `ARF10-05` Add content-addressed object/blob storage for large
  artifacts with retention, quota, garbage collection, and accepted-release
  protection.
- [ ] `[should]` `ARF10-06` Add OpenTelemetry-compatible traces and
  OpenLineage-compatible dataset/job events while preserving AdaOS identities
  without those collectors.
- [ ] `[should]` `ARF10-07` Add multi-user proposal/review/mandate concurrency
  only after identity, authorization, expected-generation, conflict, and audit
  semantics pass their owning architecture gates.
- [ ] `[should]` `ARF10-08` Publish provider, model-profile, autonomy-profile,
  and recovery compatibility matrices for supported storage/tracker/executor
  combinations.
- [ ] `[could]` `ARF10-09` Add tracker, executor, literature, repository, or
  publication adapters only to test a named portability contract.
- [ ] `[deferred]` `ARF10-10` Do not declare research-domain schemas stable core
  ABI, expand A5 external authority, or claim autonomous scientific reliability
  from TLP or one benchmark release alone.

## Cross-Cutting Evidence Matrix

| Gate | Minimum evidence |
| --- | --- |
| Contract | Versioned schema snapshots, compatibility tests, invalid fixtures |
| Local integration | Clean-node install/activate record, deterministic fixture, restart test |
| Storage | Isolation, migration, backup/restore, contention, secret-rotation tests |
| Tracker | Provider conformance, outage/restart, normalized export verification |
| Executor | Idempotent submit, unknown reconciliation, cancellation, checkpoint, fault injection |
| Scientific | Locked protocol/analysis plan, paired trial manifest, QC/exclusions, independent recomputation |
| Authoring | Exact Project/direction identity, manifested local artifact groups, ResearchPrototype diffs, consensus/AutomationBrief, scoped unopened Development Session, source-to-Study lineage |
| Research compilation | Frozen visible/hidden inputs, source analysis, source-to-evidence traceability, C0-C4 matched delivery arms, evidence-valid completion, per-stage failure attribution, cost/intervention evidence |
| Autonomy | Research Mandate, admitted Agent Decisions, budget ledger, exploration/confirmation isolation, escalation/terminal evidence |
| Builder re-entry | CapabilityGap, exact installed base, bounded Codex context, tests, Trial candidate, adoption/rollback lineage |
| Synthesis/release | ClaimSet coverage, contradicting/negative evidence, table/figure provenance, independently verified ResearchRelease |
| Replication benchmark | Frozen task/rubric/evaluator, per-target evidence, matched budgets, repeated-run uncertainty, contamination controls |
| UI | Native state/action contract tests; provider UI access and auth tests if enabled |
| Stand | Environment identity, SLO/load/failure report, residual risks |
| Production | Explicit human acceptance and rollback/recovery evidence |

## Definition of Framework MVP

Research Fabric MVP is complete only when all of the following are true:

- ARF1 and ARF3 `[must]` items are `validated-local`;
- the local TLP proof covers ARF6 operator, protocol, evidence, restart, native
  UI, and reviewer-decision requirements;
- MLflow and Ray remain optional and their absence does not prevent the local
  proof;
- provider-specific claims are made only after ARF4 or ARF5 conformance gates;
- PostgreSQL is not called supported until ARF2 PostgreSQL conformance and
  backup/restore evidence pass;
- documentation and manifests contain no research-specific installation CLI,
  `.adaos/research` state root, direct provider-schema access, or accidental
  test unblinding.

This is the **deterministic Research Fabric MVP**. It does not imply assisted or
autonomous research readiness.

The **Research Project Pre-Codex Milestone** requires every ARF7.1 `must` item
and the Workbench-to-unopened-Codex-session TLP story. The broader **Research
Project Authoring MVP** additionally requires every remaining ARF7 `must` item
and its attachment-to-published-TLP acceptance story. Because the historical
TLP path was review-assisted, the authoring MVP also requires the ARF7.3 clean
from-raw compilation receipt; otherwise only the narrower implementation
handoff has been proven.

The **Autonomous Research Preview** additionally requires every ARF8 `must`
item, one matched A0/A4 TLP run, and an independently verified ResearchRelease.
It is a bounded preview, not a claim of general autonomous scientific
reliability.

A **comparable research-compilation claim** requires the multi-task
ResearchCompilerBench subset of ARF9, matched C0-C4 and external/no-AdaOS
baselines, repeated-run uncertainty, contamination and judge controls, and
independent expert review. A **comparable autonomous-research claim** further
requires ARF8 and the full replication subset of ARF9. Broader
operational/core claims are blocked on ARF10. The deferred writer, journal
adaptation, and A5 external publication are not prerequisites for deterministic
or benchmark MVPs.

## Related Plans

- [Builder Roadmap](builder-roadmap.md)
- [Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
- [Builder Conversational Development Architecture](builder-conversational-development.md)
- [Research Compilation and Autonomous-Science Evaluation Program](research-compilation-and-sota-program.md)
- [Governed Data-Driven Workflow Model Roadmap](governed-workflow-runtime-roadmap.md)
- [Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md)
- [Model Runtime Roadmap](model-runtime-roadmap.md)
- [Operational Event Model Roadmap](operational-event-model-roadmap.md)
- [Projection Subscription Roadmap](projection-subscription-roadmap.md)
- [Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md)
- [Scenario Guidance and Help Contract](scenario-guidance.md)
