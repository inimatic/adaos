# Builder Phase 11 Verification — 2026-08-05

Status: non-deferred engineering slice implemented and locally verified;
explicit human production gates remain visible.

## Scope and result

Phase 11 now has one data-defined control path from conversational proposal to
governed workflow command, registered activity, asynchronous response and
transport receipt. The Builder Workbench derives its continuation actions,
Process lineage, desired/observed Preview state and drift from the same
governed snapshot.

The implementation includes:

- package-bound EN/RU conversational compilation, ingress, Teacher promotion
  through Changes, learning-story export and comparison/evaluator evidence;
- canonical shared Interaction/Response handling, rich-view negotiation,
  executor readiness and transport recovery inspection;
- dependent Prototype -> Automation -> verification -> Trial -> Publication
  activities with durable accepted/progress/input-required/terminal envelopes;
- Issue split/merge, semantic add/remove and data-binding changes;
- latest-generation Preview supersession, transition events, reconnect drift
  reconciliation and compact Builder-owned projections;
- canonical browser `NavigationLocation`, scoped Yjs delta writes, safe
  offline structural compaction and a documented SharedWorker decision;
- Telegram inline callback consumption plus a durable outbound attempt and
  final hub-scoped delivery receipt. Root relay acceptance is not mistaken for
  final Telegram delivery.

## Reference skill

`conversational_workflow_lab_skill@0.1.2` is the bounded reference artifact in
`.adaos/workspace/skills/conversational_workflow_lab_skill` (Forge/workspace
commit `eaa381015b55e0ac459c4f809417dcb8584dc6fa`). It contains one manifest-bound
`workflow.json`, one conversational package, EN/RU output and context-dependent
actions for `collecting`, `review`, `completed` and `cancelled`.

A live call through the restarted local API and the active slot B runtime used
`phase11-live-20260805` and proved:

```text
collecting generation 0
  -> submit
review generation 1
  -> approve (confirmed)
completed generation 2
  -> duplicate approve = already_applied, generation remains 2
```

The Russian terminal message decoded to the exact Unicode code points for
`Запрос одобрен.`. Windows PowerShell 5 displayed UTF-8 JSON without a charset
parameter as mojibake; Python UTF-8 decoding proved that neither the HTTP bytes
nor persisted workflow/conversation data were corrupted. Non-ASCII automation
continues to use UTF-8 files or Python, never PowerShell argument injection.

## Package correction found by strict admission

Strict DEV validation found a malformed indentation in the Builder
conversational manifest and stale `confirmation: none` story policies. The
package now aligns its four reviewed operations with the normative
`TransitionDescriptor`, models Trial admission as `trial_waiting`, and is
pushed and activated as `builder_skill@0.3.46` at Forge commit
`133458ee7f27d827c8a257aafcbadef07e1f79db`.

## Fresh conversational project pipeline

The non-Builder acceptance project `workflow_lab_dashboard` exercised the
complete product path on this machine rather than reusing historical Builder
evidence:

```text
user request
  -> Change builder_change_1ba6441d with five bounded Issues
  -> built-in LLM Prototype UI 001 (EN/RU, deterministic mock data)
  -> explicit Prototype acceptance
  -> isolated Codex task task.01KZ9S388A01VP2GAR2VAE76TZ
  -> scenario 0.1.2 + workflow_lab_dashboard_skill 0.1.1
  -> paired Forge checkpoint
  -> isolated empty-data Trial
  -> explicit Trial acceptance
  -> stable Publication
  -> WorkspaceLock revision 4
```

The promoted candidate is
`workflow_lab_dashboard-0-1-2-f731ed4209d9`. Its scenario package digest is
`sha256:a51eb4065fb7cd60fccae83d9992495c1414cc3591839a8f0db7f731ed4209d9`,
source revision is `bd51e2197bf85524905b70f686aae08ca6626f30`, and release digest is
`sha256:ed7c186a4e57b65cff12d0ad64f50222585e25e1a954cf5d5eb6c8380e98271d`.
Trial and installed Workspace trees are byte-identical for both components:
scenario tree
`sha256:cf1e8599e7aa7d9919e43c4a9cc42126adc6f2b5d0c5e28e494332bd7c76d688`
and skill tree
`sha256:4e15bd8cc2ef071a6c1777283bc2bc539125fe109e008d20db9236d150e0e6ea`.

Strict recovery testing exposed and corrected four reliability gaps instead of
concealing them with another mutation:

- publication runtime convergence inspected unrelated retained WorkspaceLock
  components; it is now scoped to the candidate dependency closure;
- a rolled-back activation is recovered once with the exact failed operation
  and a new idempotency identity; the stable channel move was recorded once;
- post-reconciliation local Publication attempts are versioned by source
  workflow generation, so an old admitted command cannot shadow a new attempt;
- a completed promotion is replayed only from terminal receipts, and a legacy
  `paused` marker over complete receipts is reconciled without registry,
  channel, or activation replay.

The final workflow is generation 24 with canonical state `published`, and the
promotion contains one `channel_moved` receipt plus activation operation
`1a240344e10b7f1ed8ef47c7ebd123fd`. WorkspaceLock revision 4 pins only the
new scenario and its required skill in this project closure while retaining
the independently managed projects.

## Test evidence

| Surface | Result |
| --- | --- |
| Complete Builder-named Python suite | 230 passed in 275.2 s |
| Workflow/conversation/interaction suite | 255 passed in 131.3 s |
| Telegram receipt, durable outbox, Router and NATS bootstrap | 135 passed in 64.6 s |
| Builder scenario plus reference-skill tests | 15 passed |
| Strict probed `builder_skill` and reference-skill validation | passed |
| `builder_skill` and `builder_sdk_control_skill` runtime self-tests | passed |
| Candidate/publication/root callback regression | 83 passed |
| Builder governed/compatibility workflow regression | 55 passed |
| Current `builder_sdk_control_skill` regression | 50 passed |
| `workflow_lab_dashboard` scenario validation and companion skill tests | passed (3/3 companion tests) |
| AdaOS backend TypeScript build and complete suite | passed, 20/20 |
| Client NavigationLocation/App/Modal/Auth/YDoc focused suite | 295 passed |
| Client production build | passed, version 0.0.275 |

Key commits in this slice are `69d4fb13`, `d0aeb3c9`, `ae6ae315`,
`cd281033`, `dce940a5`, `5a7835ee`, `b4696beb`, `7cae1ce5`, `9f04fca7`,
`340bbeb4`, `59e68448`, `f241762a`, `0a23efa2`, `4c2a1847`, `301e885a`,
`41c96a9b`, `8ea22c0e`, `d425a0fc`, `91a6b6a1`, core receipt commit
`e2d4201e`, backend receipt commit `2726686`, and client commits `2f99450` and
`9b2fccc`. Publication recovery and dependency-boundary corrections are
`91747d45`, `5bab3547`, `75b20c97`, `188acf40`, and `994c3457`; the final
Builder control-skill Forge checkpoint is
`2bfe0957f827e96de30f5db0e9f0b21cacfd2e1e`.

## Explicit acceptance gates

The following are not represented as completed by automated evidence:

- the user-owned final wide/compact visual comparison and consequent deletion
  of `builder_reference_042`;
- one human mutating Telegram callback after the receipt-enabled backend is
  deployed.

These gates do not authorize compatibility shortcuts. Trial and Publication
continue to require their normative waiting/result transitions and explicit
approval.
