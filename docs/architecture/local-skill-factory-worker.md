# Local Skill Factory Worker

Status: first executable vertical slice.

The local Skill Factory worker lets Prompt IDE exercise the same realization
workflow that will later run in an isolated Docker dev node.  It is intended
for trusted operator development and debugging on the AdaOS host.

## Prompt IDE flow

1. The project remains in the normal prototype/TZ workflow while Builder and
   the remote LLM prepare the interface and implementation brief.
2. The user presses `Execute`.  `prompt_engineer_skill.tz_execute` produces the
   standard detailed implementation brief.
3. A successful result creates an `adaos.builder.automation_session.v1` and a
   normalized `adaos.builder.realize_request.v1`.
4. Prompt IDE switches to the virtual `automation` workflow state.
5. `LocalSkillFactoryWorker` polls the existing Skill Factory queue and runs
   Codex against a disposable git workspace.
6. A validated result is committed on the task branch and synchronized to the
   selected DEV scenario/skill roots.
7. Later `builder_skill:chat` turns for the same Prompt IDE webspace are routed
   to new local Codex iterations instead of the remote Builder LLM.

The standard Execute call remains the boundary between prototype authoring and
code realization.  Chat turns after that boundary modify the implementation;
they do not patch the rapid prototype through the old remote transformation.

## Generated project envelope

For a skill target the worker materializes:

```text
skills/<skill-id>/
docs/requirements/<skill-id>/
```

For a scenario prototype it materializes both the scenario and a companion
skill:

```text
scenarios/<scenario-id>/
skills/<scenario-id>_skill/
docs/requirements/<scenario-id>/
```

The companion skill starts from the current AdaOS skill template.  Template
identity, conversation ownership, tools, tests, and prompt references are
rewritten from `new_skill` to the generated skill id before Codex starts.

## Run artifacts

Local runs are stored below
`<state-dir>/skill_factory/local_runs/<task-id>/`:

```text
input/
  assignment.json
  packet.json
  task.md
  allowed_files.txt
workspace/
output/
  codex-live.jsonl
  codex-live.stderr.log
  last_message.md
  test_report.json
  result.json
runtime/
  state.json
  codex-events*.jsonl
  codex-stderr*.log
```

`GET /api/builder/automation/status` returns the current task and the local
paths for the live event stream, stderr, and result.  This makes the shell
process observable without exposing an arbitrary shell through the UI.

## Validation and repair

Before synchronization the worker enforces:

- all changes remain in the assignment sparse paths;
- every JSON and YAML file parses;
- generated Python handlers compile;
- every `webui.json` passes `adaos.webui.v1` validation;
- required skill/scenario files exist;
- generated skill pytest suites pass in a bounded subprocess;
- dependency-file changes are recorded for review;
- bytecode and pytest caches are removed.

If deterministic validation fails, Codex receives one repair turn containing
the exact errors.  The task is reported as failed only when the repair still
does not pass.  Failed workspaces and sanitized diagnostics remain available;
they are not copied into DEV.

## Local security profile

The process inherits only the OS paths and Codex home required for local Codex
authentication.  AdaOS tokens, provider API keys, and other environment
variables are not inherited.  The prompt denies secrets, production data,
external APIs, and changes outside the task paths.

On Windows the current native Codex sandbox is not writable in this host
profile, so the trusted `local-process` backend defaults to
`danger-full-access` inside the disposable task checkout.  This is a debugging
profile, not a multi-tenant security boundary.  Set
`ADAOS_LOCAL_CODEX_SANDBOX=workspace-write` where the native sandbox is
available.  The Docker worker must provide the real filesystem/network
boundary and can use `workspace-write` (or bypass the inner sandbox only when
the container is externally constrained).

## API

- `POST /api/builder/automation/start` starts or reuses a project session.
- `POST /api/builder/automation/turn` queues an implementation correction.
- `GET /api/builder/automation/status?object_type=...&object_id=...` returns
  the session, task progress, live log paths, result, or failure evidence.

Normal operation starts through Prompt IDE Execute and Builder chat; the API is
also useful for tests and operator diagnostics.
