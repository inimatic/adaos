# Development Signals Roadmap

Status: proposed cross-domain roadmap.

Last reviewed: 2026-08-18.

This roadmap sequences the work needed to make evolution feedback a governed,
natural AdaOS interface. It is subordinate to
[Development Signals And Evolution Feedback](development-signals.md) for
architecture and to the domain roadmaps for implementation details.

## Priority Model

- `[must]`: required for the first coherent Development Signal vertical slice.
- `[should]`: required before broad repeated use or unattended repair.
- `[could]`: useful ergonomic or analytics improvement.
- `[deferred]`: intentionally waits for AdaOS Issue-first support, trusted
  collaboration, or public reuse.

Maturity follows the Governed Evolution Roadmap vocabulary:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

## DS0. Contract And Storage Spine

Goal: observations become immutable, scoped records before they become
Builder tasks, NLU corrections, or Issues.

Exit proof: a user feedback note, a runtime compatibility finding, and an NLU
miss are all captured as Development Signals with scope, artifact identity,
evidence refs, dedup metadata, and terminal or deferred state.

- [ ] `[must]` `DS0-01` Specify `adaos.development_signal.v1` with `owner_scope`,
  `origin_scope`, `target_scope`, `artifact_refs`, conversation refs, Teacher
  refs, Builder refs, Issue refs, lifecycle state, severity, blocking,
  confidence, policy, and provenance.
- [ ] `[must]` `DS0-02` Implement a workspace evolution inbox that can store
  signals for installed, catalog, remote, read-only, and not-yet-materialized
  artifacts.
- [ ] `[must]` `DS0-03` Add artifact-local signal projection for skills,
  scenarios, WebUI surfaces, and components when writable source exists,
  without making the projection the source of truth.
- [ ] `[must]` `DS0-04` Add dedup and relationship keys for repeated signals,
  supersession, duplicates, stale-after-version-change, and linked repairs.
- [ ] `[must]` `DS0-05` Store screenshots, logs, transcripts, DOM/context
  snapshots, and test output as artifact refs with digest, media type,
  sensitivity, redaction, origin, and retention metadata.
- [ ] `[should]` `DS0-06` Add SDK helpers for `capture_signal`,
  `classify_signal`, `link_signal`, `defer_signal`, and `resolve_signal` so
  skills and scenarios do not write private shapes.
- [ ] `[should]` `DS0-07` Add a compact global projection for active signals by
  workspace, artifact, severity, blocker status, and pending decision.
- [ ] `[could]` `DS0-08` Provide import/migration from current
  `builder.development_feedback.v1`, Builder repair tasks, review anchors, and
  NLU Teacher promotion candidates.

## DS1. Feedback Skill Intake

Goal: a person can record a scoped remark or improvement request from UI,
chat, or voice without entering Builder.

Exit proof: a feedback action opened from a scenario header, skill panel, and
modal creates correctly scoped signals; screenshot capture hides and restores
the modal; voice input stores transcript and not raw audio by default.

- [ ] `[must]` `DS1-01` Implement the Feedback Skill modal with summary,
  category, scope display, scope correction, severity, and record/postpone/
  Builder actions.
- [ ] `[must]` `DS1-02` Wire invocation-site scope: scenario header, skill
  panel, modal, component affordance, runtime diagnostic, chat turn, and voice
  turn.
- [ ] `[must]` `DS1-03` Implement screenshot capture as an artifact flow:
  hide feedback modal, capture current surface, store artifact ref, restore
  modal, and show attachment status.
- [ ] `[must]` `DS1-04` Capture voice feedback through transcript and
  conversation refs; raw audio retention requires explicit policy or consent.
- [ ] `[must]` `DS1-05` Keep Feedback Skill conversations short and bounded to
  intake clarification. Builder planning and NLU correction stay outside this
  state machine.
- [ ] `[should]` `DS1-06` Add one-tap user choices for "record only",
  "postpone", "ask Builder to repair autonomously", and "open Builder".
- [ ] `[should]` `DS1-07` Add contextual microcopy after ambiguity, such as
  "Say 'Ada, record an improvement' for future changes" and "Say 'No, I meant
  ...' when command understanding was wrong."
- [ ] `[could]` `DS1-08` Add component-level attachment helpers for UI controls
  that can pass a stable semantic component id.

## DS2. Conversational Failure Triage

Goal: AdaOS does not surprise the user by choosing between action dispatch,
NLU correction, feedback, adaptation, and development on low confidence.

Exit proof: ambiguous voice/chat utterances produce a visible clarification
before durable training, feedback, or Builder work is created.

- [ ] `[must]` `DS2-01` Define the conversational failure taxonomy:
  `asr_misrecognition`, `nlu_no_match`, `wrong_intent`, `wrong_target`,
  `wrong_action`, `missing_parameter`, `action_unavailable`,
  `development_request`, `feedback_note`, `user_adaptation`,
  `support_question`, and `runtime_failure`.
- [ ] `[must]` `DS2-02` Add a lightweight fallback classifier after deterministic
  NLU/Rasa failure and before NLU Teacher or Feedback/Builder handoff.
- [ ] `[must]` `DS2-03` Add bounded disambiguation prompts for low-confidence
  cases: perform another action, correct command understanding, record
  feedback, save a personal adaptation, or plan development.
- [ ] `[must]` `DS2-04` Route `correct_understanding` and NLU examples to NLU
  Teacher; route `development_request` and `feedback_note` to Development
  Signals; route `do_now` back to action preview/dispatch.
- [ ] `[must]` `DS2-05` Prevent feedback loops: failure to understand "record
  feedback" is an NLU/Teacher issue, not a product feedback signal by itself.
- [ ] `[should]` `DS2-06` Persist classifier result, confidence, rejected
  alternatives, user clarification, and final owner as conversation evidence.
- [ ] `[should]` `DS2-07` Add conversation-story tests for each taxonomy class,
  including RU/EN/STT-noisy variants.
- [ ] `[could]` `DS2-08` Add analytics for clarification rate, wrong-route rate,
  and user correction outcomes.

## DS3. NLU Teacher Bridge

Goal: NLU Teacher and Development Signals share evidence without sharing
state machines.

Exit proof: an NLU miss can link to a Development Signal or Builder task when
the problem is a descriptor gap or missing capability, while ordinary
understanding correction remains inside NLU Teacher.

- [ ] `[must]` `DS3-01` Add `nlu_teacher_ref` to Development Signal records:
  request id, candidate id, promotion candidate id, phrase, intent/action
  candidate, confidence, and trace ref.
- [ ] `[must]` `DS3-02` Allow NLU Teacher to create or link a Development Signal
  when a repeated miss indicates descriptor gap, missing capability, wrong
  action routing, or runtime failure.
- [ ] `[must]` `DS3-03` Allow Feedback Skill to link a signal to the active NLU
  trace when the user reports "you misunderstood" or "the wrong thing ran".
- [ ] `[must]` `DS3-04` Keep accepted Teacher corrections in scoped runtime
  overlays first; source promotion remains a Builder patch.
- [ ] `[should]` `DS3-05` Add user-visible terminal wording that distinguishes
  "correction planned", "development planned", "feedback recorded", and
  "action executed".
- [ ] `[should]` `DS3-06` Add replay evidence from originating Teacher request
  after a Builder descriptor or capability fix.
- [ ] `[could]` `DS3-07` Add a Teacher/Feedback combined history view that
  groups refs without merging records.

## DS4. Runtime Compatibility And Legacy Receiver Findings

Goal: deterministic runtime contract problems become repairable signals
instead of compatibility fallbacks or raw logs.

Exit proof: a legacy skill that handles stream/Yjs events without declared
receiver/data-route contracts produces one deduplicated compatibility signal,
one repair context, and one Pending Action with autonomous and interactive
Builder options.

- [ ] `[must]` `DS4-01` Add compatibility finding codes for missing or invalid
  `data_routes`, `webio.receivers`, projection slots, receiver ownership, and
  route/projection mismatches.
- [ ] `[must]` `DS4-02` Convert activation, validation, stream-admission, route
  pressure, and projection-rule-miss evidence into Development Signals and
  Builder repair tasks when design-time fixable.
- [ ] `[must]` `DS4-03` Add computed blocker fields:
  `blocking`, `run_policy`, `design_time_fixable`, and
  `autonomous_repair_eligible`.
- [ ] `[must]` `DS4-04` Publish a Pending Action for user-visible or blocking
  compatibility findings with actions `preview_evidence`,
  `start_autonomous_repair`, `open_builder`, `postpone`, and where policy
  permits `run_once_with_compatibility` or `disable_until_fixed`.
- [ ] `[must]` `DS4-05` Make descriptor-only receiver fixes eligible for bounded
  autonomous repair only when they do not broaden permissions, receivers,
  external I/O, or data access beyond the observed contract.
- [ ] `[must]` `DS4-06` Require acceptance evidence: strict validation,
  activation/smoke import, expected receiver admission, negative admission for
  unrelated receivers, and no unexpected projection/route diagnostics.
- [ ] `[should]` `DS4-07` Add a migration scan that creates deferred
  compatibility signals after core ABI changes, without activating every
  affected skill.
- [ ] `[could]` `DS4-08` Add batch triage for low-risk descriptor debt.

## DS5. Builder Handoff And Closure

Goal: a signal can become autonomous or interactive Builder work without
losing scope, evidence, or version lineage.

Exit proof: both "repair autonomously" and "open Builder" create the same
typed Builder context; completion links the result back to the originating
signal and closes only with evidence.

- [ ] `[must]` `DS5-01` Define the handoff packet from Development Signal to
  `builder.task.v1`, `builder.repair_task.v1`, or `builder.realize_request.v1`.
- [ ] `[must]` `DS5-02` Add Pending Action response handlers for
  `start_autonomous_repair` and `open_builder`.
- [ ] `[must]` `DS5-03` Materialize a development context for installed,
  catalog, remote, or read-only artifacts through existing DEV source,
  local fork/overlay, upstream proposal, deferred state, or
  `not_design_time_fixable`.
- [ ] `[must]` `DS5-04` Include Development Signal context in Builder packets:
  original words, scopes, versions, digests, evidence refs, NLU refs, privacy
  constraints, and acceptance expectations.
- [ ] `[must]` `DS5-05` Close signals only through resolved version, resolved
  overlay, acceptance evidence, explicit rejection, supersession, stale
  revalidation, or not-design-time-fixable state.
- [ ] `[should]` `DS5-06` Add delayed completion notifications that deep-link to
  signal, repair, Builder run, release, and verification evidence without
  making notifications authoritative.
- [ ] `[should]` `DS5-07` Support user-visible status labels: recorded,
  postponed, waiting for approval, in autonomous repair, in Builder, fixed in
  version, still blocked, or cannot be fixed locally.
- [ ] `[could]` `DS5-08` Add comparison/evaluation hooks for multiple repair
  strategies when a signal is eligible for more than one adaptation method.

## DS6. Analytics, Campaigns, And Policy Hardening

Goal: feedback improves the platform without turning private observations into
uncontrolled telemetry.

Exit proof: a core ABI migration can generate scoped compatibility signals,
dedupe them, ask for bounded repair where eligible, and report aggregate
outcomes without leaking private evidence.

- [ ] `[must]` `DS6-01` Define minimum metrics for signal source, target,
  severity, duplicate rate, classification confidence, clarification rate,
  repair eligibility, time to closure, stale rate, and false-positive rate.
- [ ] `[must]` `DS6-02` Add privacy and retention policy checks for screenshot,
  voice transcript, raw audio, logs, DOM snapshots, and remote support export.
- [ ] `[must]` `DS6-03` Add campaign mode for core/runtime/schema migrations:
  scan, create signals, dedupe, batch triage, repair eligible items, and
  record unresolved debt.
- [ ] `[should]` `DS6-04` Promote recurring model-discovered symptoms into
  deterministic compatibility checkers or validation rules.
- [ ] `[should]` `DS6-05` Add dashboards only after event fields answer
  operational questions reliably.
- [ ] `[could]` `DS6-06` Add user-level preference reports that separate
  personal adaptations from shared artifact evolution.
- [ ] `[deferred]` `DS6-07` Share signals across trusted development groups only
  after GE4 defines proposal visibility, consent, and revocation.
- [ ] `[deferred]` `DS6-08` Use aggregated signal evidence for public capability
  ranking only after GE5 defines verified reuse, privacy, and comparability.

## Recommended First Slice

The first implementation should be narrow:

```text
Feedback Skill modal + workspace inbox
  -> screenshot artifact ref
  -> Development Signal schema
  -> runtime receiver compatibility finding
  -> BuilderRepairService report
  -> Pending Action: autonomous repair or open Builder
  -> descriptor fix
  -> strict receiver validation evidence
```

This proves the full lifecycle while avoiding the harder unresolved pieces of
global Issues, public collaboration, and broad autonomous development.

## Documentation Owners

- [Development Signals And Evolution Feedback](development-signals.md) owns the
  architecture.
- This roadmap owns the Development Signal sequencing.
- [Governed Evolution Roadmap](governed-evolution-roadmap.md) owns the
  cross-domain milestone gates.
- [NLU Teacher Evolution Roadmap](nlu-evolution-roadmap.md) owns NLU and
  conversational repair gates.
- [Builder Roadmap](builder-roadmap.md) owns Builder readiness, repair context,
  and release evidence.
- [Pending Actions](pending-actions.md) owns durable user decisions.
- Future AdaOS Issue architecture owns accepted support/development work after
  Issue-first repair is admitted.
