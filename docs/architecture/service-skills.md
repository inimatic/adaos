# Service Skills (managed processes)

Status: current runtime and lifecycle contract.

Last reviewed: 2026-08-20.

AdaOS supports **service skills**: skills that run as **external long-running processes** managed by the hub (instead of in-process Python handlers).

This is the main tool for integrating components with:
- incompatible Python/ABI requirements,
- heavy dependencies,
- their own HTTP servers (e.g. NLU engines).

---

## 1) What is a service skill?

A service skill is a normal skill folder (`skill.yaml`) with:

- `runtime.kind: service`
- `service.host`, `service.port`
- `service.command` (argv; hub prepends selected python where needed)

The hub discovers and manages these skills via:
- `src/adaos/services/skill/service_supervisor.py`

---

## 2) Service lifecycle

### Auto-start

During hub boot, AdaOS starts all discovered service skills:
- `src/adaos/services/bootstrap.py`

Also, when a skill gets (re)activated or rolled back, AdaOS restarts the service (if it is a service skill):
- `src/adaos/services/skill/service_supervisor_runtime.py`

### Health

For each service skill, the supervisor can perform HTTP health checks:
- `service.healthcheck.path` (default `/health`)
- `service.healthcheck.timeout_ms` (default `3000`)

### Distributed membership

A Project-deployed service may opt into core-managed distributed membership:

```yaml
service:
  membership:
    enabled: true
    group_id: media-library-home
    lease_seconds: 600
    protocol_version: '1'
    capabilities: [media.catalog]
    endpoints:
    - endpoint_id: catalog
      protocol: adaos.skill.v1
      address_ref: skill://{node_id}/{skill}/catalog
      scopes: [media.read]
      metadata: {}
```

The declaration contains service-facing identity only. Core selects the exact
active `ComponentActivation` on the trusted node and derives release, runtime
generation, topology generation, and a stable node/activation instance ID. A
member reports bounded health and pressure over its authenticated hub link; it
does not open or mutate a local topology authority store. `HubLinkManager`
binds the report to the transport-derived member identity, and the hub
supervisor registers or renews the instance in the subnet authority plane.
The hub expires stale leases every 30 seconds and exposes local membership
receipts as `distributed_membership` in service status. Registration remains
closed until the matching Project release, service definition, and group
exist.

The health response may expose bounded membership observations as
`distributed.health` and `distributed.pressure`; the service does not call the
distributed SDK or run a private heartbeat. An explicitly draining instance is
not revived. A still-active exact activation is re-registered after accidental
lease expiry, including across a core restart.

---

## 3) Isolation / environment

Service skills can run in an isolated venv:

- `runtime.env.mode: venv`
- optional `runtime.env.venv_dir`
- default runtime venv: `skills/.runtime/<skill>/v<major>.<minor>/venv`
- workspace-source fallback venv: `state/services/<skill>/venv`
- dependencies:
  - `skill.yaml: dependencies` (pip requirement strings)
  - optional `requirements.in` file inside the skill root

---

## 4) Self-managed services (issues + self-heal)

Service skills may opt-in to **self-management**:

```yaml
service:
  self_managed:
    enabled: true
    crash:
      max_in_window: 3
      window_s: 60
      cooloff_s: 30
    health:
      interval_s: 10
      failures_before_issue: 3
    hooks:
      on_issue: handlers.main:on_issue
      on_self_heal: handlers.main:on_self_heal
      timeout_s: 5.0
```

### Issue detector

The supervisor detects:
- crash-loop (many crashes within a time window) + cooloff
- repeated healthcheck failures

When an issue is recorded:
- it is persisted to `state/services/<skill>/issues.json`
- event is emitted: `skill.service.issue { skill, issue }`

### Doctor requests and reports

If `service.self_managed.doctor.enabled: true`, the supervisor emits:
- `skill.service.doctor.request` (with service status + log tail).

Doctor consumer is implemented as a skill (so it can evolve independently and later plug LLM logic):
- `.adaos/workspace/skills/service_doctor_skill`

It turns `doctor.request` into persisted reports:
- `state/services/<skill>/doctor_reports.json`
- event: `skill.service.doctor.report { skill, report }`

### Self-heal hooks

If enabled, the supervisor may call skill-provided hooks inside the service venv:
- `hooks.on_issue` is called when an issue is detected
- `hooks.on_self_heal` is called when the supervisor decides to attempt a self-heal

Entrypoints are `module:function` and are executed with `PYTHONPATH=<skill_root>` so `handlers.*` is importable.

---

## 5) API (service supervisor)

Hub API endpoints:

- `GET /api/services`
- `GET /api/services/{name}`
- `POST /api/services/{name}/start`
- `POST /api/services/{name}/stop`
- `POST /api/services/{name}/restart`

Self-management:

- `GET /api/services/{name}/issues`
- `POST /api/services/{name}/issue` (manual injection)
- `POST /api/services/{name}/self-heal`
- `GET /api/services/{name}/doctor/requests`
- `POST /api/services/{name}/doctor/request`
- `GET /api/services/{name}/doctor/reports`

---

## 6) Events (service supervisor)

### Service-to-runtime event capability

A persistent service has no in-process `AgentContext`. The supervisor issues a
rotating, per-service capability for a loopback-only event bridge and injects
its URL/token into the child process. `adaos.sdk.io` uses the bridge for the
fixed `io.out.*` output topics. `adaos.sdk.data.events.publish()` may also use
it, but only for exact topics declared by that skill under
`skill.yaml.events.publish`.

The bridge stamps `skill_name`, `owner=skill:<name>`, source authority and a
`service_bridge` marker. It rejects remote callers, stale tokens, undeclared
topics, payloads above 256 KiB and rates above 50 events/s. Skills must use the
SDK; the internal HTTP endpoint and token are not a product API.

The capability is output-only. Queue/control input remains an explicit service
API or a durable store owned by the skill; it is not smuggled through event
publication.

### Supervisor events

Emitted by the platform:

- `skill.service.started { skill, pid }`
- `skill.service.ready { skill, pid }`
- `skill.service.stopped { skill, pid }`
- `skill.service.crashed { skill, code }`
- `skill.service.issue { skill, issue }`
- `skill.service.doctor.request { id, ts, skill, reason, issue?, service, log_tail[] }`
- `skill.service.doctor.report { skill, report }`
