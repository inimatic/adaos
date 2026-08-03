# Agent Behavior Interface (ABI)

This folder contains JSON Schemas used by AdaOS for validation and by editors or Builder workflows for structure hints.

- `dcd.v1.schema.json` - device capability descriptor
- `latent.v1.schema.json` - latent state payload
- `lrpc.v1.schema.json` - lightweight RPC messages
- `nb.v1.schema.json` - notebook payload
- `scenario.schema.json` - scenario manifest (`scenario.yaml`)
- `skill.schema.json` - skill manifest (`skill.yaml`), including browser
  `data_routes` for explicit Yjs/stream/details route planning
- `builder.task.v1.schema.json` - Builder task handoff packet for human,
  AI-assisted, and human-in-the-loop capability creation workflows
- `builder.draft.v1.schema.json` - Builder draft workspace metadata before
  validation, preview, approval, and runtime apply
- `builder.issue.v1.schema.json` - one independently testable Builder
  requirement, defect, or acceptance concern
- `builder.change.v1.schema.json` - canonical bounded delivery scope spanning
  Issues, Runs, revisions, Trial, and Release evidence
- `builder.run.v1.schema.json` - one LLM, Codex, deterministic transformer,
  evaluator, or recovery attempt linked to a Change
- `builder.context_packet.v1.schema.json` - bounded, stable-digested execution
  context assembled from refs instead of an unbounded transcript
- `builder.action_risk.v1.schema.json` - deterministic side-effect,
  confirmation, approval, isolation, rollback, and limited-channel policy for
  one Builder command risk class
- `builder.interaction_frame.v1.schema.json` - chat-first message, context,
  risk-aware actions, and rich-view projection
- `builder.process_projection.v1.schema.json` - dependent Change -> Prototype
  -> Automation -> Trial -> Publication lineage and exact Preview choices
- `builder.project.v1.schema.json` - project portfolio, release/component refs,
  scoped Change focus, conflict/dependency indexes, and coordination generations
- `builder.binding_profile.v1.schema.json` - explicit mock, fixture, sandbox,
  live-readonly, and live Preview data boundary plus implementation mappings
- `builder.semantic_ui_change.v1.schema.json` - reversible semantic operation
  against stable declarative UI refs
- `builder.review_anchor.v1.schema.json` - durable target model for Review
  feedback; current browser-local storage remains a compatibility draft
- `artifact.attestation.v1.schema.json` - detached Ed25519 package/release
  provenance statement bound to immutable subject and predicate digests
- `artifact.release-attestation-set.v1.schema.json` - immutable exact
  attestation references bound to one ProjectRelease without changing its
  canonical release digest
- `artifact.trust-store.v1.schema.json` - local publisher trust keys, allowed
  signing purposes, validity windows, rotation, and fail-closed revocation state
- `endpoint-audio-events.v1.schema.json` - MVP endpoint audio event wire
  contract for ReDevice and future endpoint agents
- `nlu.teacher.v1.schema.json` - NLU Teacher request/thread, candidate,
  clarification, feedback, idempotency, scope, response policy, and MCP
  capability profile contracts
- `conversation.output.v1.schema.json` - semantic conversation output before
  channel-specific `ResponseEnvelope` materialization
- `conversational.package_manifest.v1.schema.json` - git-versioned
  `conversational/manifest.yaml` contract for skill/scenario conversational
  sources and compiled output refs
- `conversational.input.v1.schema.json`,
  `conversational.affordances.v1.schema.json`,
  `conversational.repair.v1.schema.json`,
  `conversational.output.v1.schema.json`, and
  `conversational.story.v1.schema.json` - design-time conversational package
  sources for IntentProposal inputs, workflow-facing affordances, repair
  behavior, semantic output templates, and executable conversation stories
- `conversational.validation_report.v1.schema.json` - Builder/SDK validation
  report for package schema checks, workflow cross-checks, and story runner
  evidence
- `webui.v1.schema.json` - skill WebUI contributions (`webui.json`), including
  staged readiness hints, stream receiver budget/guard metadata, runtime
  data sources, skill-owned UI view interfaces, modal address contracts, and
  browser media surface contracts such as `visual.frameViewer`
- `webui.v1.types.d.ts` - TypeScript declaration helpers for authoring and
  reviewing WebUI view, modal route, modal domain, ownership, and diagnostics
  contracts against `webui.v1`
- `webui.semantic.v0.schema.json` - draft semantic browser UI ABI for future semantic views, typed bindings, view state, and typed actions layered above `webui.v1`

## Current Manifest Runtime Extensions

The ABI includes the typed runtime metadata used by activation-aware workspace orchestration.

### Skill runtime activation

`skill.runtime.activation` describes when a skill should perform expensive work:

- `mode: eager | lazy | on_demand`
- `startup_allowed`
- `background_refresh`
- `when.scenarios_active`
- `when.client_presence`
- `when.webspace_scope`
- `when.webspaces`

### Scenario to skill bindings

`scenario.runtime.skills` lets the scenario own dependency truth:

- `required`
- `optional`

Compatibility note:

- `scenario.depends` remains valid and is treated by runtime code as a legacy alias for required scenario skills.

### Browser data routes

`skill.data_routes` is a reviewable design contract for browser-facing data. It
does not move data by itself; it documents the route chosen by the skill author:

- `route: yjs` for compact reconnect-stable bootstrap/control state
- `route: stream` for live variables, active rows, telemetry, logs, and event
  tails
- `route: tool/details`, `skill-local`, or `disk/360log` for explicit
  drill-down or diagnostic evidence

`status` and `statusPlane` are intentionally not valid data routes. Status
cards are compact summaries that reference one of the routes above; they must
not carry live rows, inventory tables, logs, or diagnostic payloads.

`webui.webio.receivers[*]` can declare stream budgets, freshness fields,
snapshot policy, and guard visibility so stream pressure is attributable during
review and later runtime diagnostics.

`visual.frameViewer` is the first typed browser media surface in `webui.v1`.
It renders stream-provided media through browser-routed descriptors such as
`hub_browser_media`, keeps large media payloads out of Yjs, and declares
fullscreen, keyboard, swipe, and action-button behavior as UI-as-data.

### Skill UI interfaces and modal addressing

`webui.interface.views` declares stable skill-domain UI views. Concrete
widgets and modals implement those views without making callers depend on
renderer-private ids.

Modal descriptors can declare:

- `implements`: the public view ids implemented by the modal.
- `schema.interface.routes`: concrete modal routes, optional typed params, and
  the private state patch produced by a validated address.
- `schema.interface.domain`: declarative modal domain states such as list,
  entity edit, or draft states. Each state maps to a modal route and may name
  the entity id param used by that route.
- `schema.interface.ownership`: the ownership split for skill domain truth,
  modal route state, browser-local view state, and durable persistence
  acknowledgments.

`navigate` opens a domain view on a supported surface. `navigateModal` changes
the route of an already opened modal. Runtime validation failures are reported
through UI diagnostics so mismatches between skill descriptors and client
behavior can be repaired from logs.
