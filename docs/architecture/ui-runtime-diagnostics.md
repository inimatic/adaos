# UI Runtime Diagnostics to Skill Logs

## Purpose

Browser-side runtime issues are part of the skill authoring feedback loop. When
the UI notices a broken modal action, renderer failure, missing stream payload,
or other skill-owned surface problem, the diagnostic should be visible in the
same context as the skill runtime logs. This keeps LLM-assisted debugging focused
on the entity being developed instead of forcing it to mine the platform-wide
`adaos.log`.

## Contract

- The browser records user-visible runtime issues in `[Node 0] Notifications`.
- In `dev` runtime mode only, UI notifications whose `source` starts with
  `ui.` are sent to `POST /api/node/ui/diagnostics`.
- The node writes accepted diagnostics as JSONL to
  `.adaos/logs/service.<skill>.ui_runtime.log`.
- In-process `adaos.*` log records emitted while a `CurrentSkill` is active are
  routed to `.adaos/logs/service.<skill>.runtime.log` and suppressed from the
  platform-wide `adaos.log`.
- Service-skill stdout/stderr keeps using
  `.adaos/logs/service.<skill>.log`.
- MCP `get_skill_logs(skill="<skill>")` reads all matching files:
  `service.<skill>.log`, `service.<skill>.runtime.log`,
  `service.<skill>.ui_runtime.log`, and future
  `service.<skill>.*.log` files.

## Skill Attribution

The preferred attribution path is explicit metadata:

- UI materialization annotates skill-provided modal definitions with
  `originSkill` and `_adaos.originSkill`.
- The browser includes node/modal context in diagnostic payloads.
- The node resolves modal ownership from live Yjs `ui/application/modals`.

If attribution cannot be resolved, diagnostics fall back to
`service.__ui_runtime__.ui_runtime.log`. That fallback is intentional: it keeps
unexpected browser/runtime failures visible without pretending they belong to a
specific skill.

## Why HTTP, Not Yjs

Diagnostics use a bounded HTTP ingestion endpoint rather than Yjs branches. This
avoids feedback loops where a UI failure writes to Yjs, triggers render/update
work, and produces more UI failures. Yjs remains the source for UI runtime state;
HTTP is only the side channel for observability.

## TODO

- Add a typed ABI schema for `ui.runtime.diagnostic.v1`.
- Add per-skill log retention and rotation policy for `*.runtime.log` and
  `*.ui_runtime.log`.
- Add richer widget-level ownership metadata, not only modal ownership.
- Surface skill-log links from `[Node 0] Notifications` when running in dev.
- Feed `get_skill_logs(skill=...)` into the future LLM skill debugging MCP
  workflow automatically.
- Add rate-limit counters and duplicate suppression for noisy renderer failures.
