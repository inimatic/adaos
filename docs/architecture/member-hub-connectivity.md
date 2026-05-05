# Member-Hub Connectivity

## Goal

Define the target control-plane architecture for how a `member` node joins a subnet,
establishes a durable upstream connection to its `hub`, survives runtime restarts,
and eventually hands realtime continuity to `adaos-realtime`.

Read this together with:

- [AdaOS Supervisor](adaos-supervisor.md)
- [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md)
- [Transport Ownership](transport-ownership.md)
- [Member node onboarding (phase 1)](../onboarding/member-node-phase1.md)

## Why this exists

The current `adaos node join` flow persists the membership contract:

- `role=member`
- `subnet_id`
- `hub_url`
- member join/session token

but it does not yet guarantee that the running node process immediately activates
the `member -> hub` link after the contract is saved.

Today, the live connection is started later by runtime boot. That leaves a gap between:

- "this node is configured as a member"
- "this node is actively present in the subnet"

For production nodes, that gap should be closed by the supervisor. For development
nodes that run without supervisor, the runtime should still activate the link
directly after a successful join.

## Current implementation

Today the system is split like this:

- `adaos node join`
  - resolves join-code against Root or direct hub join endpoint
  - persists `role`, `subnet_id`, `hub_url`
  - persists member upstream session token in runtime state
- runtime boot
  - starts member register + heartbeat loop
  - starts member subnet link client
- hub runtime
  - receives member link events
  - rebuilds member snapshot projection into shared desktop/webspace state

This works once the runtime is restarted or booted as `member`, but it is weaker than
the target lifecycle because the connectivity contract is not yet owned by a stable
always-on authority.

As of the current implementation increment:

- `adaos node join` also performs a best-effort local activation request
- runtime exposes `request_member_hub_reconnect()`
- member connectivity status carries semantic transition states for restart/update windows
- supervisor now has an initial `member-hub` watchdog that can request reconnects for production-style nodes
- reliability/CLI surfaces now expose the active `required_upstream_link` view for the current node role
- supervisor status now also publishes a unified `required_upstream_link` contract over role-specific watchdog internals
- reliability/CLI now enrich that contract with sidecar handoff readiness and restart recovery policy

This closes the biggest developer-experience gap, but it is still not the final
production model because sidecar-aware ownership and deeper recovery policy are not implemented yet.

## Target architecture

### 1. Role-scoped upstream link

Every node has exactly one required upstream control link based on its role:

- `hub` owns `hub -> root`
- `member` owns `member -> hub`

Supervisor should reason about those as one generic concept:

- `required_upstream_link`
- `desired_state`
- `owner`
- `health`
- `transition_state`

This keeps the control plane symmetric and avoids having one special watchdog for
 hubs and ad-hoc reconnect logic for members.

### 2. Membership contract vs transport session

The architecture must separate:

- membership contract
  - durable desired state
  - `role=member`
  - `subnet_id`
  - `hub_url`
  - `member_hub_token`
- transport session
  - current websocket / relay / p2p live connection
  - reconnect generation
  - health / backoff state
- transport owner
  - `runtime`
  - `supervisor-managed runtime`
  - later `sidecar`

The contract survives restarts and slot switches. The transport session is disposable.

### 3. Semantic transition states

The upstream link state machine must distinguish failure from expected transition:

- `ready`
- `degraded`
- `reconnecting`
- `waiting_restart`
- `restarting`
- `paused_for_update`
- `disabled`

Important rule:

- if the node is in `waiting_restart`, `restarting`, or `paused_for_update`,
  the watchdog must not try to heal the link
- the node should instead publish an explicit transitional status such as
  `member_link=paused_for_restart`

This matters because hub and member updates are allowed to happen independently.
Temporary absence during a declared restart should not be treated as transport failure.

### 4. Production vs development ownership

#### Production / supervisor enabled

Supervisor owns the policy:

- desired membership state
- whether the node should currently be connected
- whether reconnects are allowed in the current transition phase
- whether the link should be paused because the node is preparing to restart
- whether recovery should be reconnect, session reset, runtime restart, or later sidecar handoff

Runtime owns execution:

- establish member-hub session now
- stop member-hub session now
- report health and diagnostics

#### Development / direct `adaos api serve`

Runtime can use a lighter path:

- `adaos node join` saves the contract
- runtime immediately attempts local member-hub activation
- no heavy always-on watchdog is required

The target ergonomics are:

- developer joins a subnet
- the node appears in hub `members` without manual restart

### 5. Sidecar evolution

The target long-lived continuity boundary is:

- `adaos-supervisor`
  - lifecycle authority
- `adaos-runtime`
  - local API, skills, scenarios, data handling
- `adaos-realtime`
  - eventual owner of long-lived member/hub realtime continuity

For members, sidecar is not required in phase 1 of this architecture.
But the contract should already be prepared for it:

- desired upstream link state should not depend on runtime PID identity
- transport ownership should be explicit
- slot switch should not require rewriting business semantics

Target later capability:

- sidecar can keep selected member-hub realtime channels alive across runtime slot change

## Skill boundary: AdaOS Connect

`AdaOS Connect` should remain a user-facing orchestration skill, not the owner of
transport truth.

The skill is responsible for:

- showing join options
- creating join sessions
- presenting short codes / QR flows
- showing approval / pending / expired state
- surfacing reconnect / retry affordances

The skill is not responsible for:

- directly mutating `node.yaml`
- directly writing role/subnet identity outside SDK/core APIs
- owning long-lived member-hub heartbeat loops
- deciding whether restart-time recovery is allowed

In other words:

- core owns connectivity truth
- the skill owns the user workflow and presentation

## Join flows

### Join by code

Target behavior:

1. operator runs `adaos node join --root ... --code ...`
2. command stores the durable membership contract
3. command requests immediate local activation
4. if supervisor is enabled, supervisor takes over persistent lifecycle ownership
5. if supervisor is not enabled, runtime performs best-effort direct activation

### Join by QR

Target behavior:

1. member node opens `AdaOS Connect -> Join node`
2. local runtime creates a short-lived Root join session
3. browser shows QR that contains a Root session URL or session id, not the final member token
4. owner scans QR from a trusted device with Root access
5. Root marks the session as approved and stores the membership payload
6. member node polls Root for join-session completion
7. member stores the membership contract and activates member-hub connectivity

Important constraint:

- member does not require a direct `member -> root` websocket for this flow
- polling Root for join-session completion is the baseline design
- future root push can be added later as an optimization, not a dependency

## Target kernel responsibilities

Core services should provide explicit APIs for:

- create membership contract
- load membership contract
- request member-hub reconnect
- request member-hub disconnect
- publish member-hub connectivity status
- publish semantic transition state
- suppress healing while restart/update transition is in progress

The implementation should avoid:

- storing live transport state in `node.yaml`
- embedding transport policy inside skills
- requiring browser-only actions to make a production node stable

## Roadmap

### Phase 1 - Self-activating member join

- extend `adaos node join` to request immediate activation after saving membership contract
- add a runtime-level `request_member_hub_reconnect()` path
- in dev/no-supervisor mode, activate directly in the running runtime
- keep the current Root join-code contract unchanged
- when the member link comes up, have the hub request an immediate runtime snapshot and
  keep one bounded follow-up refresh if the first payload does not yet carry desktop
  catalog material for remote desktop/YJS projection seeding

Success criteria:

- after successful `adaos node join`, a dev node appears on the hub without manual restart
- `members=1` becomes observable after join on a healthy path

### Phase 2 - Supervisor member-hub watchdog

- add a member-side watchdog symmetric to the existing hub-root watchdog
- teach supervisor about `required_upstream_link` for the current role
- persist member-hub watchdog state and cooldown/recovery data
- expose member-hub watchdog diagnostics through CLI, reliability, and browser-safe status

Success criteria:

- production member nodes automatically recover member-hub link loss
- the logic is owned by supervisor, not by the one-shot join command

### Phase 3 - Semantic restart/update coordination

- add `waiting_restart`, `restarting`, and `paused_for_update` connectivity states
- suppress watchdog healing during expected transition windows
- publish those states to hub and browser surfaces
- treat update-time absence as planned transition instead of plain failure

Success criteria:

- member updates do not look like unexplained link flapping
- hub/operator surfaces can distinguish degraded transport from planned restart

### Phase 4 - QR join session flow

- add Root-backed join session creation
- add skill/UI path in `AdaOS Connect`
- show QR that points to Root session approval flow
- add member polling for approval completion
- activate connectivity immediately after approval payload is claimed

Success criteria:

- a fresh local node can join a subnet from the browser without typing the code manually
- the QR does not embed long-lived member transport credentials directly

### Phase 5 - Sidecar-aware continuity contract

- make member-hub transport owner explicit in runtime/supervisor status
- prepare handoff hooks for sidecar-managed member realtime continuity
- keep runtime slot switching compatible with future sidecar ownership

Success criteria:

- connectivity lifecycle no longer assumes that the runtime process must always own the live member-hub session
- future sidecar work can reuse the same membership contract and watchdog model

## Non-goals for this roadmap

This roadmap does not yet require:

- full member-root websocket control plane
- full sidecar ownership of all member channels
- moving skill/business semantics into the sidecar
- replacing the current Root join-code model

Those can be layered later without changing the core split between:

- durable membership contract
- live transport session
- transport lifecycle owner
