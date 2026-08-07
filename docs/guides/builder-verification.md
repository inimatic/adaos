# Builder Verification Guide

Status: operational verification guide for the current governed Builder
workflow. Architecture and roadmap pages remain authoritative for contracts and
completion status.

Last reviewed: 2026-08-07.

## Purpose

This guide defines a repeatable Web and Telegram verification of Builder. It
does not replace automated tests. Expected states, links, controls, and outcomes
must be checked against the canonical workflow rather than accepted from reply
text alone.

The guide was promoted from a useful Russian-only verification document when
English became the documentation authority. Dated local results are not copied
here; they remain in the English evidence records linked at the end.

## Deterministic Builder Commands

Read and navigation commands must not start an LLM, Codex task, or Automation
Run.

| Command or action | Expected result |
| --- | --- |
| `Builder, what is selected?` | Current Project identity and context-valid actions. |
| `Builder, show the current project` | The same Project state without a workflow transition. |
| `Builder, show projects` | Builder and Preview context, one working Project, current Preview target, and selectable DEV projects. |
| `Builder, select <id>` | Change the working Builder Project without a business transition or implicit Preview switch. |
| `Builder, help` | Contextual help and the nearest safe actions without LLM or Codex execution. |
| `Builder, Preview link` | Exact `proto:`, `active:`, or `public:` target and a `webspace.open` URL with zone, subnet, Webspace, source boundary, and expected scenario. |
| `Show process` | Localized process projection from Change through Prototype, Automation, Verification, Trial, Publication, installation, and placement. |
| `Show prototype` | Select the accepted or current `proto:` revision as Preview target. |
| `Show implementation` | Select the current `active:` Automation result when one exists. |
| `Show publication` | Select the installed `public:` release when one exists. |
| `Open Trial` | Open the exact runtime-only Trial placement from an immutable Candidate PackageRef. |
| `Place in Webspace` | Request a target Workspace Webspace as durable `input_required`; the control itself does not invent a target. |
| `Open published project` | Open an existing stable placement through Navigation SDK; available only after placement. |
| `Continue project development` | Start a new Change over the published version. |
| `Add requirement` | Extend the current Change and include the requirement in the next Prototype revision. |
| `Fix prototype` | Revise the Prototype inside the already agreed Change scope. |
| `Refine implementation` | Request Automation instructions inside the current Change. |

Control labels may be localized. An exact label from the latest unfinished
Interaction is an allowed textual fallback. Free fuzzy matching does not grant
authority to a state-changing action.

## Context and Preview Boundaries

Project selection and Preview selection are independent:

- `show projects` renders a **Context** block and a **Projects** block;
- exactly one Project is the working Builder Project;
- selecting a Project does not switch the open Preview;
- `Show prototype`, `Show implementation`, and `Show publication` select the
  Preview target;
- `Preview link` opens the already selected target and does not change Project
  or workflow state.

A Preview link is a destination, not authority. Resolution proceeds through
zone, authentication, subnet, Webspace, source boundary, complete
materialization, and scenario/revision identity. A zone or subnet mismatch
requires an explicit transition or cancellation. The URL uses
`intent=webspace.open`; legacy `mode` is not part of the contract. Completed
authorization codes must not remain in a canonical copyable URL.

## Interaction Consumption and Idempotency

After a semantic control is used, the originating assistant message becomes
`consumed` in Web, Voice, and Telegram projections. Controls disappear, the
prompt remains, and the selected action is recorded as an annotation. Telegram
also answers the callback query.

The command result is a separate fact. Presentation may acknowledge selection,
but it must not replace the operation outcome. Replaying the same signed action
token returns the stored result and must not repeat an LLM call, tool, Codex
task, or workflow transition. A repeated transport update may repeat delivery,
not execution.

## Preconditions

1. `/health/ready` reports the commit and version of the runtime under test.
2. After a DEV skill push, explicitly activate the new DEV revision. Changed
   files on disk do not update an already loaded skill process.
3. After DEV-to-Workspace publication, `handlers/main.py`, `workflow.json`, and
   tests match even when DEV and Workspace version sequences differ.
4. Send Cyrillic HTTP fixtures as UTF-8 or ASCII JSON with Unicode escapes.
   Terminal mojibake alone is not evidence of corrupted stored data.
5. Use a disposable test scenario. Do not use publication of an active working
   scenario as a probe.
6. Send the next mutating request only after a terminal result or explicit
   `input_required`. After a timeout, inspect Run state before considering any
   recovery action.

## Verification Series

### 1. Context, channels, and read-only behavior

Run the project list, selection, current-project, help, Preview-link, and
process commands in Web and Telegram.

Verify:

- the same Project identity and state-valid actions appear in both channels;
- one working Project is distinguished from the Preview target;
- selecting a Project leaves Preview unchanged;
- a Preview link contains exact zone, subnet, Webspace, source kind, scenario,
  and revision/stage when available;
- a mismatch requires explicit confirmation before any Webspace or scenario
  switch;
- reopening an already current target is idempotent;
- authentication resumes the original `webspace.open` intent;
- none of these steps creates an LLM, Codex, or Automation Run.

For multi-tab verification, each live page has its own browser session and
peer identity. Closing pages must remove their Yjs peers. One state update must
not fan out to dead page sessions or trigger an unbounded replay loop.

### 2. Read current state without mutation

Ask Builder to show the current Project, active Change, selected process node,
and exact Preview target without changing anything.

Expect exact identities, no new Run, and an explicit separation between the
Builder Webspace and paired Preview Webspace.

### 3. Create Change and Prototype

Request a small application with explicit screen requirements and mock data.
Require Prototype-only work until UI approval.

Expect one Change, related Issues, `prototype_first` routing, fixture or mock
data, and a new immutable Prototype revision. Automation must not start.

### 4. Iterate the same Change

Add layout, content, or empty-state requirements and explicitly keep the same
Change.

Expect a new iteration Run and Prototype revision inside the existing Change,
with prior revisions still addressable.

### 5. Review and correction

Submit review feedback against the current Prototype and apply it to existing
Issues. Also create and withdraw one incorrect review item before application.

Expect the withdrawn item to disappear from active model context while a
minimal audit tombstone remains.

### 6. Handoff to Automation

Accept an exact Prototype revision, request the implementation brief and
acceptance criteria, then explicitly approve Automation or Codex execution.

Expect Automation to reference the immutable accepted Prototype revision.
Approval must not rewrite it.

### 7. Direct functional Change

Request a backend or persistence change while holding UI fixed. Ask Builder to
prepare Automation but wait for execution approval.

Expect `automation_direct` routing and no Prototype LLM run.

### 8. Declarative workflow correction

Request a precise `workflow.json` rule change while explicitly excluding UI
changes.

Expect the Automation/Codex lane, structural validation of complete transition
descriptors, a graph-level diff, and no UI revision. Definition and code/package
activation remain atomic.

### 9. Asynchronous Automation Run

Approve an implementation with tests but without automatic Trial creation.

Expect the sequence `accepted -> started -> progress -> input_required/resumed`
when needed, then exactly one terminal outcome. Progress is not workflow state,
and retrying result delivery must not rerun the tool or Codex task.

### 10. Inspection and recovery

Ask for Run state, commands, changed files, tests, and remaining acceptance
criteria without starting anything.

After a runtime restart, the Run result and ReplyRoute remain queryable. A
redelivery creates a DeliveryAttempt, not a new Run.

### 11. Trial

When Automation and acceptance criteria are complete, request an immutable
Trial without publication.

Verify exact Prototype and Automation identities, dependencies, validation
results, Candidate PackageRef, runtime-only placement, data mode, health, and
durable TrialActivation. Trial must not mutate the stable WorkspaceLock.
Conflicting shared-skill versions are rejected before activation unless the
runtime explicitly supports the combination.

### 12. Trial feedback and repair

Exercise representative behavior in Trial. When a defect appears, create an
Issue in the same Change and a separate repair Run. Previously completed
commands must not be repeated silently.

### 13. Publication and placement

Prepare a publication candidate from an accepted Trial, show immutable lineage,
evidence, dependencies, and expected Workspace changes, and wait for separate
publication approval.

After approval, verify immutable source/package/candidate/release lineage and an
atomic WorkspaceLock transition. Publication and placement remain distinct:
before placement Builder offers `Place in Webspace`; after durable target input
it offers `Open published project` through Navigation SDK. Continuing
development creates a new Change over the stable version.

## Negative Checks

- A stale control returns a fresh frame and does not execute.
- A repeated callback or transport update produces at most one action dispatch
  and one operation result.
- A consumed message has no live controls after reload in any owned projection.
- An unknown or malformed signed-action token is rejected and never passed to
  Builder, Automation, or an LLM as user text.
- A missing required capability produces an explicit fallback, deep link, or
  unsupported result; it does not silently expose a less safe action set.
- `input_required`, failure, and expiry create visible escalation; frequent
  progress is coalesced.
- Quiet hours do not hide a mandatory approval.
- Archived and active Projects expose Restore and Archive respectively.
- An unavailable `active:` or `public:` source is explained and is not replaced
  by an unrelated Desktop or scenario.
- Invalid UTF-8 is rejected before LLM or Codex processing.
- A timeout or unknown outcome never authorizes blind repetition of a mutating
  command.

## Evidence and Authority

Use these records for dated verification results and open gates:

- [Builder Governed Workflow Verification — 2026-08-01](../architecture/builder-workflow-verification-2026-08-01.md)
- [Builder Context Routing Evidence — 2026-08-03](../architecture/builder-context-routing-evidence-2026-08-03.md)
- [Builder Phase 11 Verification — 2026-08-05](../architecture/builder-phase-11-verification-2026-08-05.md)
- [Builder Roadmap](../architecture/builder-roadmap.md)
- [Governed Workflow Runtime Roadmap](../architecture/governed-workflow-runtime-roadmap.md)

The evidence records establish what was observed on a dated environment. The
roadmaps own remaining acceptance gates. This guide owns only the reusable
manual verification sequence.
