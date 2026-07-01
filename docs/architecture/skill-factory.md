# Skill Factory and Isolated Dev Nodes

Status: target architecture, current-state delta, and implementation roadmap.

This document defines the target architecture for turning Builder ideas into
implemented AdaOS skills, scenarios, UI descriptors, connectors, and related
artifacts through isolated development workers.

The key principle is:

```text
Root does not send "make a skill".
Root issues a bounded development task envelope.

The dev node does not access the user subnet directly.
The dev node receives task-scoped snapshots, mocks, and validation tools.

Codex does not operate AdaOS directly.
Codex runs inside a private developer skill that constrains files, commands,
MCP access, git access, and result reporting.

The user hub does not trust the dev result blindly.
The user hub pulls the commit, validates it, tests it, and only then presents
it for staging, approval, or repair.
```

## Relationship To Builder

[AdaOS Builder](builder.md) owns the product workflow from idea to governed
artifact. The Skill Factory is the remote realization layer that can execute
that work outside the user's active hub.

The current Builder implementation already has local draft, preview, patch,
Pending Action, and workbench slices. This document describes the next
architecture step where Builder can submit a normalized `realize_request` to
Root, and Root can dispatch it to an isolated AdaOS-controlled development
worker.

This layer must remain compatible with:

- [AdaOS Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Root MCP Foundation](root-mcp-foundation.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Pending Actions](pending-actions.md)

## Current Implemented Slice

AdaOS already has useful foundations, but not the Skill Factory itself.

Implemented today:

- Builder can create local draft and preview artifacts through the existing dev
  workspace and lifecycle facades.
- Prompt IDE has a paired Builder dev webspace model for current source
  webspaces.
- `builder_skill` owns the first conversation-native draft and patch flow, with
  Pending Action review handoff.
- Root MCP has descriptor cache, `AdaOSDevPlane`, session leases, and
  plane-scoped tool contracts.
- The skill runtime supports prepare, test, activate, rollback, A/B slots,
  lifecycle diagnostics, and quarantine markers.
- `tool_bridge` can proxy tool calls across hub/member nodes and now enforces
  runtime action-risk approval gates for dangerous effects.

Missing for this target architecture:

- Root-owned development queue.
- Isolated Dev Node registry, heartbeat, lifecycle, and task assignment.
- Private developer skill installed inside isolated dev-node containers.
- Task-scoped MCP bridge for realization tasks.
- Forge task-branch discipline with sparse checkout and result manifests.
- Codex runner wrapper that receives controlled instruction files instead of
  broad repo access.
- User Hub result pull, validation, staging, and approval loop from a task
  branch.
- Retry, cancellation, failure feedback, and cleanup contracts for dev tasks.

## Target Flow

```text
Prompt IDE / Builder
  -> RealizeRequest
  -> User Hub
  -> Root Server / Dev Queue
  -> Isolated Dev Node
  -> adaos-forge private git
  -> commit / package / result.json
  -> Root ready event
  -> User Hub validation
  -> Prompt IDE / Dev Webspace staging and approval
  -> normal skill or scenario runtime lifecycle
```

The Isolated Dev Node is an AdaOS-controlled development worker. It never
modifies the user subnet directly. It receives bounded development tasks from
Root, checks out only the required forge paths, uses task-scoped MCP access to
read specifications and snapshots, invokes Codex with a constrained instruction
set, commits results to a task branch, reports completion, and cleans up local
artifacts. The User Hub remains responsible for final validation, staging,
approval, and installation.

## Core Entities

### Prompt IDE / Builder

The human-facing creation surface. It captures an idea, draft, correction, or
missing capability and normalizes it into a `realize_request`.

Prompt IDE should not submit free-form "build this skill" text to Root. It
should submit structured task data with target artifact refs, acceptance
criteria, sparse repo paths, constraints, and requested MCP scopes.

### User Hub

The hub for the user's subnet. It hosts the Builder conversation, source
webspace, paired dev webspace, local validation, staging, and approval surface.

The User Hub submits realization work to Root, but it remains the final
validator before any result enters the user's runtime.

### Root Server / Dev Queue

The central coordinator for development work. It owns:

- dev-node identity and authorization
- dev-node registry and heartbeat status
- realization task queue
- task assignment and cancellation
- task-scoped token issuance
- timeout and retry policy
- result and failure events
- audit trail
- notification back to the User Hub

Root is a coordinator, not a relay. It should accept normalized tasks and
dispatch them through a queue policy.

### Isolated Dev Node

A temporary or semi-persistent AdaOS development subnet, usually running in a
container. It includes:

- AdaOS hub runtime
- private developer skill
- Codex CLI or runtime wrapper
- git client
- MCP client
- task workspace
- local test tooling

For MVP, a dev node should run one active task at a time:

```yaml
dev_node_registration:
  node_id: devnode_xxx
  node_type: isolated_dev_node
  capabilities:
    - codex
    - git_sparse_checkout
    - adaos_sdk
    - mcp_client
    - local_tests
  status: registered_waiting
  trust_level: isolated
  max_parallel_tasks: 1
```

### Private Developer Skill

A non-public skill installed only inside Isolated Dev Nodes. It owns the dev
task lifecycle:

- bootstrap and health checks
- device auth flow
- registration with Root
- polling for assignments
- sparse checkout and task branch setup
- Codex instruction packet generation
- Codex subprocess execution
- test and validation commands
- commit and result manifest creation
- status reporting
- cleanup

This skill is not installed in the user's runtime and is not a marketplace
skill. It is a controlled development worker component.

### adaos-forge

A private git repository or repository group that stores development artifacts,
task branches, and generated evidence.

The target shape is:

```text
main
dev/user-subnet-abc
realize/task-001-shopping-list
```

The dev node works only in a task branch. The user hub can fetch that branch or
an approved revision for local validation and staging.

### Task MCP Bridge

A restricted Root MCP path for one assigned development task. It is
task-scoped, read-mostly, and snapshot-oriented.

Allowed MVP tools:

- `get_capability_snapshot`
- `get_requirement_spec`
- `get_ui_draft`
- `get_datasource_schema`
- `get_mock_data`
- `run_staging_validation`
- `report_progress`

Denied operations:

- reading real user data
- reading secrets
- executing production actions
- publishing directly
- modifying user runtime state
- broad repository or filesystem browsing outside the task scope

## Realize Request

When a user moves a prototype to `Realize`, Builder should submit a structured
request:

```yaml
realize_request:
  schema: adaos.builder.realize_request.v1
  request_id: realize_001
  user_subnet_id: subnet_abc
  source_session_id: devsess_123
  source_conversation_id: conv.builder.default

  target:
    type: skill
    id: shopping_list_skill

  artifacts:
    requirement_spec_id: req_v4
    ui_draft_id: ui_v6
    datasource_schema_id: ds_v2
    ui_mapping_id: mapping_v3
    acceptance_criteria_id: ac_v2

  repo:
    forge_project: user_abc_devspace
    base_branch: dev/user-subnet-abc
    sparse_paths:
      - skills/shopping_list/
      - scenarios/shopping_list/
      - docs/requirements/shopping_list/

  constraints:
    no_external_api: true
    no_secrets: true
    must_add_tests: true
    must_update_manifest: true

  mcp:
    requested_scope:
      - capability_snapshot
      - mock_runtime
      - staging_validation
```

Root validates the user, target, policy, and queue limits before turning this
into a task assignment.

## Dev Node Lifecycle

Dev-node states:

| State | Meaning |
| --- | --- |
| `provisioning` | Container exists, but AdaOS and the private skill are not ready. |
| `auth_pending` | The private developer skill is completing device auth. |
| `registered_waiting` | Root recognizes the node and it can receive a task. |
| `assigned` | Root assigned a task, but workspace setup has not started. |
| `preparing_workspace` | Sparse checkout, branch setup, and task files are being prepared. |
| `developing` | Codex is running inside the bounded task workspace. |
| `testing` | Local tests, manifests, schemas, and validation commands are running. |
| `committing` | The result is being committed and pushed to forge. |
| `reporting` | The dev node is reporting result metadata to Root. |
| `cleanup` | Temporary workspace, tokens, and local sensitive artifacts are being removed. |
| `waiting` | The node is reusable for another task. |
| `failed` | The node or task failed and needs retry, repair, or retirement. |

Task states:

```text
queued
assigned
workspace_preparing
in_progress
tests_running
commit_ready
completed
failed
cancelled
expired
```

The dev-node state and task state are separate. A node can fail after a task is
already requeued, and a completed task can leave the node in cleanup before it
returns to `waiting`.

## Identity And Credentials

Do not collapse these credentials:

| Credential | Scope | Purpose |
| --- | --- | --- |
| device auth | node-scoped | Authorizes the dev node identity with Root. |
| task token | one task | Lets the node accept, progress, complete, or fail the assigned task. |
| MCP token | one task | Grants read-mostly snapshot and validation access for that task. |
| git credential | node or task | Allows checkout/push to allowed forge repo paths or branches. |
| Codex auth | runtime-local | Allows Codex execution inside the dev node without exposing AdaOS secrets in prompts. |

For MVP, the forge credential can be a node-scoped deploy key registered by
Root or the forge adapter. The target architecture should move toward
task-scoped credentials that can be revoked independently.

Rules:

- private keys are generated inside the dev node
- public keys are registered through Root/forge policy
- keys are limited to required repositories or branches where possible
- keys are revocable through Root
- private keys are not inserted into prompts
- MCP tokens are exposed as environment variables or tool bindings, not prompt
  text
- secrets from the user runtime are never visible to Codex

## Task Assignment

Root sends the dev node a task assignment:

```yaml
dev_task_assignment:
  schema: adaos.skill_factory.dev_task_assignment.v1
  task_id: task_001
  subnet_id: subnet_abc

  target:
    type: skill
    id: shopping_list

  forge:
    repo_url: git@forge:adaos/user_abc_devspace.git
    base_branch: dev/user-subnet-abc
    branch: realize/task_001
    sparse_paths:
      - skills/shopping_list/
      - scenarios/shopping_list/
      - docs/requirements/shopping_list/

  mcp:
    endpoint: https://root.example/v1/root/mcp/task/task_001
    token_ref: task_mcp_token
    scope:
      - read_capability_snapshot
      - read_requirements
      - read_mock_data
      - run_staging_validation

  codex:
    instruction_file: .adaos/tasks/task_001/task.md
    working_dir: workspace/
    mode: autonomous_bounded

  policy:
    network: restricted
    secrets_visible_to_llm: false
    require_tests: true
    require_commit: true
    cleanup_after_completion: true
```

For MVP, the dev node may create the task branch from the assigned base branch.
Root should still record the expected branch name and reject results that do
not match the task envelope.

## Codex Execution Packet

The private developer skill should not simply run Codex with the user's raw
request. It prepares a controlled instruction packet:

```text
.adaos/tasks/task_001/
  task.md
  project_context.md
  adaos_sdk_notes.md
  constraints.md
  acceptance_criteria.yaml
  allowed_files.txt
  denied_files.txt
  mcp_tools.md
  test_commands.md
```

Codex receives:

1. the goal
2. allowed file paths
3. denied file paths
4. test commands
5. MCP tool descriptions
6. acceptance criteria
7. result packaging rules

The developer skill enforces the boundary before and after Codex runs. If Codex
edits outside allowed paths, skips required tests, or produces forbidden
artifacts, the developer skill fails the task before commit.

## Result Contract

The result must include code and machine-readable evidence.

Expected artifact shape:

```text
skills/<skill_id>/
  skill.yaml
  handlers/
  tests/
  README.md

scenarios/<scenario_id>/
  scenario.yaml
  tests/

docs/requirements/<target_id>/
  skill_request.yaml
  implementation_notes.md
  acceptance_report.md
  risk_report.md
  changelog.md

.adaos/tasks/<task_id>/
  result.json
  test_report.json
  changed_files.txt
  sanitized_logs/
```

`result.json`:

```yaml
dev_result:
  schema: adaos.skill_factory.dev_result.v1
  task_id: task_001
  status: completed
  commit_hash: abc123
  branch: realize/task_001
  changed_paths:
    - skills/shopping_list/
    - scenarios/shopping_list/
  tests:
    status: passed
    command: pytest
  validation:
    manifest_valid: true
    permissions_valid: true
  notes:
    - Implemented local shopping list skill.
  open_questions: []
```

Commit messages must include the `task_id` and should reference the target
artifact id.

## Ready Event And Hub Validation

When Root accepts a completed result, it sends the User Hub a ready event:

```yaml
dev_ready_event:
  schema: adaos.skill_factory.dev_ready_event.v1
  task_id: task_001
  subnet_id: subnet_abc
  target:
    type: skill
    id: shopping_list
  forge:
    branch: realize/task_001
    commit_hash: abc123
  result:
    status: completed
    tests: passed
  next_action:
    - pull_revision
    - validate_locally
    - show_to_user
```

The User Hub then:

1. fetches the task branch or commit
2. updates the local development skill or scenario view
3. validates manifest, permissions, data routes, and schemas
4. runs local tests or staging smoke checks
5. creates or updates Pending Actions for approval, rejection, or repair
6. presents the result in Prompt IDE / Dev Webspace
7. only after approval, uses the normal skill or scenario lifecycle to install
   and activate the result

This gives two validation layers:

- Dev Node validates isolated build and tests against snapshots and mocks.
- User Hub validates compatibility with the concrete user subnet and staging
  environment.

## Failure And Retry

Failure reports should be structured:

```yaml
dev_task_failure:
  schema: adaos.skill_factory.dev_task_failure.v1
  task_id: task_001
  stage: testing
  error_type: test_failed
  summary: Manifest validation failed.
  logs_ref: sanitized_log_001
  commit_hash: null
  retryable: true
```

Root may:

- retry on the same node
- retry on a different node
- return the failure to Builder as a repair prompt
- create a follow-up task with previous logs and constraints
- mark the task failed after max retries
- cancel the task if the user cancels or the source draft is superseded

## Queue Policy

MVP queue policy:

```text
priority:
  1. user-triggered realize
  2. fix failed staging
  3. proactive improvement
  4. background refactor

limits:
  - one active task per subnet
  - one active task per dev node
  - task timeout
  - max retries
  - explicit cancellation by user
```

Later versions can add parallel tasks per dev node, node pools by capability,
regional placement, cost budgets, and model/runtime variants.

## Cleanup

After each task, the dev node must remove:

- workspace clone
- temporary MCP token
- temporary task token
- local task files containing sensitive context
- local caches
- generated artifacts outside git
- logs containing sensitive runtime details

It must preserve:

- forge commit
- `result.json`
- task status and audit events
- sanitized logs
- test report
- dev history in forge

Cleanup must be observable. A node should not return to `waiting` until cleanup
either succeeds or records a non-sensitive failure reason.

## Current-Code Alignment

The target architecture deliberately reuses current AdaOS rails instead of
inventing a separate development system:

- Root MCP descriptors and session leases are the right foundation for
  task-scoped snapshot and validation access.
- Builder task and draft contracts are the right source for
  `realize_request` normalization.
- Pending Actions are the approval and review surface after User Hub
  validation.
- Skill runtime prepare/test/activate/rollback remains the only path from
  generated artifact to active runtime.
- `tool_bridge` action-risk gates are relevant to runtime execution, but they
  are not a substitute for dev-node task scoping.
- Existing hub/member proxying shows the current routing substrate, but the
  Skill Factory needs a separate queue, heartbeat, and result protocol.

## Roadmap

### Phase 0. Architecture And Contracts

- [x] `[must]` Define the target architecture and responsibility boundaries in
  this document.
- [ ] `[must]` Name canonical schemas:
  `adaos.builder.realize_request.v1`,
  `adaos.skill_factory.dev_node_registration.v1`,
  `adaos.skill_factory.dev_task_assignment.v1`,
  `adaos.skill_factory.dev_result.v1`,
  `adaos.skill_factory.dev_ready_event.v1`, and
  `adaos.skill_factory.dev_task_failure.v1`.
- [ ] `[must]` Decide whether the first forge backend is private GitHub,
  Gitea, GitLab, or existing AdaOS registry infrastructure.
- [ ] `[should]` Decide whether task branches are created by Root or by the dev
  node for the first implementation. Default MVP recommendation: dev node
  creates the branch from an assigned base branch.

### Phase 1. Realize Request Normalization

- [ ] `[must]` Add a Builder `realize_request` schema that references
  conversation, draft, preview, acceptance criteria, sparse paths, constraints,
  and requested MCP scope.
- [ ] `[must]` Make Prompt IDE / Builder emit `realize_request` instead of raw
  implementation text when the user asks to realize a prototype.
- [ ] `[must]` Keep local Builder draft/preview flow working when remote
  realization is unavailable.
- [ ] `[should]` Link `realize_request` records to Pending Actions and Builder
  conversation threads.

### Phase 2. Forge Workspace Discipline

- [ ] `[must]` Define private forge project layout for skills, scenarios,
  requirements, and task evidence.
- [ ] `[must]` Implement sparse checkout path calculation from target artifact
  and Builder draft metadata.
- [ ] `[must]` Require task branches for remote realization results.
- [ ] `[must]` Validate that a dev result only changes allowed paths.
- [ ] `[should]` Add branch retention and cleanup policy for abandoned or
  superseded tasks.

### Phase 3. Root Dev Queue And Node Registry

- [ ] `[must]` Add Root-side dev-node registration records with capabilities,
  trust level, status, heartbeat, and max parallel tasks.
- [ ] `[must]` Add a Root dev queue with task states, priority, timeout,
  cancellation, retry count, and source refs.
- [ ] `[must]` Add polling endpoints or Root MCP tools for dev-node
  assignment, status events, completion, failure, and heartbeat.
- [ ] `[should]` Publish queue and dev-node status to operator diagnostics.

### Phase 4. Isolated Dev Node Bootstrap

- [ ] `[must]` Define the container image contents: AdaOS hub, private
  developer skill, Codex runtime, git client, MCP client, and test tooling.
- [ ] `[must]` Implement device auth and Root-issued dev-node identity.
- [ ] `[must]` Install and start the private developer skill on boot.
- [ ] `[must]` Register the dev node with Root and enter
  `registered_waiting`.
- [ ] `[should]` Add a local dev-node simulator for repository tests and
  operator trials.

### Phase 5. Task-Scoped Credentials And MCP

- [ ] `[must]` Issue task tokens separately from node identity.
- [ ] `[must]` Issue task-scoped MCP leases with read-mostly capability
  profiles.
- [ ] `[must]` Register and revoke forge credentials through Root or a forge
  adapter.
- [ ] `[must]` Keep MCP tokens and git private keys out of Codex prompt text.
- [ ] `[should]` Move from node-scoped deploy keys to task-scoped forge
  credentials after the first MVP path works.

### Phase 6. Codex Runner Wrapper

- [ ] `[must]` Generate the Codex execution packet from the task assignment,
  Builder descriptors, SDK notes, allowed files, denied files, tests, and MCP
  tool docs.
- [ ] `[must]` Run Codex as a bounded subprocess or service inside the dev-node
  container.
- [ ] `[must]` Enforce allowed path changes before commit.
- [ ] `[must]` Run required tests and manifest/schema validation before
  reporting completion.
- [ ] `[should]` Capture sanitized logs and concise repair evidence for failed
  runs.

### Phase 7. User Hub Validation And Approval

- [ ] `[must]` Let the User Hub fetch a task branch or commit by `dev_ready_event`.
- [ ] `[must]` Validate manifests, permissions, data routes, route budgets,
  schemas, and tests in the user's staging context.
- [ ] `[must]` Present result, tests, risks, and unresolved issues in Prompt
  IDE / Dev Webspace.
- [ ] `[must]` Use Pending Actions for approve, refuse, request changes, test,
  or postpone decisions.
- [ ] `[must]` Apply approved results through normal skill or scenario
  lifecycle rails, not through the dev node.

### Phase 8. Failure Loop And Repair Tasks

- [ ] `[must]` Convert dev-node failure reports and User Hub validation
  failures into Builder repair context.
- [ ] `[must]` Deduplicate repeated failures and supersede stale tasks when the
  source draft changes.
- [ ] `[must]` Support cancellation by user and timeout expiry by Root.
- [ ] `[should]` Add golden task fixtures for success, test failure, forbidden
  file edit, MCP denial, cancelled task, and hub validation failure.

### Phase 9. Hardening And Scale

- [ ] `[should]` Add node pools by capability profile, runtime stack, and cost
  class.
- [ ] `[should]` Add WebSocket/SSE assignment delivery after polling is proven.
- [ ] `[should]` Add task-scoped forge credentials and branch protection.
- [ ] `[should]` Add operator UI for queue depth, node health, task history,
  policy denials, and retry decisions.
- [ ] `[could]` Add parallel tasks per dev node after isolation, cleanup, and
  workspace separation are proven.

## Acceptance Criteria

The architecture is implemented when:

- Builder can submit a normalized `realize_request` for a skill, scenario, UI,
  datasource, or connector target.
- Root queues the task, assigns it to a registered isolated dev node, and
  tracks heartbeat, timeout, cancellation, and retries.
- The dev node receives only task-scoped forge and MCP access.
- Codex runs with generated instruction files, allowed paths, denied paths,
  test commands, and tool bindings.
- The dev node commits results to a task branch and returns `result.json`.
- Root sends a `dev_ready_event` to the User Hub.
- The User Hub fetches the commit, validates it locally, shows the result to
  the user, and uses Pending Actions for final decisions.
- Approved results enter the runtime only through normal prepare, test,
  activate, and rollback lifecycle rails.
- Failed results produce structured repair evidence without exposing secrets or
  real user data to the dev node.
