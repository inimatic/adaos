# Builder Automation Skill

Status: local SDK-backed runtime slice with chat follow-ups, atomic
finalization, and Forge checkpoint gating.

`builder_automation_skill` is the system adapter between the Builder product
surface and the implementation executor. It owns the Automation-stage tool
contract; it does not contain the Codex runner, duplicate Skill Factory state,
or edit project files directly.

## Boundary

The ownership split is:

- Prompt IDE / Builder selects a skill or scenario and produces an approved
  implementation brief.
- `builder_automation_skill` exposes `start`, `chat`, and `get_state` to the UI.
- `BuilderAutomationService` persists the session and normalizes each turn into
  an `adaos.builder.realize_request.v1` task.
- `LocalSkillFactoryWorker` executes the bounded local Codex iteration, validates
  generated artifacts, and reports the task result.
- the normal skill/scenario lifecycle remains responsible for staging,
  activation, publication, rollback, and destructive actions.

The skill lives in `.adaos/workspace/skills`, because it is part of the AdaOS
platform runtime. It is not a user-editable Builder template and must not be
resolved from a project dev space.

## Tool Contract

`start` begins implementation from an explicit `object_type`, `object_id`, and
`implementation_brief`. Repeated starts while the same project is active are
idempotent and return the existing session.

`chat` submits one follow-up implementation instruction. The first revision
serializes iterations per project: while a task is active, the skill reports
`automation_busy` instead of creating an ambiguous concurrent branch.

`get_state` returns the compact
`adaos.builder.automation_projection.v1` projection. The projection contains
only render-safe lifecycle data: selected project, current iteration and task,
busy/terminal/input flags, summary/error, and the stable steps `queued`,
`workspace`, `implementation`, `verification`, and `result`.

Session files and full Skill Factory task evidence remain service state and are
not copied into the Web UI document.

## Delivery And Recovery

Every persisted session update emits `builder.automation.changed`. Builder UI
can update from that event and call `get_state` for first paint or recovery.
The persisted session and Skill Factory task are authoritative if an event is
missed. The local worker mirrors each bounded progress transition into the
Automation session, so `workspace`, `implementation`, `verification`, and
`result` can advance without UI polling. Unchanged state reads do not emit a
new event.

Ordinary UI chat is always dispatched to `builder_skill.chat`. That skill owns
the selected Builder project/thread, checks its workflow state, and uses
`adaos.sdk.builder.automation` for `get_state` / `submit`. The HTTP tool
transport has no Builder-specific service interception and therefore cannot
route a message using an unrelated stale Automation session.

Worker completion is not the terminal Automation state. The session remains
`commit_ready` while it:

1. checkpoints the companion skill and scenario in Forge;
2. verifies current commit/task metadata;
3. prepares and activates the DEV skill;
4. rematerializes the paired DEV scenario.

Only then does it become `completed`. Any unconfirmed checkpoint becomes a
terminal `forge_checkpoint` failure before activation. A follow-up turn moves
the preceding readiness into bounded history and clears summary, failure,
task, and progress fields so navigation/reconnect cannot resurrect the old
terminal projection.

## First-Revision Limits

- execution is the bounded local worker; remote isolated dev-node dispatch is
  the compatible target, not part of this slice;
- one implementation task may be active for a project at a time;
- cancellation, retry policy controls, diff review, staging, and activation are
  intentionally not invented in the skill before their core contracts exist;
- the projection is a backend contract; the final Builder Automation screen
  may compose it with chat, artifacts, tests, and dev-preview status.
- the Root scenario-draft endpoint still needs a durable asynchronous or
  idempotent commit acknowledgement: the archive may update while nginx returns
  `504` and Redis commit metadata remains stale. The client retries one
  transient failure, but Automation correctly remains failed if the current
  commit cannot be confirmed.

Primary references:

- [Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Skill Factory and Isolated Dev Nodes](skill-factory.md)

