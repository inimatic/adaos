# Agent Behavior Interface (ABI)

This folder contains JSON Schemas used by AdaOS for validation and by editors or Builder workflows for structure hints.

- `dcd.v1.schema.json` - device capability descriptor
- `latent.v1.schema.json` - latent state payload
- `lrpc.v1.schema.json` - lightweight RPC messages
- `nb.v1.schema.json` - notebook payload
- `scenario.schema.json` - scenario manifest (`scenario.yaml` or `scenario.json`)
- `skill.schema.json` - skill manifest (`skill.yaml`), including browser
  `data_routes` for explicit Yjs/stream/details route planning
- `builder.task.v1.schema.json` - Builder task handoff packet for human,
  AI-assisted, and human-in-the-loop capability creation workflows
- `builder.draft.v1.schema.json` - Builder draft workspace metadata before
  validation, preview, approval, and runtime apply
- `nlu.teacher.v1.schema.json` - NLU Teacher request/thread, candidate,
  clarification, feedback, idempotency, scope, response policy, and MCP
  capability profile contracts
- `webui.v1.schema.json` - skill WebUI contributions (`webui.json`), including
  staged readiness hints, stream receiver budget/guard metadata, runtime
  data sources, and browser media surface contracts such as
  `visual.frameViewer`
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
