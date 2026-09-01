# Agent And Project Context Capsules

Status: target architecture informed by a measured Builder run.

Last reviewed: 2026-09-01.

## Problem

AdaOS development work has several kinds of context with very different
lifetimes:

- stable platform contracts and public SDK/API knowledge;
- project composition, decisions, constraints, and component relationships;
- current component source and semantic target locations;
- one Change, Dev Ticket, Builder repair, or validation attempt;
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

AdaOS uses hierarchical, immutable context capsules and ephemeral task
overlays. A warm model session is an optional cache only.

```text
Platform Knowledge Capsule
  -> Project Context Capsule
     -> Component Context Projection
        -> Change / Task Overlay
           -> bounded tool working set
```

Each lower layer contains only its delta and references the digest of its base.
Canonical contracts and source remain outside summaries and are retrieved by
typed reference when needed.

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

## Agent Granularity

Builder opens a project-scoped Development Session. It does not require a
permanent model process for that project.

An implementation may keep a warm execution session keyed by:

```text
(project_context_digest, component_projection_digest, agent_profile_digest)
```

The warm session has these constraints:

- it is disposable and cannot be the only copy of a decision;
- switching projects creates a new scope instead of merging memories;
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

## Context Broker

Root MCP is the agent-facing context front door. The target Context Broker
provides typed operations equivalent to:

```text
context.resolve(scope_ref, task_intent, budget)
context.overview(capsule_ref)
context.drilldown(refs, byte_budget)
context.source_slices(target_refs, base_digest)
context.checkpoint(run_ref, outcome)
context.invalidate(event_ref)
context.inspect(run_ref)
```

`resolve` returns a deterministic packing plan before model submission. It
selects compact overviews first and exact details second. The model may request
more detail, but core enforces scope, byte budget, freshness, and RBAC.

The canonical packet may retain rich evidence by reference. The model-facing
projection must not stringify that complete packet into provenance and then
embed it again elsewhere.

## Storage And Events

Capsule metadata is relational/resource data; larger immutable projections are
content-addressed artifacts. The minimum generic records are:

```json
{
  "schema": "adaos.context.capsule.v1",
  "capsule_id": "ctxcap.<id>",
  "scope_ref": "project:<id>",
  "kind": "project",
  "base_refs": ["ctxcap.<platform-id>"],
  "source_digests": {},
  "summary_ref": "artifact://context/<digest>",
  "index_refs": [],
  "policy_ref": "policy:<id>",
  "locale": "en",
  "created_at": "<timestamp>",
  "digest": "sha256:<digest>"
}
```

The record is immutable. A mutable binding selects the current capsule for a
project/session. Signed operational events invalidate or advance bindings when
source, release, SDK, ABI, role policy, tickets, or accepted changesets change.

This model fits Declarative Resource Workbench: Builder can inspect capsule
layers, included/omitted refs, freshness, access decisions, and measured costs
without exposing hidden model state.

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

- capsule and task-overlay digests;
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
- cache reuse is reported but cannot conceal growth in unique context.

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

## Relationship To Existing Contracts

- `AgentContext` remains the process runtime dependency container; it is not
  model memory.
- `prompt_state.json` remains a bounded authoring preference/specification
  surface until migrated to capsule bindings; it is not a canonical transcript.
- `adaos.builder.context_packet.v1` remains the exact per-Run execution packet
  and should reference the selected capsule layers.
- `adaos.builder.development_session.v1` remains the authority boundary for
  targets, context members, artifacts, budget, and agent profile.
- Context Compression owns overview/detail projections and prompt packing.
- Root MCP owns agent-facing retrieval and audit.
- Development Signals and Dev Tickets provide change intent and lifecycle, not
  project memory storage.

