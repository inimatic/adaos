# Builder Engineering Journal

Role: incremental evidence journal, not target architecture or a task backlog.

Contracts and delivery decisions belong to the [Builder Roadmap](builder-roadmap.md),
[corrective register](builder-intent-to-prototype-roadmap.md) and their linked
owners. Generic generation context must not automatically retrieve this
journal: it contains inspected evaluation cases and historical behavior.

## Entry Contract

Append dated increments with scope, exact source/runtime/model identity where
known, retained evidence, result limits, and links to decisions/tasks.
Keep missing information explicit. Do not add active checklists or restate
target contracts here. Do not store credentials or private conversation data.
A correction is a new increment; immutable raw results keep their old verdicts.

The entries below consolidate selected useful evidence from the removed dated
Builder reports and roadmap narratives. They are a summary, not a claim that
every original measurement or historical prompt was retained. Git preserves
the removed documentation; raw artifacts remain at their original identities.

## 2026-07-18 Through 2026-08-05: Control And Transport

Scope: local SDK-backed Builder control, preview, governed workflow and
Web/Telegram interaction, not autonomous generation of unseen applications.

- The SDK control fixture derived functional revisions 030/032 from reviewed
  UI 029; the functional parity contract later pins revision 042 and forward
  replacements. It is Builder-product evidence, not a generic template.
- Skill/scenario publication, declared dependencies, exact DEV activation,
  bounded file writes, conversation and idle Automation were exercised.
  The July scenario-draft path returned 504 despite changed archive bytes
  and stale commit/task metadata. Archive parity alone was insufficient.
- Workflow evidence through Core `4ec78aa5` and explicit DEV/Workspace
  versions exercised transport idempotency and scoped context.
  A later context record identified source/runtime drift and repeated Yjs
  delivery from cancelled adapters; cleanup and exact reload checks mattered
  more than another prompt change.
- Human wide/compact comparison, generated-link review and a mutating
  Telegram callback remained narrower outstanding gates. Local mechanism
  tests did not close them.
- Preview measurements separated cold bootstrap/discovery from ordinary
  selection; duplicate process/catalog work and room fan-out were diagnosed.
  Those timings are not a current HTTP latency benchmark.

Decisions now live in the SDK durable-checkpoint gate, Preview Runtime,
BC-11.2, BC-12.4 and BIP-03/BIP-22. The July 504 is not evidence of a current
Root outage. No rollout is authorized by this record.

## 2026-09-10: Legacy Boundary And Evaluation Integrity

Scope: bounded pre-decontamination snapshot, labelled
`legacy_recipe_guided`. Machine-readable source evidence remains
`e2e/builder/forensics/legacy_recipe_guided_20260910.json`.

The snapshot recorded a 53,983-byte Applications recipe and selected
Core/DEV/Client ownership surfaces. Across retained Applications revisions,
25 canonical telemetry records reported 420,485 input, 50,048 cached and
60,233 output tokens. This mixed experimental history was not a controlled
autonomy baseline. Raw prompts and uniform browser/stage traces were not
all retained; later source or browser reconstruction cannot recover them.

Decisions: explicit domain packs, no subject-triggered generic routing,
model-input attribution, calibrated evidence labels and a sealed successor
baseline. Current completion is owned by BIP-01/BIP-04/BIP-07/BIP-09,
not the former forensic report.

## 2026-09-11: Context, Stage Boundaries And Model Probes

The retained volunteer comparison used one GPT-5-mini run with a repair:
88.6 seconds, semantic failure after repair. Its GPT-5 reference took
39.2 seconds and compiled before an outcome gate rejected overlap enforcement.
Browser execution was off and cache conditions differed. The latter gate
incorrectly demanded Automation evidence during Prototype; neither historical
verdict was rewritten into a pass.

Other effort/capacity probes showed that increasing reasoning/output capacity
removed some truncation but did not cure requirement interpretation, hidden
compiler constraints or repair regressions. The required-reference inventory,
exclusive state operands and scoped preservation checks address contracts;
they do not guarantee unseen-task success.

Decisions: separate executable local Prototype behavior from disclosed
Automation obligations, preserve complete model inputs/outputs, evaluate
reasoning/capacity/context changes independently, and do not impose optional
UX richness as a hard requirement. Owners: BIP-06/BIP-10/BIP-13/BIP-14/BIP-30/
BIP-31. Historical credit/schema canary incidents are not standing roadmap
tasks; current provider conformance and calibration are the durable goals.

## 2026-09-12: Small Prototype And Capability Qualification

Evidence root: `e2e/artifacts/builder/`.

- `structured-bounds-gpt5-20260912-02/response.json` records
  `status=succeeded`, `ok=true`, model GPT-5. This resolves the earlier
  quota-obstructed canary in that evidence, not today's account balance.
- `context-archetypes-gpt5-low-20260912-16` reported 16/16 fresh cases,
  eight primary successes and eight after one repair.
- `context-archetypes-gpt5-low-20260912-22/report.json` reports 8/8,
  ten generation/repair calls, 62,147 fresh input, 18,944 cached input and
  48,593 output tokens. Eight grader calls are accounted separately.
  One complete cohort is not a repeated reliability baseline.
- Separate capability probes exercised calendar commit, query toolbars,
  modal/detail selection, wrapping/alignment, board dragging/compact move,
  tree/accordion/chart/tabs/settings and project Conversation visibility.
  Unsupported interactions remained explicit, not inferred from screenshots.
- Keyed resource lookup and append-oriented traces reduced retained local
  in-process read measurements from 576-655 ms to 19-21 ms. This is storage
  mechanism evidence, not API/browser performance proof.

Decisions: keep development/holdout sets distinct; qualify actual component
effects and scope; keep storage, provider and end-to-end timings separate.
Current gates: BIP-08/BIP-13/BIP-16/BIP-17/BIP-25.

## 2026-09-13: Frozen Automation Cohort

Scope: sequential DEV Builder Automation from retained Prototype 002; no
prototype regeneration or manual generated-application source repair.
Trial/publication were paused. Equipment's accepted prototype was preserved.

Evidence: `e2e/artifacts/builder/automation-cohort-20260913-<case>/`.
Each row is a retained passing journey, not a first-attempt reliability claim.

| Case | Task | Browser proof | Scope |
| --- | --- | --- | --- |
| Household budget | task.01M2DM0CRQZ0MJS5Q49HQVZ6HF | journey-05, 42 steps per viewport | DEV local |
| Service appointments | task.01M2DNHEKZ3GMWGDJHEBY8PXVX | journey-02, 60 steps per viewport | DEV local |
| Inventory/procurement | task.01M2DPZFW3J0XK7BPQ6VV76YMA | journey-04, 72 steps per viewport | DEV local |
| Volunteer roster | task.01M2DR64GPKMHD24HJHZXZG8SN | journey-04, 48 steps per viewport | DEV local |
| Operations queue | task.01M2DT42C8V2199R44K0TKGSW8 | journey-04, 35 applicable steps per viewport | DEV local, drag/menu alternatives |
| Knowledge library | task.01M2DTPPKPQAWKYVBSJ8VVF71C | journey-04, 46 steps per viewport | DEV local |
| Media review | task.01M2DVYS1YWR4QR46Z20MQZG1Q | journey-01, 74 steps per viewport | Local-only partial |

All rows have separate independent HTTP evidence. DEV-owner and anonymous
checks do not qualify installed delegated reader/writer behavior.

Media's first task stopped because a Prototype handoff introduced external
team notifications absent from the original request and without channel/
recipient/callable contract. The local-only iteration explicitly retained
that blocker. Real image/video, failure display and edition/comment/status
work passed; full acceptance did not. `visual-02` later timed out on compact
startup/filtering; an unchanged passing retry does not erase that failure.

Shared findings: related captions excluded skill reads, item.details dispatched
only one named action, board event documentation omitted displayed revision,
and a global runtime import lock included expensive path resolution.
Core/Client fixes and focused tests exist. The profiled live API still used
the old runtime when restart was blocked; no loaded-fix speedup was established.

Roster's archived preceding model outputs have four matching byte-count/SHA-256
receipts. Budget/Service outputs overwritten before archive support cannot be
recovered. This is development evidence, not a pristine first-attempt baseline.

Owners: BIP-03/BIP-10/BIP-15/BIP-16/BIP-26/BIP-27/BIP-28.

## 2026-09-14: Documentation And Boundary Audit

Scope: all 17 original `docs/**/builder-*` files, linked Client ownership,
selected code/tests and retained reports. No new model calls, browser
execution, remote probe, API restart or publication.

Audit starting Core commit: `0daeaf52f`; recorded Client gitlink:
`6ec45ec35ecf62cd04698af71088523c29cea3ab`. A separate concurrent Client
checkout change is outside this documentation commit; no current runtime
equivalence is inferred from either gitlink.

Code inspection confirmed:
Prototype SDK still selects `LegacyDevSkillPrototypeExecution`;
Brief outcome/actors/entities can be unknown; the Client inventory is generated
from handwritten definitions rather than being their authoritative source;
cancelled non-form confirmation can still be returned as action success.

The old corrective roadmap had 189 unchecked entries and 174 checked entries.
Open entries comprised 122 must, 45 should, eight could and 14 deferred.
They mixed repeated experiments, mechanism tasks and full acceptance gates.
The replacement register contains 37 non-deferred work packages and eight
deferred packages. Three narrow implementation gates are verified; the obsolete
credit/canary follow-up is replaced by current provider/calibration goals,
not treated as a newly successful historical regrade.

Local checks: 218 tests passed in 29.67 seconds across semantic Prototype,
resource workbench, attribution and generic capabilities; an additional
overlapping intent/SDK/context/repair/acceptance selection passed. All seven
specified retained browser journey reports were reread with
`passed=true`; their Media/latency limits above remain unchanged.

Post-consolidation checks: 28 functional-parity/context/grading tests passed.
The corrective register has 45 unique task IDs/checks: 37 non-deferred and
eight deferred, with three narrow verified gates. No active checklists remain
in the journal or Builder architecture/guides. All 134 local file/fragment
references into Builder Markdown documents pass validation across the docs
tree; all 13 remaining Builder files pass strict UTF-8 decoding. Git whitespace
checks pass; line-ending conversion notices are not treated as UTF-8 errors.

Decisions: keep target contracts and implementation boundaries in architecture/
roadmaps, one incremental journal for observations, no parallel dated plans.
Durable human, SDK, cold-context and installed-delivery gates remain visible.
No completion percentage or new autonomy claim follows from consolidation.

## 2026-09-14: Builder Workbench Design Specimen

Scope: preserve the operational Workspace Builder, then author a native DEV
interface for human design review. This is not a fresh Builder generation
benchmark and not implementation of the new process controller.

Before editing, DEV/Workspace owned handlers, tests and UI content matched;
differences were version/updated metadata and DEV-only prompt state. Workspace
Builder skills were ready; active-slot handler hashes matched DEV. The retained
UI baseline is a **fresh observation** at revision 060, not a reconstruction of
the absent older revision files. Workspace UI remains equivalent to it except
for UI version (Workspace 0.2.89, observed DEV 0.2.90).

Design iterations 061-064 were recorded locally without replacing revision
files. Revision 064 is materialized in existing `desktop-dev`; no Webspace was
created. The actual Change is `builder-workbench-design-20260914`, registered
through the workflow SDK, with Prototype acceptance required and absent,
Automation not started. Twelve local specimen states distinguish decisions,
checking, required input, partial results, failure, disconnection and stop.
Equipment records and their 002/003 revisions are explicitly synthetic; the
real feedback target is `dev:scenario:builder`, design revision 064.

Validation: 42 Python tests passed (nine design-contract tests and 33 existing
Workbench tests). Browser probes at 1440x1000 and 390x844 passed 18 checks and
retained 40 screenshots. They exercise distinct acceptance/start transitions,
clarification, stop, all specimen states/views, explicit revision navigation,
selected-record edit/save/reopen, diagnostics settings and opening the existing
Dev Tickets screenshot entry with the exact DEV design target. No tickets were
created by the probe; screen-share permission, screenshot upload persistence
and the shell's implicit target inference are **not** qualified by this run.
Two additional read-only wide/compact browser checks confirmed the operational
Workspace surface remains present; the design controls are absent there.

Design corrections exposed by execution: modal actions require `params.modalId`;
form record hydration uses a data source plus selected record, not `$state`
strings in `defaultValue`; edits must update both the table and reopened form;
historical inspection must hide current-candidate editing/acceptance. Client's
long adaptive-toolbar menu can be occluded by a later toolbar's stacking
context. The specimen now uses a modal selector; the generic defect, narrow
table minimum width, compact command density and global feedback scope remain
explicit C3 tasks. EN/RU control resources exist, but Russian specimen prose
and complete locale/long-content qualification remain outside this RU probe.

An attempted local source snapshot at existing version 0.2.127 was correctly
rejected as a different immutable release digest. Retrying with a patch version
retained local ProjectRelease `builder@0.2.128` using
`adaos dev project push builder --local-only --bump patch`.
Release digest: `sha256:5c3afd5f20fea59dd2786519231f85dbfe4ef58b5314c28aad925e0eecde58ac`.
Source revision: `sha256:75824976dbabf06c40fa552e984256f28491e3fb6230c290bb5cf2f0ac0792c1`.
This is an unapproved design checkpoint, not a stable/Trial promotion or
remote publication. UI-revision observations remain local development evidence.

Evidence root: `e2e/artifacts/builder/workbench-design-20260914/`, especially
`workspace-preservation.json`, `design-receipt.json` and
`browser-064/report.json`. Authoring source:
`scripts/build_builder_workbench_prototype.py`; browser reproduction:
`e2e/stand/browser/builder-design-review.mjs` with local DEV hub credentials in
the environment. Current implementation gates remain BIP-02 and BC-11;
human review is pending. Core runtime and Client implementation were not changed
or restarted. No Git push was performed.

## 2026-09-14: Workbench Resources And Decision Revision

User review requested a clearer menu, revision scope, resource hierarchy,
multiple clarification questions and the delivery path after Automation.
Design revisions 065 and 066 retain immutable observations; 066 is materialized
in the existing `desktop-dev`. Workspace files and both owned handler hashes
still match the earlier preservation receipt. No new Webspace, real Trial,
model request, acceptance or publication was created.

Decisions are incorporated in the Workbench target architecture and BIP-02,
not owned by this journal. The specimen separates equipment business records
(previously ambiguously titled `Assets`), reference Inputs and the generated
File tree. Files are abbreviated revision-scoped examples, not an actual host
filesystem. A real retained screenshot demonstrates resource-image inspection.
Local file selection stores metadata only; it explicitly does not upload bytes
or include them in an LLM request. Generated-file changes route to a scoped
conversation draft. Two required clarification answers and one optional answer
can be saved/reopened without continuation; incomplete submission stays blocked.

Seventeen fixture states include task/actor/milestone cues and explicit Beta
preparation, installation, acceptance, local Stable release and public Stable
publication decisions. Alpha remains DEV, not an extra acceptance stage. These
are design simulations, not evidence that delivery implementation passed.

Generic Client corrections: adaptive-toolbar menus use Ionic root overlays,
bounded wrapping option labels, keyboard opening and focus return; a short
authored trigger may remain unchanged by selection. `ui.actions` ABI/types/
catalog now describe this and the existing selection payload. Hidden widget
hosts leave layout entirely instead of accumulating empty gaps. Media previews
resolve `resource:<id>` images/posters through the page registry. No component
contains Builder/equipment workflow logic. Client inventory and boundary checks
pass; the inventory generation produced no content change.

Verification: 141 distinct Python tests passed across Workbench, design, WebUI
schema and capability contracts; 32 focused Client tests passed. Final browser
probes pass 30 checks across 1440x1000 and 390x844 with 74 screenshots, no page
errors and no document-width overflow. File-view spacing is 8px on both
viewports after hidden-host removal. Two additional read-only checks preserve
Workspace Builder. Retained report: `browser-066/report.json` under
`e2e/artifacts/builder/workbench-design-20260914/`.

The probes found two real defects before passing: overlay dismissal initially
lost trigger focus, and default form commit policy did not retain a partial
answer. Later, the spacing probe incorrectly counted explicitly enabled
Diagnostics as empty space; it now disables Diagnostics before measuring gaps.
The probe also awaits the selected process title before subsequent actions,
so feedback cannot race a requested fixture switch. No screenshot tickets are
automatically created; the existing screenshot entry is exercised, not its
permission/upload lifecycle. Full EN/RU prose, light theme, keyboard/APG audit,
outer compact collapsible wrappers and dense navigation remain review debt.

Checkpoint: Client commit `3398fc0`; local ProjectRelease `builder@0.2.129` via
`adaos dev project push builder --local-only --bump patch`.
Release digest: `sha256:b224725b97ea778956a9b34c51bcef00f938959d5f638642bb078dfb02be4c30`.
Source revision: `sha256:e63b1d86617d054589b44b31b0fa75ba211665a36d0ec4676946827727d4c48c`.
The source snapshot includes deployable assets; local UI-revision history is
separate development evidence. Core Client gitlink and `.sha` are aligned.
Human acceptance and real bindings remain open. No Git push or API restart.

## 2026-09-14: Workbench Compatibility And Shared README

The next user review restored the full application identity and asked for old
Builder parity, component-scoped Files, informal conversation, platform feedback
and public documentation. Revisions 067-069 retain immutable design evidence;
069 is materialized in the existing `desktop-dev`. The full name wraps in the
header, including a long suffix; the bounded menu uses the same name. The
identity line is `Revision 003 / Change 12 / design 069`, without an unexplained
duplicate head or a phase-independent `Prototype` label.

Files selects Project, Scenario, owned Skill or read-only dependency before
rendering its version-scoped tree. Revision/member changes clear file selection.
README has one local draft shared by rendered view, editor and tree inspection;
save/reopen, cancel and read-only historical inspection are exercised. This
does not write a live project file or publish documentation. The target adds
the real shared human/LLM artifact with digest preconditions and conflict review.

Native `ui.chat`, adjacent scope menus and local forms model formal and informal
dialogs. Correction/requirement/discussion intents remain available; unsent
drafts survive scope changes without mixing. A confirmed informal proposal
cannot inherit an unrelated earlier file request. Channel identity is separate
from task scope; Telegram remains explicitly disconnected. Development Feedback
has a dedicated surface and a reviewed Core/Client request with consent before
dispatch. The specimen records consent only, not a fabricated delivery receipt.
Settings and the local Preview link/QR use existing generic components; the QR
does not claim that a loopback address is reachable from another device.

The old operational UI and functional-parity manifest were compared explicitly.
BIP-02 now owns the replacement parity matrix, including unmodeled creation,
metadata/lifecycle operations, technical specification/addenda, stable updates,
initiator provenance and full feedback triage. The old settings form displayed
profile/provider/voice but forwarded only model: future qualification must check
effective application of each setting, not just field presence. No new
Builder-specific renderer or chat transport was introduced. Concurrent Core Yjs
and Client page-data/build changes were left outside this change.

The browser caught a real initial README binding error: nested state-reference
text in a tree record was not dereferenced by the viewer. The specimen now
binds the viewer explicitly to the canonical local draft. A later extended
probe incorrectly selected an Angular option by its serialized value; the
probe now selects its visible label and still verifies the semantic intent.
These are distinct findings, not reasons to relax the acceptance checks.

Verification: 88 distinct Python tests passed across design, Workbench and WebUI
schema; the final design/schema rerun passed all 55 cases. Final DEV browser
qualification passes 44 checks at 1440x1000 and 390x844, with 98 screenshots,
no page errors and no document-width overflow. Two additional read-only browser
checks confirm the operational Workspace surface is preserved. DEV evidence is
`e2e/artifacts/builder/workbench-design-20260914/browser-069/report.json`.
The local package was inspected: it contains revision 069 and all three
referenced resource files. Full EN/RU content, light theme, live transports,
actual file writes/conflict handling and overall replacement acceptance remain
open; this evidence qualifies the local design, not operational equivalence.

Checkpoint: local `builder@0.2.130` via
`adaos dev project push builder --local-only --bump patch`, with 56 source files.
Release digest: `sha256:db268318baba3e397bd14793cb98cf589461b7b00b554f879e131b81c42c6230`.
Source revision: `sha256:1c2f2ef4c63e4cab4eaf0c9c8695fb29913d48ab1d8e2613d070c27929c35fa6`.
Workspace UI and owned handler hashes match the preservation receipt. No Git
push, Workspace replacement, new Webspace, LLM call or API restart was performed.

## 2026-09-14: Message-To-Result Design Traceability

DEV Builder design 070 replaces Task with Scope and models linked inspection
of original messages, versioned requirements, execution tasks, attempts,
partial/corrected results and checks. A single human-authored fixture in
`scripts/fixtures/builder-workbench-trace.json` supplies 23 illustrative records;
the generator derives bidirectional projections and exact UTF-8 input snapshots.
This fixture is not loaded by the production Core or a model prompt/template.
The architecture keeps requirements in the existing Issue model and execution
tasks in the existing journal; BIP-02 owns the remaining canonical bindings.

The specimen covers one Change's Prototype 003 scope: one request introduces
four requirements, a subsequent clarification creates R1 v2, one requirement
belongs to Automation and README is explicitly deferred. Context-only, informal
and later unprocessed messages do not acquire invented execution tasks. P16
retains its original input and failed E16 result after the P17 correction and
E17 check. Inspection never changes the Preview or current Change. Historical
002 has an explicit unavailable trace instead of substituted 003 evidence.

Native chat message actions and linked-record inspection connect Conversation,
Scope, Process, Result and Review without another top-level menu or graph
editor. Review lists current obligations and evidence separately from future
or deferred work. All trace/check records are marked as illustrative: the
actual design remains unaccepted and Automation remains not started. This
does not prove live trace capture or consistent projection across all simulated
process states. Full locale/light-theme qualification and operational parity
remain open; compact tables and raw JSON still use bounded horizontal scrolling.

Browser qualification exposed a generic Client defect: long Markdown code
expanded the details grid beyond its modal. Explicit zero-minimum grid tracks
and bounded scrolling now preserve all input bytes without enlarging the panel.
The Client change is CSS-only, has a rendered narrow/wide regression test, and
does not alter the ABI or add Builder-specific rendering logic. Early probe
failures also identified hidden-view and modal-render timing assumptions in
the harness; these were corrected without relaxing the functional assertions.

Verification: 93 distinct Python tests passed (60 design/schema, 33 Workbench),
34 Client details/Markdown tests passed, and the capability inventory remains
current at 41 widgets. DEV browser qualification passes 50 checks at 1440x1000
and 390x844, with 108 screenshots, no page errors or document-width overflow.
Evidence: `e2e/artifacts/builder/workbench-design-20260914/browser-070/report.json`.
Workspace UI and owned handler hashes still match the preservation receipt.
Two additional read-only browser checks confirm its operational wide/compact
surface remains available after the generic Client rendering correction.
Concurrent Core Yjs and Client page-data changes are excluded from these commits.

Checkpoint: local `builder@0.2.131` via
`adaos dev project push builder --local-only --bump patch`, with 59 source files.
Release digest: `sha256:bed0014f74fd873434b81ae168245ce339f19ad7c7f5269a69e50489d94bb8f5`.
Source revision: `sha256:882028cdef03e73a96f7560255737a0c59d20b833c711777d416daf6adde872a`.
The packaged UI contains revision 070, its 23 trace records and all three
referenced resources. Client commit `c652ccc` is pinned with matching gitlink
and `.sha`. No Git push, Workspace replacement, new Webspace, LLM call or API
restart was performed.

## 2026-09-14: Basic And Detailed Workbench Presentation

DEV Builder design 071 adds Basic (default) and Detailed profiles in one schema,
with shared workflow state, records and actions. Basic retains Result, Scope
and Conversation, with a compact requirement list linked to the same trace
records. Technical sections remain reachable through Sections; a focused section
stays open when the profile changes. Detailed exposes their direct navigation
and full requirement/source projection. Neither profile hides critical status,
limitations or decision consequences, or resets revision, selection or drafts.

The generic Client command menu supports opt-in `rememberSelection` for static
options and a safe top-level state key. Only the declared scalar preference is
stored, scoped by subnet/page/widget/button. Restore does not dispatch actions;
obsolete values, dynamic menus, failed actions and scope changes do not write
unrelated state. Opt-out menus perform no preference storage lookups. Schema,
types, capability catalog and Client tests share this contract; there are no
Builder-specific branches in the renderer. This is presentation persistence,
not workflow authority, document storage or operational replacement.

Verification: 73 Python design/schema/contract tests and 37 Client command-menu,
scoped-storage and page-state tests pass. Client boundary checks pass and the
capability inventory remains current at 41 widgets. DEV browser qualification
passes 56 checks at 1440x1000 and 390x844, with 114 screenshots, no page errors
or document-width overflow. Evidence:
`e2e/artifacts/builder/workbench-design-20260914/browser-071-final/report.json`.
Two additional read-only browser checks preserve the operational Workspace
surface. The screenshot probe waits for rendered summary rows and scrolls them
into view; an earlier run was interrupted by a development-server reload during
a Client edit, so the final qualification runs without source changes.

Checkpoint: local `builder@0.2.132` via
`adaos dev project push builder --local-only --bump patch`, with 62 source files.
Release digest: `sha256:e964bad9feba548a4579e5d3e2811c4ccbf79700251ff00e5dad00f0ff5db336`.
Source revision: `sha256:a8eafdc29db13a9d33cf26c841657b77981a5dfee921b7bd455c3c6229467c14`.
Client commit `f00e93e` is pinned with matching gitlink and `.sha`.
The package contains UI 071, the Basic default and referenced resources. Actual
acceptance is pending and Automation is not started. Full locale/light-theme
qualification and canonical live trace bindings remain open. Concurrent Core
Yjs and Client page-data changes are excluded. No Git push, Workspace replacement,
new Webspace, LLM call or API restart was performed.

## 2026-09-14: Accepted Design And Initial Automation Prerequisites

The user explicitly accepted Builder design 071 and authorized Automation plus
a complete isolated TEST journey. The acceptance is now recorded through the
public workflow SDK, tied to revision 071 and its browser evidence; workflow
generation advanced to 14. The receipt is
`e2e/artifacts/builder/workbench-automation-20260914/design-acceptance.json`.
The immutable design and operational Workspace Builder were not rewritten.

The first acceptance attempt exposed a generic scope-interpreter defect:
"Do not implement ..., create ..." became a required create operation. Explicit
negative implementation/include clauses now remain exclusions with exact source
spans; contrasted positive clauses remain required. This is not a Builder
exception or acceptance bypass. 77 intent/capability/handoff tests passed, then
the original design passed the unchanged acceptance gate.

Initial model accounting work separates stored Codex preferences from Prototype,
adds a Root-catalog SDK and records explicit CLI model evidence per attempt.
Automation passes actual known model identity to Root; unknown historical
identity is not reconstructed. Root development profiles support Automation
scope, per-model token buckets and optional operator-managed tariff estimates.
The subscription SDK retains bounded model breakdowns. No live Root policy or
Subscriptions UI has been qualified yet; selection UI, complete cost projection,
durable specification deltas and the actual Builder Automation remain open.

Verification of this prerequisite slice: 37 model/preferences/subscription/SDK
tests and six focused Automation usage tests passed. Backend TypeScript build
and seven usage/cost tests passed locally. These are not end-to-end results.
No model execution, Trial, publication, Git push or API restart occurred in
this increment. Concurrent Core Yjs and Client page-data changes stay excluded.

## 2026-09-14: Live Automation Admission And Model Cost Projections

Admitted `task.01M2G9QQPVHZHA1ETXRGB6BWSX` through the Automation SDK for
`project:builder`, accepted design 071, on host `desktop-dev`. The execution
profile explicitly pins `gpt-5.5` / `medium`; Root read-only MCP discovery is
disabled for this task, which has an admitted local SDK reference. The complete
stage-specific implementation brief is retained in
`e2e/stand/fixtures/builder-workbench-automation-20260914.md`; start/context
receipts are under `e2e/artifacts/builder/workbench-automation-20260914`.
Input inspection confirms the full brief, immutable design identity and owned
scenario/skills envelope. The source packet also contains historical design-only
requirements and an old not-started Automation source revision; the brief explicitly
separates these from current implementation authority. The Run is not acceptance:
live bindings and independent browser/lifecycle parity remain to be verified.

Root model buckets and cost windows now distinguish fully priced, partly priced,
unpriced and zero-model work, using retained event tariffs. The SDK sanitizes these
projections and the DEV Subscription status skill has a per-model 24h usage table.
No production subscription skill was replaced. Backend build and eight metering
tests passed; 20 SDK/skill tests passed before the additional model-table regression.
Root deployment, policy population, model-selector behavior and actual Run receipt
verification remain open. No invented default prices are configured.

## 2026-09-14: Accepted Design Is Not The Publication Baseline

Cancelled the Builder self-automation Run before activation. Its first result
implemented only a subset of the brief. Repair then restored the legacy layout:
the input called the old publication an implementation baseline, package tests
asserted obsolete widget identities, and validation inspected immutable retained
publication files against the current ABI. These are admission/test-boundary
defects, not evidence that design 071 should be changed. Builder implementation
is now performed directly; LLM/Codex qualification belongs to an isolated TEST
application, as clarified by the user.

The worker now treats publication as a behavior reference, excludes retained
baselines from candidate validation/package projection, and protects the accepted
revision and previous Automation evidence. Cancellation/expiry releases the
workflow instead of leaving a working projection. Capability parity must replace
obsolete layout assertions before the live Builder is qualified.

Canonical workflow now admits bounded, stage-scoped specification deltas with
Issue/message references and base digests. Prototype evidence and an Automation
checkpoint merge only their own specification layer; stale bases fail closed.
UI/editing and full lifecycle qualification remain open.

Backend model/cost code is deployed. Root policy was updated through the audited
operator API to advertise separate GPT-5.4/GPT-5.5 Codex profiles, preserving
Prototype choices and access policy. RU relay returns the EU catalogue. Tariffs
remain unset; unpriced usage must not be displayed as free execution.

Packaged Builder tests reproduced a 60-second timeout, then all 306 passed in a
diagnostic run of 171 seconds without one stalled test. Investigating repeated
handler compilation under disabled bytecode writes; the production timeout is
unchanged. Diagnostic reports remain in the workbench Automation artifact folder.

The compiled-code reuse test-harness change reduced the exact packaged suite
from 171 to 25 seconds: all 306 tests pass under the unchanged 60-second worker
budget. Each test still constructs a fresh module; only immutable bytecode is
shared. No test was skipped. The broader Core suite had one obsolete prompt-text
assertion; it was migrated to assert the stronger accepted-layout boundary, and
the focused five-test regression rerun passes.

## 2026-09-14: Direct Workbench Integration And Legacy Capability Reuse

The accepted design 071 remains immutable. A Builder-owned migration now uses
its presentation with the operational Workspace picker, template creation,
component file tree, Preview/QR, metadata, review/start and delivery controls.
New bindings expose canonical Change/Issue/Run state, separate saved model
profiles, a digest-checked README and explicit specification deltas. This is
direct implementation, not an LLM-generated Builder candidate or a generic
application template. Workspace was not promoted or rewritten.

Action review found an aggregate/primary mismatch in Prototype model preferences:
the editor saved to Project while execution read the primary component. Both
Prototype and Codex settings now use the same resolved execution identity; a
regression protects that boundary. Root RU exposes the separate Codex catalogue.

The first browser report was structurally green but its screenshots included old
design chat fixtures. A generic Client defect retained the previous subscription
after a widget source replacement. Clearing/rebinding the source fixes it without
a Builder-specific renderer. All 19 chat tests pass. Fresh wide/compact review
`browser-live-03` has 18 navigation checks and 20 screenshots with no page errors
or document overflow; it is not write-path or lifecycle qualification.

The combined DEV scenario, SDK control and Core capability suite has 121 passing
tests. The accepted shell is still marked qualification pending. Browser creation,
effective model execution, durable clarification, informal promotion, reference
intake and independent Automation review remain open. The mutation stand may
create only an explicitly named `workbench_test_*` application and records the
exact creation response. Core/Client commits remain local during qualification;
unrelated Yjs/page-data work is excluded.

## 2026-09-14: Native Chat Qualification After Picker Reuse

Browser tests exercised the retained TEST/non-test filters, scenario-default
creation and separate model save/reopen at the primary component. A late form
state reset erased an already received project projection; the reset was removed.
DEV Builder 0.2.133 was checkpointed locally, without publishing Workspace.

The first native request was misrouted: "create the current application's
prototype" entered legacy project creation. A bounded existing-target guard and
EN/RU regressions now retain that target. The next request exposed an E2E/native
route mismatch: native chat still emitted renderer patches with a 5,000-token
budget, whereas the qualified adapter used semantic v2. The native empty-canvas
and semantic-source routes now use the semantic contract; authored legacy WebUI
is not implicitly converted. Input text fell from 42,888 to 25,567 UTF-8 bytes
(provider schema excluded from these message-byte measurements).

TEST `workbench_test_20260914_b` produced revision 001 through chat. Wide/compact
rendering passed, but this was not acceptance: the output language contradicted
the admitted RU Brief, title validation was absent, and the header remained
stale after the background result. The initial interaction probe stopped at lost
focus after dismissing the detail editor. The locale policy now uses the Brief,
wrapped requirements no longer lose unfinished lines, and follow-up input must
include the digest-matched current semantic source. Generic chat invalidation
and focus-preserving pending detail commands were added; 46 Client unit tests
pass. The combined scenario/SDK/Brief suite has 160 passing tests.

The next explicit review request is being qualified against revision 001.
No new Prototype acceptance, Automation success, Trial, release or replacement
of Workspace Builder is claimed. Mutation observers retain terminal failures
and never automatically resend paid requests. Cohort browser probes are reused
through a provenance adapter for actual UI creation/preview receipts.

## 2026-09-14: Async Wait Diagnosis And Same-Job Recovery

The next TEST follow-up was marked failed locally after 150 seconds while Root
still reported running. Read-only retrieval of that exact job found a completed
response after 260.678 seconds, 13,789 input and 6,761 output tokens (3,072
reasoning). RU returned the complete 13,354-character output without fallback;
no new generation was submitted for this diagnosis. Root's reported 1.8-second
TTFT is its first provider event, not proof that visible text began that early.

Builder's default async wait is now 600 seconds, separately configurable up to
1,800; submission remains 15 seconds and ordinary polls remain short. The SDK
retains observation progress and transport errors. Exhausted observers become
interrupted, not proof of provider failure. Replay now restores the original
semantic mode/Brief from digest-checked input and retains original usage separately
from incremental cost. Twelve focused Builder and 23 SDK tests pass. The Core
semantic/context suite also passes (184 tests).

`root-recovery-01` retains the full request/response and read-only replay result.
Normal validation rejects an invalid-form requirement modeled as a stored sample
and an Automation disclosure bound only to a resource. This is not an accepted
Prototype. Generic field-validation guidance now distinguishes required inputs
from conditional guards and stored-state fixtures. Replaying the paid response
must not silently weaken these findings.

Browser interaction reruns still stop at editor focus restoration on both widths.
The earlier pending-button change was insufficient; focus tracing is retained
before another Client correction. No Automation, Trial, stable or Workspace
replacement has been accepted in this increment.

## 2026-09-14: Recovered Candidate And Independent CRUD Review

An explicit bounded repair reused the retained rejected candidate and its exact
Brief. It produced TEST revision 002: 63.574 seconds, 15,533 input and 7,271 output
tokens (3,648 reasoning); 60 successful polls, no transport errors. The original
20,550 tokens are recorded as reused-source usage, not charged again as repair.
The context now explains required-field validation independently of stored state
fixtures. A misleading state identifier remains a context-quality finding; it
does not substitute for an invalid-form interaction.

Browser tracing identified focus capture after Ionic overlay creation as the
remaining cancellation defect. Capturing before creation restores the opener on
both widths. An AOT access error also left the development server serving the
previous successful bundle; verification now retains actual loaded method bodies.
The focus/modal/chat/details suite has 86 passing tests. A separate CRUD review
found optional choices submitted as empty strings rather than nullable values.
The generic form fix preserves explicit empty choices and read-only context;
all 39 form tests pass. No generated application code was hand-edited.

`prototype-002-crud-03` covers wide and compact create/read/edit/delete, search
by either text field, choice filtering, empty results, mandatory-title rejection,
empty optional inputs, cancelled create, cancelled deletion and collection
refresh. Cleanup-only provider writes are distinguished from user interactions.
The visual report has no document overflow; the compact table remains internally
scrollable. The scaffold header name and untranslated empty-details fallback are
retained UX debt, not missing CRUD. No independent Automation or delivery pass is
claimed. The complete DEV skill suite has 340 passing tests; SDK/context has 34.

The timeout policy follows the separation between background execution, polling
and explicit cancellation described in the
[OpenAI background-mode documentation](https://developers.openai.com/api/docs/guides/background).
This is a design reference, not a claim that Root's job wrapper uses provider
background mode. Provider-event latency, first visible text and total execution
must remain distinct metrics.

## 2026-09-14: Executor Availability Before Automation

TEST revision 002 received delegated agent Prototype acceptance backed by the
retained desktop/mobile visual and CRUD reports. Native Builder then submitted
its first Automation request. The task failed before generation: Root allowed
`gpt-5.4`, but the local ChatGPT-authenticated Codex rejected that model. Increasing
the execution timeout cannot fix this failure. Full input, profile and failure
evidence remain in `workbench-automation-20260914`; no worker result was accepted.

Public app-server `model/list` discovery completed locally in under 200 ms and
starts no model turn. The SDK now intersects this executor catalogue with Root's
operator permissions, checks reasoning support, and refreshes at admission.
Explicitly selecting an available profile for a follow-up retains the previous
profile/task/iteration instead of rewriting failed execution evidence. Combined
Core model/SDK/Automation regression checks pass (205 tests before the additional
profile-history case). The next TEST iteration explicitly selects `gpt-5.5`;
Prototype remains `gpt-5`. Root tariffs remain unpriced, not zero cost.

The actual failed-task context retained the complete Russian Automation brief,
accepted revision/digests, scoped files and empty production seeds. Required
context was not truncated. A historic Prototype-only instruction also remains
as traceable source text; its stage must not override the current Automation
brief. This is a context-review concern, not evidence that the model executed
or misunderstood the task. Workspace Builder is still unchanged.

## 2026-09-14: Native Automation And Manifest Gate Gap

The native follow-up ran `gpt-5.5 / medium` against accepted TEST revision 002.
The first implementation failed two packaged tests: Cyrillic case-insensitive
search and a test walker treating JSON-schema union types as hashable strings.
One worker repair corrected both; packaged skill/scenario tests passed. The
validated worker run lasted approximately eight minutes, without an execution
timeout. Reported cumulative usage across both attempts was 1,667,692 input
tokens (1,524,096 cached) and 19,614 output, including 3,623 reasoning tokens.
These are cumulative agent-turn inputs, not a single context-window size.
Root retained the explicit model usage receipt; no tariff was invented.

Finalization then stopped at skill checkpoint: `max_request_hz` and
`preserve_last_value` had been placed outside `read_policy`. This is an actual
manifest error, not network loss or an older Root schema. Worker validation had
checked route behavior but omitted the full skill schema used by checkpoint.
The worker now shares that complete schema check with install/checkpoint
validation. The retained result is not independently accepted or delivered;
the next correction addresses the current implementation, not a new Prototype.

The workbench now distinguishes a failed execution from its retry-ready gate,
without inventing different command permissions. Locale coverage includes all
current governed states. The obsolete stand that could send Builder itself to
Codex was removed: Builder is implemented directly, and model work stays on the
isolated TEST application. DEV SDK-control tests pass (103); Workspace unchanged.

## 2026-09-14: Independent Automation Runtime Qualification

The next native `gpt-5.5 / medium` iteration corrected only the malformed manifest
and added its regression check. One model attempt completed in approximately two
minutes. The exact retained prompt was 30,972 UTF-8 bytes and included the complete
original brief, current correction, accepted Prototype identity and verification
ownership. No required context was omitted. Root acknowledged 239,990 input
tokens, including 190,848 cached, and 3,143 output, including 245 reasoning tokens.
The narrow correction did not repeat the full brief as a second addendum.

Full skill-schema checks, packaged tests, DEV activation and both Forge
checkpoints passed. Independent `automation-http-01` passed 15 checks, including
null inputs, Cyrillic search, server-side validation and stale-update refusal.
`automation-browser-02` passed actual wide/compact create, edit, cancellation,
delete confirmation, empty search and persistence after page reload. The first
browser report exposed an evaluator race: it read the editor before async data
arrived. Waiting for the expected field value fixed the check without editing
the generated application. A real local API restart preserved both retained
HTTP records byte-for-byte at the JSON value level; `automation-restart-02`
then removed only those evidence-owned records.

The stronger Builder browser stand now waits for the selected application and
stage projection, not just HTTP 200. Both widths show the completed TEST task.
Independent reports are still external: automatic Forge checkpointing projects
trial-ready state, while Review correctly has no consumer acceptance receipt.
Canonical linkage of external review remains open rather than fabricating a
receipt in the worker session. A new iteration also clears its predecessor's
finalization timestamp; three focused progress/finalization tests pass.

DEV Subscriptions refreshed the actual Root snapshot through the RU endpoint.
Its `gpt-5.5` period row contains fresh, cached and output usage and explicitly
unpriced runs. The task's model agrees with the native selection and Root
acknowledgement. The first diagnostic expected a nonexistent aggregate input
field; the corrected check uses the public fresh/cached fields. No accounting
implementation changed to satisfy the stand. Browser accounting presentation
and settled monetary billing are not claimed.

The first native Trial attempt reached the network-approval gate before side
effects. Its pending action is retained; the test must use normal scoped UI
approval, not inject authorization or call an unguarded internal publish API.
No Trial or stable result is claimed in this increment. Workspace Builder and
the user's unrelated Core/Client changes remain untouched.

## 2026-09-14: Trial Placement And Execution Boundary

Native TEST preparation used the normal pending-action approval UI. The next
request created an immutable Candidate and placement but returned a stale workflow
generation error after the side effect. Exact placement replay now returns the
retained result without a write or another activation; all 64 workflow tests
pass, including rejection of changed identity and safety evidence. The original
HTTP failure remains in `trial-native-03`, not rewritten as successful admission.
`trial-reconciliation-01` proves exact Candidate identity and parsed-JSON equality
with the accepted Automation UI; package formatting changes the raw byte digest.

Opening that placement exposed a release-blocking gap. The common materializer
loaded immutable UI and tool declarations but placement navigation did not persist
the Trial pin consumed by the HTTP execution guard. Four read-only calls through
arguments/context and explicit/implicit DEV all returned HTTP 200 with existing
DEV records despite `data_mode=empty`. `trial-boundary-before` retains those
responses and the unchanged DEV database digest. No Trial writes or acceptance
were attempted. Package activation explicitly skips runtime reload; the empty
Trial runtime cannot be treated as an admitted isolated executor.

The correction pins the exact Candidate/release through existing owner topology
before materialization becomes visible. Interrupted rebuilds retain the denial
boundary; SDK selection preserves the Candidate pin rather than replacing it
with a version-only pointer. Isolated execution remains an explicit BIP-28 task,
not an implied consequence of UI rendering. No LLM request or budget increase is
needed for either of these runtime defects. Core and Client remain unpushed.

After one API restart, `trial-navigation-02` opens the exact placement from
the live Builder on both widths and shows the localized unavailable-runtime
informer. `trial-boundary-after` returns HTTP 409 for all four routes with the
same DEV database digest. The combined boundary, preview, recovery, workbench
and materialization suite passes all 264 tests. Screenshots were inspected;
application localization and header are not qualified by these denial checks,
and an empty collection message is not proof of an empty isolated Trial store.
The three preserved Workspace Builder source digests still match the baseline.

### 2026-09-15: Trial Delivery Routing Correction

User review caught an architectural error in the preceding containment fix:
Trial had been opened in DEV Preview. The denial evidence remains valid, but
does not qualify delivery. Canonical architecture now explicitly assigns local
beta desktop placement to Builder before first Workspace publication and
installed channel selection to Applications thereafter. Preview is DEV-only.
Reading List's retained immutable Candidate is the repair target; its source
must not be regenerated, manually patched or promoted to stable to make a beta
launcher appear. Native isolated execution and desktop qualification remain open.

The retained Candidate now executes through the native skill engine in its own
Trial runtime/data context. Package/lock/release verification precedes execution;
original policy and caller authority are retained, declarations are scoped, and
Trial activation does not publish Workspace-installed capabilities. Native
`empty` data is admitted; other data modes and remote forwarding fail closed.
This is a bounded local executor, not general sandbox or consumer qualification.

The desktop defect had two additional causes: shared-core materialization
dropped selection-specific catalog metadata, and the skill's Project publication
path still assigned a Preview target. Builder's native `publish_project` command
now reconciles the existing production host and live catalog even when replaying
an already prepared Trial. `trial-builder-delivery-03` proves the same Candidate
is reused. `trial-browser-03` passes desktop/mobile CRUD; `trial-http-02` passes
15 checks and `trial-restart-01` proves persistence after a real API restart.
The DEV database digest remains `60315dd6423639f09f3c65e5940147701c3afda79d51cc5f8ddd446775902487`.

`trial-builder-navigation-05` passes opening the exact beta from Process on both
widths with Trial execution headers. Placement links no longer misuse Preview
stage or UI revision fields. `dev-preview-restored-01` restores the old Preview
to Automation; it is not a second Trial host. Reading List source and the three
Workspace Builder baseline files remain unchanged. DEV Builder checkpoint
`0.2.138` retains source snapshot `sha256:61028e1cf7f01087782cc035bc2199622929f5febe49f1b98eb303022237c6a9`.
The combined Core run passes 388 tests (`trial-core-tests-01.xml`); the SDK-only
Builder skill suite passes 104 tests.

Failed attempts remain evidence, not rewritten successes: runtime-02 failed on
an evidence-shape mismatch (its initial report had an incorrect passed flag),
browser-01/02 caught the missing catalog entry, delivery-01 exposed a wrong
test assumption about catalog response fields, and navigation-03/04 exposed
premature browser assertions, stale Preview link parameters, cold-start response
latency and an ID search against displayed table columns. The successful stand
waits for observed tool responses and selects the visible application title.
No new model generation, stable promotion or external distribution was used.

Final `trial-browser-04` passes both widths with mutation response release
identity assertions, return-home/reopen interactions, and an unchanged DEV
database digest. The last screen is the production desktop with the Reading
List BETA launcher. `trial-builder-delivery-03` is the final native Builder
command replay after the last Core restart. Source remained unchanged throughout.

### 2026-09-15: Empty Catalog Availability Regression

User review found a gap in desktop acceptance: successful Trial CRUD did not
prove the Client allowed stateful interactions without a `Limited` warning.
`trial-availability-before-01` reproduces the desktop-to-beta transition on both
widths: WS/YWS and materialization are fresh, but two Client widget-data guards
reject `catalog_widgets=0`. The catalog branch is present and valid; page widgets
are declared inline. Header/action gating and uploaded diagnostics now check
branch presence without demanding catalog entries. No application-specific
exception, Trial source change, network change or API restart is involved.

The strengthened browser stand records availability diagnostics and requires
fresh stateful access at desktop, beta open/reload, return-home and reopen.
`trial-availability-after-01` overlapped the dev-server rebuild on desktop;
its compact run passed after the updated bundle loaded. The failed desktop
sample remains evidence, not a passing result. Three new positive empty-catalog
unit cases failed before the fix; missing-branch rejection remained correct.
The expanded 340-spec combined run exposed three navigation-state failures;
the unchanged YDoc suite passes 155/155 in isolation. Cross-suite navigation
isolation is recorded as Client test debt, not hidden by a green aggregate claim.

Final `trial-availability-after-02` passes all 22 checks per width, including
six availability checkpoints with `state=ready`, fresh widget data and
`disable-stateful=no`. Trial mutation responses retain the exact release digest;
the DEV database and Reading List source hashes are unchanged. Desktop and
mobile screenshots were inspected. The focused AppComponent, diagnostics and
availability-flags suites pass 185/185; YDoc passes 155/155 separately. Client
commit `b78014c` contains only the catalog guard correction and its regressions.

### 2026-09-15: Modal, Stage And Content Refinement

`workbench-automation-20260914/refinement-review-06` passes wide/compact live
Builder rendering and the six-stage Process projection. The current Beta stage
remains distinct from the inspected Prototype. Wide layout also passes modal
maximize/minimize, device-scoped resize/save/reopen, and README draft generation
without saving. GPT-5 returns 263 input / 1250 output tokens (1024 reasoning).
This receipt does not qualify all-device preferences or local stable acceptance.

Earlier attempts exposed old materialized Builder source, an incorrect stand
selector, the required network approval panel, and literal `$state` values in
form defaults. Builder now uses the established `stateKey` binding, and runtime
content rejects unresolved model expressions before Root admission. The initial
README packet contained only title, empty description and existing README;
declared component/tool summaries were added after inspecting that packet.
Their browser result and README save remain to be qualified.

The current focused suites pass 152 Core/skill tests and 205 Client shell/list
tests; modal/action suites separately pass 96. Candidate acceptance now requires
exact publication evidence and the active Workspace package closure. Stable
placement uses the production owner, not Preview; its live acceptance is pending.
Unrelated in-progress Yjs/PageData edits are not part of these checkpoints.

`local-workspace-acceptance-04` completes local Reading List beta acceptance
through Process -> production placement -> scenario Changelog -> Accept into
Workspace. The exact Candidate/package digest is checked before promotion, its
publication receipt is reconciled into ApplicationInstallation/RuntimeSelection,
and the matching Trial placement is detached. Reloaded wide/compact views execute
Workspace, not Trial or DEV. This is local activation, not public GitHub release;
isolated Trial data is not copied into production. The review now states that
data boundary explicitly. A stale selection or removed DEV checkout cannot hide
other components' changesets.

The failed first attempts revealed a tree-to-list event contract mismatch:
`$event.item.placementKind` was missing on flat-list events. Both Builder stage
lists now use direct row fields; source tests and the capability catalog record
this distinction. No domain-specific exception was added to the renderer.

`refinement-review-08` passes enriched README generation and explicit reviewed
UTF-8 save. The packet has 536 input tokens; GPT-5 returns 1141 output tokens
(832 reasoning) in 25.206 seconds at Root, 24.351 seconds upstream. Inspecting
inputs exposed empty application descriptions, so component/tool declarations
provide evidence without supplying all source files. The result is technically
accurate but still too implementation-oriented for polished user documentation;
editorial guidance remains a quality improvement, not a schema-acceptance gate.
Successful document saves now advance the open form's digest for subsequent edits.

`content-generation-20260915-01` exercises an unrelated checklist schema and an
out-of-scope prompt with GPT-5 low. They pass in 9.187/8.938 seconds including
polling, with 240/489 and 225/223 input/output tokens. Root logs show one execution
per request after exact SDK retries and terminal reads. This does not prove
crash-safe exactly-once billing. Image INPUT transport is covered; standalone
image OUTPUT, image-model entitlement/accounting and browser job recovery remain
open in the content roadmap and are not advertised as implemented generation.

`modal-settings-03` passes device save/reopen, current-user all-device preference,
explicit DEV default edit, preference precedence over the DEV default, and a
fresh compact browser context loading the shared size. Browser review caught
invalid JSON characters in a preference key, missing ALPHA metadata on scenario
modals, and late preference loading overwriting an unsaved resize. The fixes use
Core-compatible encoded scope IDs, owner-preserving materialization metadata and
an interaction guard. Desktop sizing reapplies on viewport change; mobile stays
bounded to its viewport. The Client shell/list/modal suite passes 215 tests;
the generic boundary and 41-widget inventory checks pass. Full physical-device
and all-dialog-class coverage remains outside this receipt.

The final focused Core/DEV package regression set passes 180 tests. Client
`7a59ea1` fixes shared preference identifiers and resize-intent races; its gitlink
and `.sha` are updated together in Core. Checkpoints are local-only while the
remaining content-generation qualification is open.

DEV checkpoints: Builder `0.2.141` (`sha256:ff83f450945434d08dfad84fc0a4cd3896a17927a145700af66ffb430af3c7a8`)
and Reading List `0.1.2` (`sha256:bcd03040e9f0396660e452e266ec2480efa38832d37bec8d4aa018d252586c40`)
were built with native `adaos dev project push --local-only`. The latter captures
the reviewed README in DEV; it does not replace the accepted Workspace `0.1.1`.
An additional request-budget test verifies encoded image bytes count toward the
local bound before Root's 2 MiB JSON ingress limit; no image-output qualification
is implied.

### 2026-09-15: Exclusive Application Channel And Data Continuity Decision

User review fixes one effective Stable/Beta representation per local Application
installation. A separate verification Beta alongside running Stable is forbidden.
Architecture references that implied concurrent usability now describe separate
code storage with exclusive execution. Applications' channel toggle is accepted
Prototype behavior, not qualified operational switching.

The earlier Reading List acceptance retained the exact code but did not adopt
Trial records; that is a gap, not the target data policy. Read-only inspection
found two Trial records and no Workspace records; no recovery write was made.
Canonical Application architecture now requires synthetic-data development,
algorithmic forward migration of a local Workspace snapshot, preserved Beta
working data on acceptance, and explicit possible-loss consent for snapshot
restore. Backward data migration remains deferred. Open implementation/proof
items are `APP1-13`/`APP1-14`, `APP6-10`, `AP4-20`/`AP4-21` and BIP-28.
This increment changes documentation only, not runtime behavior or user data.

The subsequent clarification requires each newly published Beta to prove the
full Stable-to-candidate migration again. Each local activation seeds from its
accepted Stable baseline, not the previous Beta's working data. Beta-only writes
are explicitly at risk on the next Beta; retention and loss acknowledgement
precede reseeding, and auto-update cannot silently reset records. Replaying an
existing activation or rebuilding its runtime is not a new Beta and preserves
data. Acceptance keeps Beta data and establishes the next Stable baseline.

Workspace-to-Beta configuration/secret continuity is a separate requirement:
new Beta business-data baselines must not reset settings or credentials.
Canonical architecture and `APP1-15` distinguish versioned configuration,
approved local credential bindings and business records; secrets do not enter
model context or immutable publication artifacts. Implementation is authorized
for the discussed Builder, lifecycle/data and About/image-generation work.

### 2026-09-15: Preview Identity And Configuration Prerequisites

DEV Builder `0.2.143` is checkpointed locally, without publication to Workspace
or GitHub. The Process dropdown uses the canonical six-stage projection; its
overlay, current-step marker and compact layout pass local wide/mobile browser
review in `workbench-refinement-20260915/process-preview-02`. The first run
correctly failed against the old materialized UI; refreshing the owner projection
loaded the new source. Client command-menu tests: 26 passing.

Preview selection now retains the aggregate Application identity and its primary
scenario, including explicit Prototype revisions and the retained Automation
task. Snapshot reads verify exact task identity and new content digests; missing
Automation content does not fall back to current DEV source. Live Open Preview
through the Builder button now matches the Result label, aggregate/primary
scenario and retained task in both 1440px and 390px layouts: six browser checks
pass in `workbench-refinement-20260915/process-preview-03`. Room recovery also
retains the selected Prototype or Automation identity, with focused unit tests.

`ApplicationConfigurationStore` provides local typed values and credential-ref
overlays with revision preconditions, compatible Beta-to-Beta retention, explicit
conflict rejection and acceptance adoption. Its twelve tests pass. It is not yet
bound into runtime activation or credential authorization; APP1-15 remains open.
Real Reading List data has not been migrated or replaced. Inspection confirmed
that the current native Trial only admits empty data and the older activation
engine requires reversible migrations: both require the planned exclusive
cutover/snapshot integration, not removal of their safety checks.

### 2026-09-15: Application Channel Execution Prerequisite

RuntimeSelection now persists one local Application channel and atomically
advances existing Webspace projections. Native skill handlers retain a shared
SQLite read lease for their actual execution; a channel writer cannot pass a
still-running handler, including after its waiting caller times out. Concurrent
readers and a separate-process reader are covered. DEV tools are not redirected
to a production Beta. HTTP execution conflicts do not trigger member fallback.

This qualifies selected-Application native tool admission, not full lifecycle
cutover. Background service drain, unplaced legacy installation admission,
stale-UI generation checks, all-room refresh, forward data migration and config
adoption remain open in APP1-13 through APP1-15. The local Reading List remains
on its existing Stable selection; its Trial/Stable business data was not changed.

Qualification: 138 focused core/runtime/API tests pass; local API reading of the
existing Stable succeeds after restart. Browser requalification in
`workbench-refinement-20260915/channel-preview-04` passes all six desktop/mobile
checks. A hot-path review found an unnecessary scan of 429 catalog definitions.
Reading only persisted channel records, without caching admission state, reduces
the local 30-call lease probe median from 175.32ms to 4.84ms. Native HTTP reads
still took 2.6-5.4s in the loaded API, also observed on DEV calls before this
change; total handler/import latency is not declared fixed by this probe.

### 2026-09-15: Published Baseline And Data-Cutover Primitives

At the user's request, Client `6e53b5d` and Core `e6f28983f` were pushed;
the core gitlink and `.sha` pin match. Yjs store tests passed (49), as did
Client PageData tests (123). DEV Builder `0.2.143` was uploaded through native
`adaos dev project push builder --bump none`, retaining the existing immutable
release identity. This is source publication, not a completed migration release.

Reading List Stable contains two user records. A consistent, local-only baseline
was captured under `.adaos/recovery/reading-list-20260915/`; the generic
`e2e/stand/sqlite-data-proof.py` reports counts and unchanged-field invariants,
not record contents. No real data was migrated, reset or supplied to an LLM.

The new Application transition journal and private SQLite staging adapter cover
durable fencing, exact-intent replay, committed WAL data, full migration-history
checksums, SQL transaction/attachment escape rejection, two synthetic forward
cycles with Beta-created records, and interrupted snapshot finalization. Their
local tests are mechanism evidence only. The live Reading List lifecycle, scoped
runtime settings/secrets, clarification and About/image-output integration are
still unqualified; APP1-13 through APP1-15 and AP4-20/AP4-21 remain open.

The follow-on typed configuration facade binds the current production skill to
its selected release's `composition_lock`, checks manifest/profile capabilities,
retains opaque credential refs without returning them, and enforces revision and
schema preconditions under the native execution lease. Six runtime configuration
tests cover Stable defaults/persistence, Beta overrides/adoption, DEV/shared-owner
denial, profile denial and cutover fencing. This does not qualify live credential
resolution, DEV synthetic settings or Builder prepare/adopt integration. Its SDK
descriptor explicitly states these boundaries to avoid misleading Automation.

The initially pushed `e6f28983f` passed both AdaOS CI and docs on GitHub. A second
local Reading List baseline verification still reports two records and zero
missing/changed records. No new model-driven Reading List cycle has been started.
