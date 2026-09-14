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
