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
