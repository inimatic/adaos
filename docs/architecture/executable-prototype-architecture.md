# Executable Prototype Architecture

Status: target architecture and MVP boundary for Builder prototyping.

This document refines the [Builder Conversational Development Architecture](builder-conversational-development.md),
the [Web UI Architecture](web-ui-architecture.md), and the
[Governed Data-Driven Workflow Model](governed-workflow-runtime.md). Delivery
priority and evidence remain owned by the [Builder Roadmap](builder-roadmap.md)
and the [Governed Workflow Runtime Roadmap](governed-workflow-runtime-roadmap.md).

## Decision Summary

**A Prototype is not a static mockup with a few rows of data.** It is a bounded,
safe, executable requirement model that lets a user inspect and adapt interface
composition, representative data behavior, and, for the MVP, a conversational
process before Automation implements real effects.

The MVP deliberately prefers a small formal vocabulary over broad implicit
behavior:

- `webui.json` remains the declarative UI source;
- stable semantic refs and a bounded composition slice describe what the model
  is changing and where it appears;
- typed Preview binding profiles separate logical data from `mock`, `fixture`,
  `sandbox`, `live_readonly`, and `live` sources;
- local state, local CRUD, provider mocks, and model-generated examples execute
  only through declared prototype activities;
- the only workflow-prototyping profile admitted in the MVP is a constrained
  **conversational workflow slice**;
- Prototype execution emits semantic requirements and a simulation trace, not
  claims that a backend already exists;
- Automation replaces mock activity bindings with implementations while
  preserving accepted UI, data, conversational, and outcome contracts.

Screenshot input, general visual workflow design, full workflow round trips,
and automatic reverse engineering are explicitly deferred. Their contracts are
retained below so the MVP does not close the extension seam.

## Implemented MVP Baseline

As of 2026-08-06, the Must/Should executable-Prototype foundation is
implemented and validated locally:

- `scenario.yaml` and `skill.yaml` may declare one typed `prototype_runtime`
  bundle with `data`, `binding`, `workflow_slice`, and
  `representative_states` JSON refs under the component's `prototype/`
  directory; path traversal and incomplete declarations fail schema or load;
- the shared `adaos.sdk.builder.prototype` facade exposes disposable data
  execution, UI composition slicing and spatial checks, conversational slice
  validation, and Automation handoff construction;
- Builder context packets load only manifest-declared bounded JSON, retain each
  exact ref/digest, validate the data/binding/workflow contracts, include
  target-local composition, and mark Simulation Trace as Prototype evidence,
  never implementation evidence;
- Automation verifies the handoff schema, project identity, evidence digest,
  story results, renderer evidence, representative states, and every required
  mapping before any workflow/session mutation;
- `examples/executable_prototype_lab` is the portable reference project and is
  also installed as a local DEV scenario under subnet `sn_6acf0c01`.

The ordinary `dev scenario push` path admitted version `0.1.1` with exact
workflow, validation, adapter and role-policy locks, then uploaded it in Forge
commit `a6ff3ac226427fb9679557ef1aa32bc28dd33304` (payload SHA-256
`fcbb16fff49e636e6cfd1e3c0641c83059bddefef53524b4910efd04e4384b4d`).
Two rejected preflight attempts are retained as useful negative evidence: an
unregistered domain activity and a widened input/risk contract were both
blocked before upload. The final definition uses the registered isolated
`builder.prototype.derive` execution adapter while keeping domain activities
as separate implementation-neutral handoff requirements.

The reference deliberately checks in one missing data mapping and one missing
workflow activity mapping. Its end-to-end test first proves rejection, then
maps the same immutable evidence in memory and proves admission into the
existing Automation rail. This keeps a passing demo from concealing the most
important safety property.

## Product Objective

The Prototype stage should answer four questions before general programming:

1. What does the user see and how is it composed?
2. What representative data and interaction behavior can be exercised safely?
3. What conversational steps, questions, choices, and outcomes are required?
4. Which semantic activities must Automation implement?

The result is an executable specification, not an implementation substitute.
A prototype may demonstrate saving, searching, generating, or publishing, but
it must identify those operations as mock requirements until a governed
implementation binding and evidence exist.

## Prototype Planes

The Prototype is a coordinated projection over four distinct planes:

```text
UI Composition Slice
    elements, hierarchy, order, layout, visibility, and stable refs

Data Binding Profile
    logical schema plus mock/fixture/sandbox/live binding

Conversational Workflow Slice
    states, commands, typed input, mock activities, and outcomes

Activity Requirements
    implementation-neutral contracts handed to Automation
```

These planes may reference one another, but none may silently become authority
for another plane. Moving a control does not change its command. Switching a
compatible fixture does not rewrite the UI revision. A mock success does not
prove an Automation activity exists.

## Prototype Data Runtime

### Data mode and behavior are different axes

A binding mode identifies authority and isolation:

| Mode | Meaning | Prototype policy |
| --- | --- | --- |
| `mock` | Generated or in-memory examples with no external authority | default |
| `fixture` | Versioned, sanitized, deterministic data | default |
| `sandbox` | Isolated connector, test tenant, or provider | explicit scoped confirmation |
| `live_readonly` | Real data without modifying effects | explicit policy and visible warning |
| `live` | Real data with modifying effects | forbidden in Prototype |

Behavior identifies how much of the experience is executable within a mode:

| Behavior | Requirement |
| --- | --- |
| static examples | representative typed records and domain-correct copy |
| local state | selection, filtering, visibility, validation, and navigation |
| local CRUD | create/read/update/delete against a disposable bounded store |
| provider mock | typed recorded or generated provider outcomes |
| model generator | typed synthetic output through a declared model activity |

`mock` is therefore not synonymous with static rows. A mock profile may provide
a realistic local CRUD session, validation, error outcomes, and synthetic
generation while retaining no external authority.

### Logical data contract

Every prototype datasource declares:

- stable logical schema and entity refs;
- supported queries and semantic commands;
- validation and cardinality constraints;
- data mode, sensitivity, owner, expiry, and redaction;
- whether state is reset on reload, session end, or explicit command;
- representative fixtures and their deterministic identity;
- activity requirements for behavior that Automation must implement;
- implementation mappings with `mapped`, `fixture_only`, or `missing` status.

Switching a compatible binding profile does not rewrite the accepted UI or
conversational revision. Changing the logical schema, commands, or observable
outcomes creates a new Prototype revision.

### Local CRUD

Local CRUD is a first-class prototype behavior, not a hidden fake backend. Each
operation uses a semantic command such as `recipe.create`, not a solution name
such as `save_to_database`. The prototype runtime owns a disposable store and
must expose its current mode visibly.

```text
UI/Conversation command: recipe.create
  -> prototype activity: prototype.recipe.create
  -> disposable mock store
  -> typed Recipe or declared error outcome
```

The same activity requirement handed to Automation remains implementation
neutral:

```text
activity: recipe.create
effect: durable_write
input: RecipeDraft
output: Recipe
outcomes: success | validation_error | unavailable
```

Automation may select a database, service, or connector. It may not silently
remove an accepted outcome or present a fixture-only behavior as implemented.

### Provider and LLM-backed mock behavior

`webui.json` must not call an external provider or model directly. Runtime
generation follows one declared boundary:

```text
UI/Conversation command
  -> prototype capability
  -> registered mock/provider activity
  -> typed result
  -> prototype datasource
```

The default execution is a recorded fixture or bounded deterministic synthetic
generator. A real model/provider call requires an explicit sandbox or
integration-trial profile with quota, cost, timeout, privacy, redaction, and
failure policy. Generated output records provider/profile identity, input
schema, prompt/profile version, seed when available, provenance, validation,
and a replayable sanitized fixture. A model assertion that data is safe or
representative is not evidence.

### Representative states

A useful prototype must be able to exercise more than a happy-path record set.
The minimum scenario catalogue is:

- empty;
- normal;
- validation failure;
- provider/activity unavailable;
- delayed or input-required;
- access denied when the operation is role-sensitive;
- long labels/data and both supported locales;
- declared compact and wide composition.

Offline, concurrent-conflict, rate-limit, and large-volume profiles were
classified as `should` extensions after the deterministic MVP cases. The
reference scenario now includes typed fixtures for all four in addition to the
minimum catalogue; they remain bounded evidence profiles, not claims of live
provider fault injection.

## Semantic UI Composition For LLM Work

### Required representations

Builder supplies a bounded structural context rather than relying on raw JSON
or a screenshot alone:

1. **Source structure:** the exact referenced `webui.json` fragments and ABI.
2. **Semantic composition:** stable ref, role, parent, siblings, order,
   grouping, layout model, visibility, actions, bindings, and constraints.
3. **Structured rendered composition:** compact viewport/breakpoint evidence
   needed to verify a spatial requirement without sending unrelated UI state.

The context is a slice around the requested targets, not the complete rendered
desktop. It carries exact source revision and coverage evidence. Missing or
ambiguous target, reference element, parent, order, or breakpoint stops before
the model call and produces a clarification.

### Spatial intent

Natural-language layout requests are normalized into semantic operations and
acceptance constraints. For example, "put checkboxes on the left" may become:

```json
{
  "op": "move",
  "target_ref": "field:shopping_item.done",
  "parent_ref": "container:shopping_item.row",
  "before_ref": "field:shopping_item.title",
  "breakpoints": ["wide", "compact"],
  "expected_source_revision": "ui:008"
}
```

The operation changes composition only. Existing value bindings, commands,
validation, role constraints, and functional-parity requirements remain
preconditions unless an accepted Issue explicitly changes them.

### Screenshot boundary

A screenshot can supplement structural context for aesthetics or a rendering
defect, but it is not part of the MVP default packet. It increases input size,
does not provide stable refs, and can obscure responsive and hidden state.
Screenshot capture, multimodal target resolution, visual-diff scoring, and
automatic screenshot inclusion remain deferred until structural composition
evidence proves insufficient for a named class of changes.

## Conversational Workflow Prototype MVP

### Purpose

The MVP prototypes only the conversational projection of a process: questions,
choices, typed answers, semantic commands, mock activities, and user-observable
outcomes. Web, Telegram, and Voice may present the interaction differently, but
they resolve the same command and state.

The target is requirement formalization before Automation, not a second general
workflow engine.

### Constrained profile

The first `PrototypeWorkflowProfile` admits only:

- one bounded conversational entry command per slice;
- sequential states and bounded conditional choice;
- typed user input and `input_required` continuation;
- semantic activities with `success`, `failure`, and `input_required` outcomes;
- deterministic mock/fixture activity bindings;
- localized messages and semantic actions;
- explicit cancel and one bounded retry where declared;
- happy-path, validation-failure, and provider-failure stories;
- a semantic simulation trace.

Parallel branches, nested workflows, arbitrary cycles, timers/schedules,
compensation sagas, active-instance migration, non-conversational workflow
surfaces, and a visual workflow editor are deferred.

### Candidate authority

The Prototype LLM still has no direct write authority over active
`workflow.json`. It may propose a schema-valid conversational slice and semantic
patch. AdaOS resolves scope, validates the constrained profile, runs stories,
and records Review. Materializing an accepted patch into the package definition
uses the ordinary governed authoring/admission path.

For a new project, the slice is extracted from an empty admitted base. For an
existing project, it is a bounded projection of the canonical workflow and
retains exact base definition ref, digest, generation, included refs, and locked
boundary refs.

### Simulation trace, not backend log

The Workbench may show a developer-facing simulation trace:

```text
14:32:10 command  save_recipe
14:32:10 state    editing -> saving
14:32:10 activity recipe.create
         mode     mock
         requires durable_write(RecipeDraft) -> Recipe
14:32:11 outcome  success
14:32:11 state    saving -> saved
```

The user-facing equivalent says that the recipe was **conditionally saved in
the Prototype** and that Automation must implement durable storage. It must not
claim that a database method ran.

### Automation handoff

An accepted conversational Prototype produces a requirement bundle containing:

- exact UI and conversational workflow revisions;
- logical data schemas and selected binding profile;
- activity requirements and implementation-mapping report;
- mock fixtures and generator provenance;
- accepted stories and observable success/failure/input-required states;
- Review constraints and unresolved fixture-only behavior.

Automation binds each semantic activity to code or explicitly retains it as
fixture-only. Missing mappings block handoff or candidate promotion. The same
stories are rerun against implementation bindings.

## Deferred General Workflow Projection And Round Trip

The following target is retained but is not part of the MVP implementation.

### Workflow Prototype Slice

A future general Workflow Prototype is not an independent simplified workflow.
It is a formal editable projection of the canonical definition:

```text
Canonical workflow
  -> extract(selector, base digest)
Workflow Prototype Slice
  -> edit through semantic operations
Semantic Workflow Patch
  -> validate/rebase/apply
New canonical workflow revision
```

The slice records source ref/digest, selector, generation, included refs,
boundary ports, binding profile, and projection version. Boundary and unsupported
nodes are visible but locked; they are never silently omitted. Applying no
changes preserves the canonical semantic digest, changes remain local to the
slice, and outside refs remain unchanged.

### Reverse projection from an implemented application

AdaOS cannot reliably reconstruct business intent from arbitrary code. The
supported future reverse path starts from the exact installed Release and its
canonical workflow, implementation bindings, package lock, stories, and runtime
evidence. Builder extracts the selected slice, detaches real effects, substitutes
mock bindings, and creates a new DEV Change. Runtime traces are discrepancy
evidence, not authority. Legacy code inference may create only a reviewed,
confidence-labelled candidate.

### Protection and migration boundary

Prototype experiments must not create a history of deprecated graph elements.
Protection begins only when an element is implementation- or runtime-bound:

| Protection | Prototype behavior |
| --- | --- |
| `none` | draft/experiment elements may be replaced or removed in a new revision |
| `implementation_bound` | accepted removal becomes deprecation plus an Automation migration obligation |
| `runtime_bound` | retirement additionally requires instance/data migration evidence |

Accepted Prototype revisions remain immutable artifacts, but removed unbound
nodes do not survive as tombstones in the next revision. Their history exists at
Run/Revision level. Automated nodes move through `active -> deprecated ->
retiring -> retired`; physical deletion is unnecessary for the first model.

Automation controls the technical Execution Graph and implementation bindings
within accepted contracts. A user-visible Business Workflow change returns as a
semantic proposal and Prototype Review rather than being changed silently for
implementation convenience.

### Deferred capabilities

- general UI-driven or mixed-channel workflow slices;
- full `workflow.prototype_slice` and `workflow.semantic_patch` ABIs;
- boundary expansion, graph-aware rebase, and round-trip laws;
- protected-node deprecation, migration obligations, drain, and retirement;
- reverse projection from stable applications;
- legacy workflow inference from code and traces;
- visual workflow editor or graph studio;
- parallel/nested/long-running workflow prototype execution;
- screenshot/multimodal composition context and visual-diff acceptance.

## MVP Invariants

1. Prototype execution has no undeclared live modifying effect.
2. Mock behavior is labelled and cannot satisfy implementation evidence.
3. UI, conversational workflow, data binding, and activity requirements retain
   separate refs and revisions.
4. A spatial change resolves stable refs, structural context, and breakpoint or
   asks for clarification before a model is called.
5. A compatible data-profile switch changes no UI or workflow revision.
6. A conversational workflow candidate cannot mutate the active definition
   without validation, Review, and admission.
7. Every demonstrated non-local behavior has a typed activity requirement and
   declared outcomes.
8. Automation handoff fails on missing required mappings.
9. Prototype experiments remain outside the accepted line until adopted.
10. Screenshot input and general workflow projection remain off by default and
    cannot become implicit context or authority.

## MVP Acceptance Evidence

The foundation is ready for implementation acceptance when one representative
project proves:

1. static examples, local state, and local CRUD over one logical schema;
2. deterministic provider/LLM fixture generation plus one explicit failure;
3. visible mock mode and no real write;
4. a spatial request applied by stable refs with wide/compact constraints;
5. one constrained conversational workflow exercised through Web and a limited
   channel with equivalent commands and outcomes;
6. a simulation trace that distinguishes mock execution from required backend
   behavior;
7. an Automation packet with complete activity mappings and the same stories;
8. a missing mapping that fails before Automation or candidate promotion.

The local reference suite now proves all eight items. It additionally verifies
the `prototype_runtime` manifest contract for scenarios, authoring-time skills,
and runtime skill validation; bounded compact/wide renderer evidence; stale
workflow digest rejection; one-retry/cancel limits; all 15 representative
profiles; exact handoff digest admission; and propagation of the admitted
packet into the isolated Codex request. Screenshots and the deferred general
workflow round trip remain outside this completion statement.
