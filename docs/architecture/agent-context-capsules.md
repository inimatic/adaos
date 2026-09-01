# Agent Context Graph And Capsules

Status: target architecture informed by measured Builder and Research Workbench
runs.

Last reviewed: 2026-09-01.

## Problem

AdaOS agent work has several kinds of context with different owners and
lifetimes:

- stable platform contracts and public SDK/API knowledge;
- durable domain aggregates such as a ResearchDirection and ResearchTask;
- project composition, decisions, constraints, and component relationships;
- current component source and semantic target locations;
- one Change, Dev Ticket, Builder repair, or validation attempt;
- accepted evidence, releases, and cross-project dependency state;
- transient tool output, runtime diagnostics, and conversation turns.

Putting all of them into one long conversation makes restoration expensive,
allows stale facts to survive source changes, and makes the result difficult to
replay. Creating one opaque long-lived model agent per project does not solve
this boundary. It may reduce transport or improve provider cache reuse, but
anything restored into the model context still consumes attention and may be
metered as input.

The durable owner of development knowledge is therefore AdaOS, not a model
provider session. A model agent is a replaceable executor over versioned AdaOS
context.

## Measured Baseline

The 2026-09-01 Subscription repair
`task.01M1ECZHQ0NDC88T2Y7PVC3VGB` provides a concrete baseline:

| Observation | Value |
| --- | ---: |
| Model-facing `task.md` | 5,876 characters, 618 whitespace words |
| Completed shell commands | 14 |
| Visible command output | 21,145 characters |
| Provider input tokens | 332,145 |
| Cached input tokens | 294,656 |
| Fresh input tokens | 37,489 |
| Output tokens | 3,632 |
| `fresh_plus_output` | 41,121 |
| Cache share of input | 88.7% |
| Internal assignment file | 303,434 bytes |
| Canonical packet file | 122,504 bytes |

The large provider input is cumulative across tool boundaries, not the size of
the initial ticket prompt. Most input was cache-reused, but the run still
needed repeated model/tool cycles.

The internal envelope also duplicated data independently of model usage:

- `realize_request` occupied about 181 KB of compact JSON;
- top-level `snapshot_context` occupied about 104 KB;
- the same context packet appeared again as a 62 KB provenance string;
- one irrelevant resolved repair occupied about 29 KB inside
  `repair_context.tasks`;
- the full workflow ABI and adapter catalog occupied about 12.5 KB;
- qualified source slices were empty, so Codex rediscovered all target
  locations with shell reads.

These are separate optimization problems. Prompt caching helps repeated model
prefixes. It does not remove duplicate envelopes, irrelevant histories, missing
source indexes, or unnecessary model turns.

## Decision

AdaOS uses a typed, immutable context graph, purpose-specific projections, and
ephemeral task overlays. A warm model session is an optional cache only.

```text
                         Platform Knowledge Capsule
                                      |
       +------------------------------+----------------------------+
       |                                                           |
Domain Aggregate Capsule                                   Project Context Capsule
Direction -> Task -> Accepted Compilation                  Project -> Component
       |                                                           |
       +------------> Implementation Track Handoff <---------------+
                                      |
                          Development Session / Change Overlay
                                      |
                             bounded tool working set

SourceBundle, Evidence, ProjectRelease, policy, and SDK contract capsules are
shared immutable nodes referenced from more than one branch.
```

This is a directed acyclic graph, not a storage tree. Project is the primary
engineering boundary, but it is not the universal owner of domain knowledge.
A domain aggregate may outlive many Projects; one Project may serve several
tasks or tracks. Every projection contains only its delta and digest-bound refs
to canonical nodes. Canonical contracts, source, evidence, and accepted
decisions remain outside summaries and are retrieved by typed reference.

## Context State Classes

The graph separates four classes that have different persistence and mutation
semantics:

| Class | Examples | Mutation rule |
| --- | --- | --- |
| Authoritative knowledge | accepted contracts, ResearchCompilation, policy, ProjectRelease | only an owning workflow may supersede or revoke it |
| Procedural memory | validated playbooks, repair strategies, SDK usage guidance | evidence-gated promotion with version and rollback |
| Episodic memory | runs, failures, comments, rejected paths, user feedback | append-only evidence with retention policy; never grants authority |
| Working context | selected cards, source slices, recent observations, model transcript | disposable projection scoped to one run |

An LLM may propose a memory candidate but cannot promote an observation,
summary, or successful trajectory into authoritative or procedural state by
writing its own context. Promotion follows a governed sequence:
`propose -> qualify -> validate -> accept -> supersede/revoke`. It records the
source episodes and evaluator and may require human or policy approval.

### Platform Knowledge Capsule

The platform capsule is generated from exact core, SDK, ABI, UI schema, and
Root MCP descriptor revisions. It contains compact cards and indexes, not a
copy of the repository.

Its cache identity includes at least:

```text
agent_profile_version
core_contract_digest
sdk_surface_digest
abi_catalog_digest
client_schema_digest
```

The capsule can be shared by many projects. A project agent must not maintain
its own divergent copy of platform semantics.

### Domain Aggregate Capsule

A domain capsule is owned by a typed domain workflow rather than by Builder or
the model provider. It binds exact aggregate identities, revisions, accepted
decisions, authority, and downstream refs. Examples include a research
Direction/Task/Compilation chain, a governed workflow instance, or another
long-lived resource aggregate whose lifecycle crosses Projects.

A domain projection declares:

- aggregate and selected-subject refs, revisions, and branch lineage;
- accepted knowledge and unresolved decision refs;
- source/evidence visibility and trust policy;
- downstream Project, ImplementationTrack, release, or execution refs;
- purpose-specific overview indexes without copying full activity history.

The generic context layer does not define domain transitions. Research Fabric,
Governed Workflow Runtime, or another owning capability validates those
transitions and publishes immutable capsule nodes or binding events.

### Project Context Capsule

The project capsule is scoped to one `ProjectDefinition` and base
`ProjectRelease` or local source generation. It contains:

- owned and dependency component graph;
- target, read-only context, and artifact authority boundaries;
- entry points and presentation relationships;
- accepted decisions and active acceptance constraints;
- active Change and Dev Ticket overview refs;
- known core blockers and linked capability tickets;
- public contract and source-index digests;
- last accepted and current Trial changeset refs;
- locale, role, and policy context needed by the task.

Full ticket histories, Builder timelines, screenshots, source files, schemas,
and logs are referenced rather than embedded. The capsule belongs to the
project/workspace and remains usable when the executor changes from Codex to
another model or deterministic worker.

It does not own accepted scientific meaning, user feedback lifecycle, or
cross-project platform knowledge. Those arrive as declared read-only refs with
their original authority and trust classification intact.

### Component Context Projection

A component projection binds one skill, scenario, modal, widget, route, or
workflow to an exact source digest. It contains a semantic source index:

- semantic ref to file and stable JSON pointer or source symbol;
- compact public contract and dependency cards;
- neighboring UI structure needed for spatial edits;
- relevant validation commands and fixtures;
- component-local decisions and unresolved findings.

Source text is fetched as a bounded exact slice. A summary never authorizes a
mutation by itself.

### Change / Task Overlay

The task overlay contains only the current intent:

- Change, Dev Ticket, repair, and task ids;
- user summary and bounded clarification result;
- acceptance checks and validation profile;
- allowed write paths and exact base generation;
- selected target refs and source slices;
- token, wall-time, and tool-round budget;
- output contract and required evidence.

Previous runs are represented by outcome, candidate digest, unresolved reason,
and evidence refs. Their full timelines are retrieved only for recovery or
audit.

### Shared Reference Capsules

Large immutable objects that participate in several contexts remain independent
nodes rather than being copied under each subject:

- SourceBundle and bounded prepared-source views;
- Evidence, validation receipts, traces, and screenshots;
- ProjectRelease, candidate release, and source-generation manifests;
- SDK/API/ABI contract sets and capability-gap records;
- policies, mandates, role bindings, locale resources, and model profiles.

Audience-specific views may redact or summarize a node, but retain a link to
the same canonical digest. A view cannot silently change the authority,
sensitivity, license, or trust class of its source.

## Agent Granularity

Builder opens a project-scoped Development Session. Researcher opens a
direction/task focus. Evaluator opens an evidence/release focus. None requires
a permanent model process for that subject.

An implementation may keep warm execution sessions, but the cache key follows
the active role and work focus:

```text
Researcher: (direction_id, task_id, accepted_revision, agent_profile_digest)
Builder:    (track_id, development_session_id, project_context_digest,
             agent_profile_digest)
Codex:      (task_overlay_digest, component_projection_digest,
             agent_profile_digest)
Evaluator:  (study_or_evidence_ref, project_release_digest,
             evaluator_profile_digest)
```

The warm session has these constraints:

- it is disposable and cannot be the only copy of a decision;
- switching the selected subject creates a new scope instead of merging
  memories;
- a source, ProjectRelease, SDK, ABI, policy, or role digest change invalidates
  affected layers;
- restoration reconstructs the prompt from capsules and refs, not from an
  opaque transcript;
- cross-project knowledge is visible only through declared dependency or
  reusable platform/resource refs;
- no secret, raw private artifact, or unrestricted path enters reusable
  memory.

Suspending work writes a small checkpoint: current goal, completed decisions,
open questions, candidate/evidence refs, and exact capsule digests. It does not
serialize every tool result or conversation message.

## Context Control Plane

The generic control plane has four separate responsibilities:

| Service | Responsibility |
| --- | --- |
| Context Registry | immutable capsule metadata, typed graph edges, current bindings, lineage, retention |
| Context Resolver | subject, purpose, audience, authority, policy, freshness, and dependency closure |
| Context Compiler | utility/risk selection, budget packing, model-specific layout, source slices, cache plan |
| Memory Curator | candidate qualification, evidence-gated promotion, supersession, revocation, and rollback |

Root MCP is one agent-facing adapter over this control plane, not the authority
or persistence model. API and SDK consumers use the same typed services without
shelling out to MCP or CLI.

The target operations are equivalent to:

```text
context.resolve(subject_refs, purpose, audience, policy_ref)
context.plan(resolution_ref, model_profile, token_budget, latency_budget)
context.compile(plan_ref, output_format)
context.overview(capsule_ref, audience)
context.drilldown(refs, byte_budget, audience)
context.source_slices(target_refs, base_digest)
context.propose_memory(source_refs, candidate_kind)
context.promote(candidate_ref, validation_refs, authority_ref)
context.checkpoint(run_ref, outcome)
context.invalidate(event_ref)
context.inspect(run_ref)
```

`resolve` computes the admissible graph; `plan` selects the minimal sufficient
working set; `compile` creates the provider/model-specific representation.
Selection accounts for required dependency closure, relevance, decision
utility, negative-transfer risk, freshness, sensitivity, and marginal token
cost rather than using unqualified top-k retrieval.

The resulting plan records required, candidate, selected, omitted, denied, and
unavailable refs with reasons. The model may request more detail, but core
enforces subject scope, byte/token budget, freshness, trust, and RBAC. The
canonical packet may retain rich evidence by reference; its model projection
must not stringify and embed the same packet as provenance elsewhere.

## Storage And Events

Capsule metadata is relational/resource data; larger immutable projections are
content-addressed artifacts. The minimum generic resources are Capsule,
Relationship, SubjectBinding, ContextPlan, MemoryCandidate, and ContextReceipt.
The capsule envelope starts with:

```json
{
  "schema": "adaos.context.capsule.v2",
  "capsule_id": "ctxcap.<id>",
  "subject_refs": ["project:<id>", "component:<id>"],
  "kind": "project",
  "relationship_refs": [
    {"type": "uses", "ref": "ctxcap.<platform-id>"}
  ],
  "authority_ref": "project:<id>",
  "trust_class": "accepted",
  "sensitivity": "workspace",
  "retention_class": "accepted_release_lineage",
  "source_digests": {},
  "summary_ref": "artifact://context/<digest>",
  "index_refs": [],
  "policy_ref": "policy:<id>",
  "locale": "en",
  "valid_from": "<timestamp>",
  "valid_to": null,
  "recorded_at": "<timestamp>",
  "supersedes_refs": [],
  "created_at": "<timestamp>",
  "digest": "sha256:<digest>"
}
```

The record is immutable. A mutable binding selects the current capsule for a
subject, role focus, or session. Signed operational events invalidate or
advance bindings when source, release, SDK, ABI, role policy, tickets, accepted
changesets, domain revisions, or evidence change.

`valid_from/valid_to` describe when a claim or contract applies in its domain;
`recorded_at` describes when AdaOS observed it. `as_of` reconstruction uses
both, so a replay can recover the exact admissible context at decision time.
Supersession does not erase prior accepted state. Compaction may deduplicate
physical artifacts by digest while preserving all logical refs and events.

### Trust, Privacy, And Taint

Content addressing proves identity, not truth or safety. Every capsule and
derived view carries authority, trust, sensitivity, license, retention, and
origin metadata. Untrusted source text, screenshots, voice/Telegram input,
tool output, repositories, and prior agent trajectories remain tainted through
derived summaries until an owning validator explicitly promotes a claim.

Source content cannot grant tools, widen write paths, reveal denied refs, or
change a mandate. Provider/model caches are partitioned by authorization and
data-residency policy. Revocation removes current bindings and future
retrieval, while durable audit retains the permitted minimum evidence.

This model fits Declarative Resource Workbench: Builder can inspect capsule
layers, included/omitted refs, freshness, access decisions, and measured costs
without exposing hidden model state.

### Independent Invalidation Domains

Invalidation follows typed graph edges instead of clearing one project-wide
memory:

| Event | Invalidates | Does not rewrite |
| --- | --- | --- |
| prepared source or prototype revision | dependent formulation/compilation candidates | prior accepted compilation |
| accepted compilation revision | AutomationBrief and track handoff projections | unrelated Project history |
| component source or ProjectRelease change | project/component execution projections | scientific task meaning |
| SDK/API/ABI revision | affected platform and project contract projections | domain evidence |
| new Study/evidence result | result-review and synthesis projections | historical release or compilation |
| role/policy/sensitivity change | audience views and provider cache bindings | canonical artifact bytes |

### Provider-Neutral State And Model Layout

Canonical capsules do not encode one model's prompt format. Context Compiler
selects ordering, detail level, tool descriptions, multimodal representation,
and stable-prefix/mutable-suffix layout for an exact model profile. Prompt
cache identity includes the selected capsule digests, model/provider profile,
authorization partition, and compiler version. Cache hits are an optimization;
they are never a restoration or audit mechanism.

## Execution Routing

Context optimization starts before prompt construction:

1. Apply an admitted deterministic structured edit when exact operations and
   preconditions are available. Model tokens: zero.
2. Use a bounded patch agent when target slices, acceptance, and file authority
   are complete.
3. Use full Codex only for open-ended implementation, ambiguous source
   discovery, or cross-component reasoning.
4. Escalate a project task to a linked core capability ticket when public
   SDK/API constraints make it unsatisfiable.

A missing context facet is not permission for broad exploration. The resolver
either retrieves the facet, requests bounded clarification, or records a typed
context/SDK gap.

## Context Attribution Receipt

Every model or agent run records `adaos.agent.context_receipt.v1` with:

- subject, purpose, audience, capsule, and task-overlay digests;
- resolver/compiler/model-profile versions and policy decision ref;
- required, selected, omitted, denied, unavailable, and drill-down refs with
  reasons;
- unique bytes and estimated tokens per layer;
- provider input, cached input, output, and reasoning tokens;
- tool/model boundary count;
- result bytes grouped by tool and selected ref;
- repeated reads and duplicate embedded fields;
- source-slice coverage before the first model call;
- drill-down refs, context misses, and invalidation reasons;
- execution route and escalation reason;
- validation outcome and resulting evidence refs.

Subscription may present both provider-billable usage and the optimization
view (`fresh_plus_output`, cache ratio, avoided model tokens). Neither metric
may silently replace the other.

## Initial Performance Gates

The first gates are comparative rather than model-specific absolute promises:

- a structured edit invokes no model and records zero provider tokens;
- a surgical repair starts with complete qualified target coverage or an
  explicit context-miss receipt;
- resolved historical repairs and full workflow catalogs are absent from the
  task prompt unless selected by relevance;
- the assignment stores one canonical packet and digest-bound refs rather than
  repeated stringified copies;
- the same acceptance fixture passes before and after compression;
- fresh input, output, tool boundaries, latency, and validation retries improve
  against the recorded Subscription baseline;
- cache reuse is reported but cannot conceal growth in unique context;
- a counterfactual wrong-project or wrong-task candidate is rejected even when
  semantically similar;
- removing a required context unit causes a detected context miss or measurable
  acceptance regression rather than silent success;
- restart/reconnect reconstructs the same authoritative refs and an equivalent
  working projection without restoring a hidden provider transcript;
- evaluation reports task success, context recall/precision, stale-context
  failures, negative transfer, latency, and provider/accounting cost together.

## Failure Modes

| Failure | Required control |
| --- | --- |
| Stale project memory | digest-bound capsules and event invalidation |
| Cross-project leakage | explicit project/session scope and RBAC |
| Hidden model-only decisions | checkpoint to AdaOS resources/evidence |
| Summary changes semantics | canonical drill-down and validation |
| Context grows forever | immutable snapshots, bounded projections, ref-only history |
| Warm agent becomes authority | disposable cache contract and replay test |
| Missing source detail causes exploration | semantic source index and coverage gate |
| Compact context hides a core limitation | typed SDK/core capability escalation |
| Successful episode becomes trusted guidance | governed memory promotion with evidence and rollback |
| Untrusted input poisons later sessions | taint propagation, authority filtering, revocation, adversarial tests |
| Domain meaning is forced under Project | typed subject graph and independent authority refs |
| Concurrent branches overwrite focus | optimistic bindings, immutable branches, explicit merge/conflict |
| Provider-specific layout becomes canonical | provider-neutral capsules and versioned compiler profiles |

## State-Of-The-Art Alignment And Claim Boundary

The architecture composes established directions rather than claiming a new
memory algorithm:

- hierarchical/virtual context movement follows
  [MemGPT](https://arxiv.org/abs/2310.08560);
- modular working, episodic, semantic, and procedural memory follows
  [CoALA](https://arxiv.org/abs/2309.02427);
- incremental curated context avoids repeated-summary collapse described by
  [Agentic Context Engineering](https://arxiv.org/abs/2510.04618);
- bounded handoff filtering and replaceable sessions are consistent with
  [OpenAI Agents SDK handoffs](https://openai.github.io/openai-agents-python/handoffs/);
- event history and cold replay follow durable-execution practice represented
  by [Temporal](https://docs.temporal.io/workflow-execution);
- Entity/Activity/Agent lineage maps to
  [W3C PROV-O](https://www.w3.org/TR/prov-o/);
- comparative retrieval/trajectory evaluation is informed by
  [ContextBench](https://arxiv.org/abs/2602.05892);
- tainted persistent-memory tests respond to the memory-poisoning threat model
  in [From Untrusted Input to Trusted Memory](https://arxiv.org/abs/2606.04329).

AdaOS's candidate systems contribution is continuity across user/domain
intent, scientific or workflow authority, software Project composition,
agent execution, evidence, release, human acceptance, subscription accounting,
and subnet evolution while agents and providers remain replaceable. The typed
multi-authority graph, purpose/audience compiler, evidence-gated memory
promotion, and exact Context Receipt form one governed context control plane.

That composition is a hypothesis, not a performance claim. A novelty or SOTA
claim requires fixed-model, fixed-tool, fixed-authority, and matched-total-cost
comparisons against full history, project-only packets, and retrieval
baselines. Required outcomes include task/evidence validity, context
recall/precision, negative transfer, security violations, recovery, latency,
and cost across Builder and at least one non-development domain such as
Research Workbench.

## Relationship To Existing Contracts

- `AgentContext` remains the process runtime dependency container; it is not
  model memory.
- `prompt_state.json` remains a bounded authoring preference/specification
  surface until migrated to capsule bindings; it is not a canonical transcript.
- `adaos.builder.context_packet.v1` remains the exact per-Run execution packet
  and should reference the selected capsule layers.
- `adaos.builder.development_session.v1` remains the authority boundary for
  targets, context members, artifacts, budget, and agent profile.
- Context Compression owns resolution planning, overview/detail projections,
  model-specific prompt packing, and comparative context evaluation.
- Root MCP owns one agent-facing retrieval adapter and audit surface; it is not
  capsule storage or workflow authority.
- Development Signals and Dev Tickets provide change intent and lifecycle, not
  project memory storage.
- Research Fabric owns Direction/Task/Compilation/ImplementationTrack
  transitions and publishes their context nodes; the generic context plane does
  not infer scientific acceptance from chat or Builder activity.

