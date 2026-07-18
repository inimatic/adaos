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

AdaOS already has useful foundations, but not the complete remote Skill
Factory path.

Implemented today:

- Builder can create local draft and preview artifacts through the existing dev
  workspace and lifecycle facades.
- Builder can normalize a draft/prototype into
  `adaos.builder.realize_request.v1`, persist it in Builder state, link it to
  Builder conversation/Pending Action metadata, and optionally enqueue it for
  remote realization.
- Prompt IDE has a paired Builder dev webspace model for current source
  webspaces.
- `builder_skill` owns the first conversation-native draft and patch flow, with
  Pending Action review handoff.
- Root MCP has descriptor cache, `AdaOSDevPlane`, `SkillFactoryTaskPlane`,
  session leases, and plane-scoped tool contracts.
- Root has a local Skill Factory queue/state service with dev-node
  registration, heartbeat, assignment polling, task priority/timeout/retry,
  cancellation, result validation, ready events, and diagnostics.
- The MVP forge policy uses an AdaOS registry/local forge-compatible backend,
  sparse checkout paths, and dev-node-created `realize/` task branches.
- The skill runtime supports prepare, test, activate, rollback, A/B slots,
  lifecycle diagnostics, and quarantine markers.
- `tool_bridge` can proxy tool calls across hub/member nodes and now enforces
  runtime action-risk approval gates for dangerous effects.

Missing for this target architecture:

- Private developer skill installed inside isolated dev-node containers.
- Task-scoped MCP bridge and credential issuance for realization tasks.
- Codex runner wrapper that receives controlled instruction files instead of
  broad repo access.
- Actual isolated dev-node container bootstrap and device auth.
- User Hub result pull, validation, staging, and approval loop from a task
  branch.
- Branch cleanup execution, dev-node cleanup enforcement, and scale policy.
- Critical MVP security controls for Codex/dev-node execution: prompt-injection
  handling, token separation, restricted network, bounded logs, dependency
  review, and cleanup attestation.
- Delivery semantics for retries, duplicate reports, Root restarts, node
  crashes, stale assignments, and branch/result replay.
- Concrete staging artifact model on the User Hub: temporary slot/dev
  webspace, release record, rollback target, and conflict handling.
- Supply-chain provenance for generated work: runner/container version,
  dependency changes, branch protection, optional signatures, and future SBOM
  or attestation evidence.
- Artifact-specific acceptance gates beyond simple skills: scenarios, UI
  descriptors, datasource schemas, connectors, NLU descriptor fixes, and
  model-backed skills.

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

The task snapshot includes a versioned, read-only target-runtime reference
bundle in addition to product requirements. It carries schema versions,
browser action/data-expression ABI, manifest examples, and validation commands.
This prevents a model from producing syntactically valid descriptors for an
action vocabulary that the installed client does not implement.

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

The worker/orchestrator performs cleanup itself before validation and commit;
cleanup is not delegated to a generated LLM shell command. It also preserves
the UTF-8 bytes and digest of the original user turn from Prompt IDE through
the assignment packet, so a localized request cannot degrade into a different
instruction without a transport failure being reported.

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

Canonical MVP task-scope capabilities:

- `read_capability_snapshot`
- `read_requirements`
- `read_ui_draft`
- `read_datasource_schema`
- `read_mock_data`
- `run_staging_validation`

Builder may use authoring aliases such as `capability_snapshot`,
`requirement_spec`, or `mock_runtime`, but Root normalizes assignments to the
canonical capability names above. Task status operations such as assignment
polling, progress reporting, completion, and failure reporting belong to the
Skill Factory task-control plane, not to the read-mostly task snapshot bridge.

Denied operations:

- reading real user data
- reading secrets
- executing production actions
- publishing directly
- modifying user runtime state
- broad repository or filesystem browsing outside the task scope

## Realization Versus Runtime Readiness

The generated repository tree owns source artifacts. The User Hub orchestrator
owns prepared runtime slots, activation markers, handler reload, resolver and
materialization cache invalidation, paired-webspace reload, and runtime smoke
evidence. A dev-node result is therefore not user-visible completion merely
because it committed and synchronized successfully.

For a scenario with a companion skill, the acceptance pipeline is:

```text
dev result -> host validation -> DEV sync -> prepare -> activate
           -> handler/cache refresh -> forced scenario rematerialization
           -> bounded tool/UI smoke -> terminal chat success
```

The forced rematerialization is required even if the current scenario id is
unchanged, because its `webui.json`, companion manifest, or handler contract may
have changed under the same identity. Any failure in this host-owned tail is a
`live_readiness` failure and returns evidence to Builder instead of producing a
false success message.

## Security Threat Model And Risk Register

The target architecture has to list all meaningful dev-node and Codex risks
even when the MVP only implements the critical controls. A deferred risk is not
accepted as safe; it is a recorded item that must be revisited before larger
scale, public ecosystem use, or stronger multi-tenant operation.

MVP critical controls are the minimum required for the first remote
realization path. Deferred controls can follow after the one-task-per-node path
is proven, but they must remain visible in this document and the roadmap.

| Risk | MVP treatment | Later / deferred treatment |
| --- | --- | --- |
| Prompt injection in requirement specs, UI drafts, mock data, logs, or retrieved context | Separate user content from runner instructions; use generated instruction files; deny secrets and production actions; enforce allowed paths after Codex runs. | Add taint tracking, context scanning, prompt-injection classifiers, and richer red-team fixtures. |
| Secret or token exfiltration through prompts, logs, commits, or result artifacts | Keep MCP/git/task credentials out of prompt text; expose credentials through runtime bindings or env; sanitize logs; bound result artifacts; revoke task credentials. | Add DLP scanning for logs/patches/results and automated secret scanning before push and before User Hub fetch. |
| Broad filesystem or repository access | Use sparse checkout, `allowed_files.txt`, `denied_files.txt`, post-run changed-path validation, and branch validation. | Add OS/filesystem sandbox enforcement and per-task mount namespaces beyond repository-level sparse paths. |
| Network egress and external API calls | Default task policy is restricted network and `no_external_api=true`; any exception requires explicit task policy and User Hub validation. | Add egress proxy, network allowlists, per-domain audit, and replayable network evidence. |
| Malicious or flaky generated tests | Run generated tests only inside the isolated dev node with timeouts and no user-subnet credentials; User Hub reruns validation before approval. | Add syscall/resource sandboxing for tests and test provenance/flake scoring. |
| Dependency poisoning or uncontrolled package downloads | Treat new runtime dependencies as review evidence; disallow broad/heavy/external dependencies unless acceptance criteria and User Hub policy allow them. | Add dependency allowlists, lockfile enforcement, SBOM generation, vulnerability scanning, and package provenance checks. |
| Container or sandbox escape | Run dev work outside the user subnet and avoid mounting user runtime secrets; one active task per node for MVP. | Add hardened runtime options such as stronger container profiles, VM/firecracker-style isolation, seccomp/AppArmor policy, and host-level attestation. |
| Git branch/result spoofing | Root records expected branch and sparse paths; result validation rejects wrong branch, missing commit, and changed paths outside the task envelope. | Add signed commits, protected task branches, forge-side required checks, and result attestations. |
| Stale assignments, duplicate completion, Root restart, or dev-node crash | Use task ids, assigned-node validation, timeout, retry, cancellation, and status history; design completion/failure reporting to be idempotent. | Add explicit delivery semantics, durable outbox/inbox records, duplicate suppression keys, and crash-recovery chaos suites. |
| Privacy leakage through snapshots or mock data | Task MCP must provide redacted snapshots and deterministic mocks, not raw user data or secrets. | Add snapshot provenance, freshness metadata, redaction proofs, synthetic-data generation policy, and privacy review tooling. |
| Log poisoning, oversized logs, or sensitive runtime details in logs | Capture sanitized logs only; bound log size; keep raw local logs out of result artifacts. | Add structured log scrubbers, DLP, artifact byte budgets, and log-retention policies per task class. |
| Codex auth abuse | Keep Codex auth local to the dev runtime and outside prompts; do not expose AdaOS secrets to Codex. | Add ephemeral model-provider credentials, per-task model budgets, and provider-side audit correlation. |
| Cost, quota, and task-abuse pressure | MVP limits one active task per subnet and one active task per dev node with timeouts and cancellation. | Add node pools, cost classes, per-user quotas, placement policy, and budget enforcement. |
| Cleanup failure leaves residual task data | A node must not return to `waiting` until cleanup succeeds or records a non-sensitive cleanup failure. | Add ephemeral disks, cryptographic wipe where applicable, cleanup attestations, and periodic node reimaging. |
| Dev-node image tampering or drift | Registration should report dev-node image/runtime version, private skill version, and runner version. | Add signed image digests, image attestations, vulnerability scanning, and enforced minimum runner versions. |

The MVP must implement or explicitly stub the left-column critical controls for
the first remote realization slice. The right-column controls are deferred
unless a deployment profile requires them earlier.

## Task Snapshot And Mock Data Contract

Task-scoped MCP does not expose live user data. It exposes bounded,
versioned, redacted context packets and deterministic mock data.

Every task snapshot or mock dataset should carry:

- schema id and schema version
- source artifact refs such as draft id, preview id, requirement id, or
  descriptor id
- generated timestamp and freshness/TTL hints
- redaction level and privacy notes
- deterministic seed or fixture id for mock data
- byte-size budget and truncation markers
- provenance for the tool or service that produced it
- explicit statement that secrets and raw user data are absent

MVP can implement this as metadata around existing Builder descriptors and
mock payloads. Later versions should add redaction proofs, synthetic-data
quality checks, and snapshot compatibility tests.

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

## Realization Policy Matrix

The `constraints` block is not enough by itself. Root and the User Hub need a
policy matrix that classifies which generated changes are allowed, which are
manual-only, and which are disallowed for remote realization.

MVP policy classes:

| Change class | Default treatment |
| --- | --- |
| Pure UI descriptor changes with declared data routes | Allowed for remote realization; User Hub validation and approval still required. |
| Skill/scenario code without new permissions or external IO | Allowed for remote realization with tests and human approval. |
| New permissions, service processes, filesystem writes, network IO, device/endpoint control, high-rate streams, or credentials | Manual-only approval; must include risk evidence and rollback plan. |
| Real user data access, secret reads, destructive production actions, or direct runtime mutation from the dev node | Disallowed for remote realization. |
| New dependencies or model artifacts | Manual-only unless a policy explicitly allows the dependency profile. |
| Descriptor/manifest/NLU hint fixes | Allowed when blast-radius preview and rollback evidence exist. |

Deferred work should turn this table into enforceable policy descriptors used
by Builder preview, Root queue admission, dev-node task policy, and User Hub
staging approval.

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
  provenance.json
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
  provenance:
    dev_node_id: devnode_xxx
    runner_version: private-developer-skill/0.1.0
    image_digest: sha256:...
    instruction_packet_hash: sha256:...
    dependency_changes: []
  notes:
    - Implemented local shopping list skill.
  open_questions: []
```

Commit messages must include the `task_id` and should reference the target
artifact id.

## Artifact Acceptance Matrix

The Skill Factory must not be skill-only. Builder can ask for several artifact
classes, and each class needs explicit validation gates before the User Hub can
present an approval action.

| Target artifact | MVP acceptance gates | Later / deferred gates |
| --- | --- | --- |
| Skill | `skill.yaml` schema, declared tools, data routes/projections, permissions, tests, conversation/runtime safety lints, allowed paths. | Public-quality examples, broader generated-skill evals, marketplace readiness review. |
| Scenario | `scenario.yaml` schema, dependency bootstrap report, materialization/rebuild check, webspace preview, tests. | Multi-webspace compatibility matrix and scenario migration/rollback UX. |
| UI descriptor / `webui.json` | WebUI schema, data-source path validity, widget/modal registry merge, browser preview, route-budget checks. | Browser visual regression, accessibility pass, and staged hydration/readiness checks. |
| Datasource schema / mapping | Schema validity, mock data generation, no real user data exposure, deterministic examples, consumer binding check. | Synthetic data quality scoring and schema evolution compatibility tests. |
| Connector / integration | Capability manifest, credential boundary, no secret exposure, network policy, mocked external calls, explicit human approval. | External sandbox accounts, contract tests against providers, rate-limit and cost evidence. |
| NLU / descriptor fix | Patch targets only manifest/webui/nlu hint surfaces, phrase probes pass, blast-radius preview, rollback evidence. | Broader phrase corpus evals and promotion policy gates. |
| Model-backed skill | Same skill gates plus model manifest, artifact checksum, dependency profile review, no implicit model downloads. | SBOM, model provenance, vulnerability scan, and provider/runtime compatibility matrix. |

If an artifact type has no acceptance row, remote realization for that artifact
type should stay disabled or manual-only.

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

## User Hub Staging Model

The ready event is not an install command. The User Hub must materialize the
task result into a staging surface before any runtime activation.

MVP staging should define:

- a fetched task branch or commit pinned by `task_id` and `commit_hash`
- a staging source tree or temporary runtime slot that is distinct from the
  active user runtime
- a dev webspace or preview surface that can render generated UI without
  replacing the source webspace
- a release record linking `realize_request`, task id, commit hash,
  validation evidence, approval identity, target runtime slot, and rollback
  target
- conflict handling when the user workspace changed after the task started
- explicit Pending Actions for approve, refuse, request changes, test, and
  postpone

Approved results enter normal skill/scenario lifecycle rails only after this
staging validation and approval record exists.

## Failure And Retry

Failure reports should be structured:

```yaml
dev_task_failure:
  schema: adaos.skill_factory.dev_task_failure.v1
  task_id: task_001
  node_id: devnode_xxx
  status: failed
  failure_class: test_failed
  message: Manifest validation failed.
  stage: testing
  retryable: true
  retry_requested: false
  logs_ref: sanitized_log_001
  details:
    command: pytest
    exit_code: 1
```

Root may:

- retry on the same node
- retry on a different node
- return the failure to Builder as a repair prompt
- create a follow-up task with previous logs and constraints
- mark the task failed after max retries
- cancel the task if the user cancels or the source draft is superseded

## Delivery Semantics And Crash Recovery

The Skill Factory should assume at-least-once delivery between Root and dev
nodes. Polling, progress reports, completion reports, failure reports, ready
events, and cleanup reports can be repeated after retries or reconnects.

MVP rules:

- `task_id`, `request_id`, `node_id`, `branch`, and `commit_hash` are the
  primary idempotency fields.
- Root rejects completion or failure from a node that is not assigned to the
  task.
- Completion is idempotent when the same assigned node reports the same
  branch and commit hash again.
- A different commit hash for an already completed task is a conflict, not a
  silent update.
- Root timeouts and user cancellation supersede stale assignments.
- Dev nodes must check assignment freshness before committing or reporting.
- Root restart must not lose queued, assigned, completed, failed, cancelled,
  expired, or ready-event state.
- If a dev node crashes after pushing a commit but before reporting, the task
  remains assigned until timeout or explicit repair; later versions may scan
  forge branches to recover.

Deferred hardening:

- durable Root outbox/inbox records for every task-control transition
- explicit duplicate suppression keys on progress and result events
- chaos fixtures for Root restart, node crash, stale assignment, duplicate
  completion, token revoke mid-task, and branch replay
- WebSocket/SSE delivery after polling semantics are proven

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

This roadmap uses `[must]`, `[should]`, `[could]`, and `[deferred]`.
`[deferred]` means intentionally postponed, not forgotten or accepted as safe.
Deferred items remain part of the target architecture and should be revisited
before broader scale, stronger multi-tenant use, or public generated-artifact
distribution.

### Phase 0. Architecture And Contracts

- [x] `[must]` Define the target architecture and responsibility boundaries in
  this document.
- [x] `[must]` Name canonical schemas:
  `adaos.builder.realize_request.v1`,
  `adaos.skill_factory.dev_node_registration.v1`,
  `adaos.skill_factory.dev_task_assignment.v1`,
  `adaos.skill_factory.dev_result.v1`,
  `adaos.skill_factory.dev_ready_event.v1`, and
  `adaos.skill_factory.dev_task_failure.v1`.
- [x] `[must]` Decide whether the first forge backend is private GitHub,
  Gitea, GitLab, or existing AdaOS registry infrastructure.
- [x] `[should]` Decide whether task branches are created by Root or by the dev
  node for the first implementation. Default MVP recommendation: dev node
  creates the branch from an assigned base branch.
- [x] `[must]` Add an explicit dev-node/Codex security threat model and risk
  register that separates MVP critical controls from deferred hardening.
- [x] `[must]` Align documented examples with the current canonical schema
  field names, including `failure_class` / `message` for
  `adaos.skill_factory.dev_task_failure.v1`.
- [x] `[must]` Define the artifact acceptance matrix for skills, scenarios, UI
  descriptors, datasource schemas, connectors, NLU descriptor fixes, and
  model-backed skills.

### Phase 1. Realize Request Normalization

- [x] `[must]` Add a Builder `realize_request` schema that references
  conversation, draft, preview, acceptance criteria, sparse paths, constraints,
  and requested MCP scope.
- [x] `[must]` Make Prompt IDE / Builder emit `realize_request` instead of raw
  implementation text when the user asks to realize a prototype.
- [x] `[must]` Keep local Builder draft/preview flow working when remote
  realization is unavailable.
- [x] `[should]` Link `realize_request` records to Pending Actions and Builder
  conversation threads.
- [x] `[must]` Attach realization policy classification to every request:
  allowed, manual-only, or disallowed for remote realization.
- [x] `[should]` Attach snapshot/mock-data provenance, freshness, redaction
  notes, and deterministic fixture ids to request context.

### Phase 2. Forge Workspace Discipline

- [x] `[must]` Define private forge project layout for skills, scenarios,
  requirements, and task evidence.
- [x] `[must]` Implement sparse checkout path calculation from target artifact
  and Builder draft metadata.
- [x] `[must]` Require task branches for remote realization results.
- [x] `[must]` Validate that a dev result only changes allowed paths.
- [x] `[should]` Add branch retention and cleanup policy for abandoned or
  superseded tasks.
- [x] `[must]` Write task provenance evidence (`provenance.json`) with runner,
  image, instruction packet, dependency, and snapshot refs.
- [x] `[should]` Add dependency-delta review to forge evidence so the User Hub
  can distinguish code changes from new runtime supply-chain risk.

### Phase 3. Root Dev Queue And Node Registry

- [x] `[must]` Add Root-side dev-node registration records with capabilities,
  trust level, status, heartbeat, and max parallel tasks.
- [x] `[must]` Add a Root dev queue with task states, priority, timeout,
  cancellation, retry count, and source refs.
- [x] `[must]` Add polling endpoints or Root MCP tools for dev-node
  assignment, status events, completion, failure, and heartbeat.
- [x] `[should]` Publish queue and dev-node status to operator diagnostics.
- [x] `[must]` Define and test idempotency for repeated polling, progress,
  completion, failure, cancellation, and ready-event delivery.
- [x] `[must]` Persist enough state for Root restart without losing queued,
  assigned, terminal, or ready-event records.
- [x] `[should]` Add operator controls for pause queue, drain node, quarantine
  node, revoke credentials, retry on another node, and cancel superseded task.

### Phase 4. Isolated Dev Node Bootstrap

- [ ] `[must]` Define the container image contents: AdaOS hub, private
  developer skill, Codex runtime, git client, MCP client, and test tooling.
- [ ] `[must]` Implement device auth and Root-issued dev-node identity.
- [ ] `[must]` Install and start the private developer skill on boot.
- [ ] `[must]` Register the dev node with Root and enter
  `registered_waiting`.
- [ ] `[must]` Record dev-node image/runtime version, private skill version,
  Codex runner version, and supported test-tool versions during registration.
- [x] `[must]` Add a local dev-node simulator for repository tests and
  operator trials.
- [ ] `[deferred]` Add hardened isolation profiles such as seccomp/AppArmor,
  VM/firecracker-style execution, or host-level attestation after the MVP
  container path works.

### Phase 5. Task-Scoped Credentials And MCP

- [ ] `[must]` Issue task tokens separately from node identity.
- [ ] `[must]` Issue task-scoped MCP leases with read-mostly capability
  profiles.
- [ ] `[must]` Register and revoke forge credentials through Root or a forge
  adapter.
- [ ] `[must]` Keep MCP tokens and git private keys out of Codex prompt text.
- [ ] `[must]` Define credential lifecycle: issue, store, renew, expire,
  revoke, rotate, audit, and leak-response behavior for node identity, task
  token, MCP lease, forge credential, and Codex auth.
- [ ] `[must]` Keep task snapshots and mocks redacted, versioned, bounded, and
  free of raw user data and secrets.
- [ ] `[should]` Move from node-scoped deploy keys to task-scoped forge
  credentials after the first MVP path works.
- [ ] `[deferred]` Add DLP/secret scanning over prompts, logs, patches,
  commits, and result artifacts before broader rollout.

### Phase 6. Codex Runner Wrapper

- [ ] `[must]` Generate the Codex execution packet from the task assignment,
  Builder descriptors, SDK notes, allowed files, denied files, tests, and MCP
  tool docs.
- [ ] `[must]` Run Codex as a bounded subprocess or service inside the dev-node
  container.
- [ ] `[must]` Enforce allowed path changes before commit.
- [ ] `[must]` Run required tests and manifest/schema validation before
  reporting completion.
- [ ] `[must]` Enforce MVP threat-model controls in the runner: instruction
  separation, prompt-injection-aware context layout, restricted network,
  dependency review, timeouts, bounded artifacts, and no credentials in prompt
  text.
- [ ] `[must]` Fail the task before commit when generated code edits outside
  allowed paths, adds forbidden dependencies, bypasses required tests, or
  attempts disallowed runtime/user-subnet operations.
- [ ] `[must]` Produce cleanup evidence before the node can return to
  `waiting`.
- [ ] `[should]` Capture sanitized logs and concise repair evidence for failed
  runs.
- [ ] `[should]` Generate first provenance evidence: runner version, image
  digest, instruction packet hash, dependency changes, tool versions, and
  snapshot refs.
- [ ] `[deferred]` Add syscall/resource sandboxing for generated tests and
  stronger OS-level filesystem/network enforcement.

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
- [ ] `[must]` Define the concrete staging model: fetched task branch,
  temporary source tree or runtime slot, dev webspace preview, release record,
  approval identity, and rollback target.
- [ ] `[must]` Handle conflicts when the user workspace or source draft changed
  after the remote task started.
- [ ] `[must]` Enforce the realization policy matrix during staging so
  manual-only and disallowed changes cannot bypass User Hub approval.

### Phase 8. Failure Loop And Repair Tasks

- [ ] `[must]` Convert dev-node failure reports and User Hub validation
  failures into Builder repair context.
- [ ] `[must]` Deduplicate repeated failures and supersede stale tasks when the
  source draft changes.
- [ ] `[must]` Support cancellation by user and timeout expiry by Root.
- [ ] `[must]` Treat duplicate completion/failure reports, node crash after
  push, stale assignments, branch replay, and conflicting commit hashes as
  first-class failure/recovery cases.
- [ ] `[should]` Add golden task fixtures for success, test failure, forbidden
  file edit, MCP denial, cancelled task, and hub validation failure.
- [ ] `[should]` Add chaos fixtures for Root restart, dev-node crash, duplicate
  completion, token revoke mid-task, oversized logs, malicious prompt content,
  bad dependency, and cleanup failure.

### Phase 9. Hardening And Scale

- [ ] `[should]` Add node pools by capability profile, runtime stack, and cost
  class.
- [ ] `[should]` Add WebSocket/SSE assignment delivery after polling is proven.
- [ ] `[should]` Add task-scoped forge credentials and branch protection.
- [ ] `[should]` Add operator UI for queue depth, node health, task history,
  policy denials, and retry decisions.
- [ ] `[should]` Add metrics and SLOs for queue wait time, task duration,
  failure class distribution, cleanup failure rate, credential revoke latency,
  and User Hub validation outcomes.
- [ ] `[should]` Add per-user/subnet quotas, cost budgets, and placement policy
  before enabling broad remote realization.
- [ ] `[could]` Add parallel tasks per dev node after isolation, cleanup, and
  workspace separation are proven.

### Phase 10. Security, Privacy, And Supply Chain Hardening

- [ ] `[should]` Add dependency allowlists, lockfile enforcement, SBOM
  generation, vulnerability scanning, and package provenance checks.
- [ ] `[should]` Add signed commits or result attestations for task branches.
- [ ] `[should]` Add image digest enforcement, signed image attestations, and
  minimum private-developer-skill/runner version policy.
- [ ] `[should]` Add egress proxy, domain allowlists, per-domain audit, and
  replayable network evidence for tasks that need controlled external IO.
- [ ] `[should]` Add structured log scrubbers, artifact byte budgets, retention
  policy, and secret/DLP scanning for result evidence.
- [ ] `[deferred]` Add taint tracking, prompt-injection classifiers, and
  automated red-team evaluation across requirements, mocks, logs, and
  retrieved context.
- [ ] `[deferred]` Add stronger sandbox backends, host-level attestation, and
  periodic dev-node reimaging for production multi-tenant pools.

### Phase 11. Artifact Matrix And Productization

- [ ] `[must]` Implement acceptance gates for each target artifact class in the
  artifact acceptance matrix, not only generated skills.
- [ ] `[must]` Keep unsupported artifact classes disabled or manual-only until
  their acceptance gates and staging UX exist.
- [ ] `[should]` Add browser visual regression and staged hydration/readiness
  checks for generated UI descriptors.
- [ ] `[should]` Add connector contract tests, mocked provider fixtures,
  rate-limit evidence, and external sandbox-account strategy.
- [ ] `[should]` Add NLU/descriptor-fix corpus evaluation and rollback
  evidence.
- [ ] `[should]` Add model-backed skill checks for model manifest, checksum,
  dependency profile, and no implicit model downloads.
- [ ] `[deferred]` Add generated-artifact marketplace/public ecosystem review,
  compatibility matrix, and public-quality examples after internal staging is
  proven.

## Acceptance Criteria

The architecture is implemented when:

- Builder can submit a normalized `realize_request` for a skill, scenario, UI,
  datasource, or connector target.
- Root queues the task, assigns it to a registered isolated dev node, and
  tracks heartbeat, timeout, cancellation, and retries.
- The dev node receives only task-scoped forge and MCP access.
- Task snapshots and mock data are redacted, versioned, bounded, and carry
  provenance/freshness metadata.
- Codex runs with generated instruction files, allowed paths, denied paths,
  test commands, and tool bindings.
- The runner enforces MVP threat-model controls: instruction separation,
  restricted network, no credentials in prompt text, dependency review,
  bounded logs/artifacts, and cleanup evidence.
- The dev node commits results to a task branch and returns `result.json`.
- Result evidence includes tests, changed files, sanitized logs, and
  provenance for runner/image/instruction packet/dependency changes.
- Root sends a `dev_ready_event` to the User Hub.
- Repeated polling, progress, completion, failure, cancellation, ready-event,
  timeout, and retry transitions are idempotent or explicitly conflict-marked.
- The User Hub fetches the commit, validates it locally, shows the result to
  the user, and uses Pending Actions for final decisions.
- The User Hub stages the result in a temporary source tree, runtime slot, or
  dev webspace and records release/rollback evidence before activation.
- The realization policy matrix is enforced so manual-only and disallowed
  changes cannot bypass approval.
- Approved results enter the runtime only through normal prepare, test,
  activate, and rollback lifecycle rails.
- Failed results produce structured repair evidence without exposing secrets or
  real user data to the dev node.
- Each supported artifact class has explicit acceptance gates; unsupported
  classes remain disabled or manual-only.
