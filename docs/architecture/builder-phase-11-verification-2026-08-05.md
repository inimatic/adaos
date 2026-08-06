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

The reference skill and the later acceptance dashboard deliberately have
different responsibilities:

- `conversational_workflow_lab_skill` owns the governed statechart and binds
  `skill.yaml -> workflow.json`;
- `workflow_lab_dashboard` is a declarative UI scenario and binds
  `scenario.yaml -> webui.json` plus the read-only
  `workflow_lab_dashboard_skill` companion;
- the dashboard's procedural `scenario.yaml.steps` are not a second governed
  workflow. A component may own zero or one governed workflow and declares one
  only through `workflow.manifest`.

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

### Post-publication materialization correction — 2026-08-06

The first human launch selected `workflow_lab_dashboard` but retained the
Homepoint page. The published package was complete; the runtime scenario
loader still required the obsolete adjacent `scenario.json` and therefore
ignored the canonical `scenario.yaml -> webui.json` reference used by the new
package.

The loader now resolves UI content from canonical `scenario.yaml` first and
uses `scenario.json` only for legacy packages whose YAML does not declare a UI
descriptor. The source fingerprint includes the referenced `webui.json`, and
materialization cannot report `ready` without
`ui.application.desktop.pageSchema`.

After restarting the local checkout runtime, an exact one-shot reload of the
already published scenario proved:

```text
current_scenario = workflow_lab_dashboard
resolver.source = loader:workspace
resolver.legacy_fallback = false
materialization.readiness_state = ready
page_widget_count = 3
changed_branches = 0 on the confirming reload
workflow_lab_dashboard_skill.get_dashboard = ok, 4 requests
```

The confirming reload changed no Yjs branch, so this correction restores the
declarative surface without introducing a periodic refresh or compatibility
write loop.

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

### Outcome, Trial, and consumed-control increment — 2026-08-06

The follow-up slice separates a stable release from its installation and
runnable placement, and removes Preview language from the terminal published
surface:

- root commit `c736c191` adds the Project placement projection, exact stable
  outcome actions, and restart-safe typed Builder continuations;
- root commit `7ff2a05e` materializes immutable Candidate PackageRefs under
  derived `workspace/.runtime/trials`, persists TrialActivation and data-mode
  evidence, reconstructs missing derived state, and detaches explicitly;
- root commit `46f681f1` plus client commit `466b3c9` retire consumed Web/Voice
  controls durably and optimistically without repeating the underlying command;
- root commit `4b156839` keeps the Trial node accepted after Publication and
  localizes the semantic Process lineage independently of command identity;
- client commit `b0a95e7` accepts exact runtime Trial navigation targets.

The current DEV Builder scenario was checkpointed to Forge as
`4d568b7dfba7e27bcba320183c719b96f38ea0b6` and published as stable scenario
`0.2.56`. The Builder skill was checkpointed as
`3c9d11b77f334f3d7a4c5b49b64d6da18f50dd29` and published as stable skill
`0.3.36`. A direct-runtime restart loaded checkout commit `7ff2a05e`; the final
root lineage correction requires the concluding restart recorded with this
ledger.

Live `builder_skill.chat` evidence for `workflow_lab_dashboard` produced one
`adaos.conversation.interaction_presentation.v1` with these published actions:

```text
Place in Webspace
Show process
Refine project
Change project
Help
```

The project had stable version `0.1.2` installed in Workspace and no stable
Webspace placement, so `Place in Webspace` was correct and no Preview action
was present. The Russian text command `Строитель, покажи процесс` returned the
same semantic lineage:

```text
Изменение -> Прототип -> Автоматизация -> Проверка -> Апробация
-> Стабильная версия -> Установка в Workspace
Дальше: Разместить в Webspace
```

The remaining acceptance gates are intentionally not hidden by these results:
`GWR5-37` still needs a fresh operational empty-project run that uses the new
runtime-only Trial path, and `GWR5-38` still needs a live mutating Telegram
callback in both supported locales.

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
| YAML-only scenario loader/materialization regression | passed (106/106 affected tests); live Workspace resolver and companion tool passed |
| Dependency-aware scenario CLI validation | passed (13/13 focused tests); published dashboard validation has zero issues |
| AdaOS backend TypeScript build and complete suite | passed, 20/20 |
| Client complete deterministic browser suite | 906 passed |
| Client NavigationLocation/App/Modal/Auth/YDoc focused suite | 295 passed |
| Client production build | passed, version 0.0.276 |
| Phase 11 placement/Trial/Builder SDK regression | passed, 100/100 |
| Current DEV `builder_skill` regression after localized Process change | passed, 167/167 |
| Runtime Trial/navigation plus Voice consumed-control client suite | passed, 22/22 |
| Published Trial status and Russian Process projection focus test | passed, 1/1 |

Key commits in this slice are `69d4fb13`, `d0aeb3c9`, `ae6ae315`,
`cd281033`, `85db66bb`, `36f24913`, `3d43e220`, `e6ce0790`, `cd4b2dea`,
`fddccee8`, `b2deacf3`, `e16b7890`, `c610c800`, `ce4197d3`, `a7bf86fd`,
`c08dc9ac`, `0bcdf524`, `2ab76eb9`, `9ee5c334`, core receipt commit
`6186ca4b`, backend receipt commit `2726686`, and client commits `2f99450`,
`9b2fccc`, `ffc09fc`, and `bcb9367`. Publication recovery and
dependency-boundary corrections are
`65c039c2`, `a289b654`, `88e650ba`, `2bad76a1`, and `1e03c76d`; the final
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
