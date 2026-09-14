# Proposal: live Builder workbench from accepted design 071

The user accepted DEV Builder design 071 and explicitly authorized Automation.
The previous Change request's instruction not to implement Automation described
the finished design-only stage. It is historical context, not an instruction to
leave this implementation simulated. The user has NOT authorized replacing the
operational Workspace Builder before independent parity review.

## Scope and source of truth

Implement the Builder application's own scenario and its owned skills. Preserve
the accepted design's layout, navigation, Basic/Detailed views, responsive
behavior, full application title, revision context, and EN/RU localization.
Use the current scenario webui and its archived revision 071 as visual input.
Do not regenerate that accepted archive or hard-code its demonstration state.
All process status, messages, selections, results and counts must reflect real
project-scoped state. An empty project must show empty data, never the sample
Equipment inspections project, mock changes, fabricated usage or fake decisions.

The existing builder_sdk_control_skill and builder_skill contain the operational
commands. Inspect their actual signatures and manifests first. Use the AdaOS SDK
for persistence, workflow, conversation, preview and lifecycle. Keep presentation
and bounded projections in the skills; do not create a second workflow database.
Do not import Core service internals into skill handlers. If a required public SDK
contract is missing, report the exact gap rather than inventing a hidden API.
The shared voice_chat_skill dependency is read-only.

## Specification delta

1. Select an existing project, or create one from a generic template. Selection is
   project-scoped and reloads real metadata. Preserve search, updated/name sorting,
   test markers, archive/restore and guarded delete. The complete title and actual
   revision remain readable in the main header.
2. Conversation is durable: informal discussion and formal change/task messages
   are distinguishable, with source message references. Show the actual request,
   addenda, multiple Issues and their outcomes. Submitting one message must not
   duplicate it or inherit another project's initiator/session.
3. Current work and process history read the canonical workflow and Automation
   projection. Show pending human decisions, blocked/unavailable states and actual
   evidence. Decisions target exact revisions/generations, not the latest revision
   implicitly. Clarifications may contain several questions. Never make approval
   equivalent to a local button color or a fabricated successful status.
4. Prototype generation, revision selection, preview, acceptance, Automation
   start/follow-up/retry and return-to-prototype invoke existing governed commands.
   Preview uses the source Builder host's one paired development webspace; no
   per-project webspaces. Keep open-in-new-window and QR access.
5. Files first select an owned component/dependency, then display a tree and
   selected content. Honor ownership/read-only boundaries. Distinguish user inputs
   for the LLM from generated application content. Provide safe README.md read/edit
   through the existing project file SDK, scoped to the selected component.
6. Settings retain development preferences and separately expose Prototype model
   and Automation/Codex model plus reasoning effort. The Core public SDK now has
   adaos.sdk.builder.model_settings.codex_options(object_type, object_id) and
   set_codex_profile(object_type, object_id, model=..., reasoning_effort=...).
   Catalog entries come from Root; unavailable catalog must be visibly unavailable,
   never substituted with fabricated prices or choices. Store Codex settings on
   the same execution target used by automation.start, not on an unrelated project.
   Changes apply to the next Run, never rewrite a running Run's captured profile.
7. Preserve Dev Tickets and development-feedback consent. Platform/client feature
   requests need explicit user consent before sending and show a separate linked
   development cycle rather than silently marking local implementation successful.
8. Retain lifecycle controls for local source checkpoint, Trial preparation/review,
   release and publication, with real prerequisites and explicit confirmations.
   Merely opening a panel must not publish, accept, install, delete or create an app.

## OpenSpec alignment

Distinguish the user's proposal (why/scope), the application specification (what
behavior is accepted), the current Change's requirement delta, optional technical
design decisions, and executable Issues/tasks. Present existing canonical data;
do not flatten all of these into one prompt or maintain separate editable truth in
UI fixtures. Trace message -> Issue -> Run -> evidence/decision. A Prototype
acceptance is a UI agreement, not evidence that Automation behavior is implemented.
If the accumulated specification SDK is not yet available, show the real current
Change specification and report that specific integration gap without faking an
accumulated application specification. Core specification work is being handled
outside this task.

## Implementation and verification tasks

- Inspect existing SDK bindings and preserve their behavior under the new layout.
- Replace fixture-backed widgets and local simulated transitions with real scoped
  data bindings and commands. Add small skill projections where they remove UI
  complexity; keep model, workflow and file ownership checks in public SDK paths.
- Update skill manifests for all added tool signatures. Keep existing bindings
  compatible where other callers use them. Do not solve parity by reinstating the
  old UI layout or requiring legacy widget IDs on the new accepted layout.
- Add focused executable tests for project isolation, empty/error state, separate
  model settings and at least the new state-changing handler dispatches. No test
  may approve a real user's prototype or publish a real application as a side effect.
- Validate the scenario against the existing declarative UI ABI and compile Python.
  Preserve accepted layout/IDs wherever feasible; only binding-related edits are
  expected. Report any necessary visual changes explicitly for review.
- Report changed files, tested paths, unresolved SDK gaps and unsupported controls
  honestly. Completion requires usable bindings, not just valid JSON. Independent
  browser review and a separate TEST application's full lifecycle follow this task;
  do not claim those external checks have already passed.

## Boundaries

No editing Workspace sources/runtime, global templates, shared skills or Client
domain code. No production publication or application installation from this task.
No fake success fallbacks, static replacement project lists or sample fixture state
on live routes. No secrets in UI, model prompts, logs or test artifacts.
