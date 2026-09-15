# Application Registry Projection

Status: target architecture and implementation roadmap.

Last reviewed: 2026-09-15.

This document defines the target AdaOS Application Registry Projection: a
private, rebuildable, SQLite-backed read model for Application catalog,
inventory, component ownership, release availability, permission declarations,
role matrices, validation state, and subnet-visible Application facts.

The projection exists to remove manifest scans and full YAML validation from
hot startup, reload, search, and access-description paths. It does not replace
Application, release, install, access, or skill-owned data authority.

Related owners:

- [Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md)
  owns Application identity, release, installation, channel, subscription, and
  Development Report semantics.
- [Application Access, Permissions, and Roles](application-access-permissions.md)
  owns permission profiles, Application roles, grants, effective authorization,
  and Builder final verification.
- [Personalization, Identity, and Access](personalization-identity-access.md)
  owns profiles, devices, sessions, memberships, local access facts, and audit.
- [Declarative Resource Workbench](declarative-resource-workbench.md) defines
  the broader pattern: shared relational indexes are query surfaces and
  ledgers, not automatic domain truth.
- [Distributed Service and Data Topology](distributed-service-and-data-topology.md)
  and [Subnet Knowledge Fabric Target Architecture](subnet-knowledge-fabric.md)
  define federation, freshness, and asynchronous observation boundaries.

## Problem

Current Application and Builder read paths still include compatibility scans
over many `project.yaml` files. A single name search can read, parse, and
validate every manifest in the development/project roots. With hundreds of
Applications, YAML parsing dominates latency.

The same pattern affects startup. Runtime startup currently prewarms Builder
project catalog state so later reads are faster, but this moves the manifest
scan into the readiness path. Reloading the API or replacing a runtime process
therefore pays work that is not needed to answer basic health, lifecycle, or
last-known catalog views.

Permissions and roles will make this pressure worse if declaration parsing,
role expansion, permission-profile digesting, and diff classification are
performed repeatedly on every request.

## Decision Summary

1. Core owns one Application Registry Projection service and storage contract.
2. The projection is a private SQLite read model under runtime state, not a
   skill data provider and not an object database.
3. Files, ApplicationStore records, release/package artifacts, access grants,
   and skill-owned stores remain authoritative.
4. Startup may serve from the projection snapshot only when the previous
   runtime epoch wrote a complete graceful seal receipt.
5. If the previous epoch did not shut down gracefully, the snapshot is not a
   trusted read source. The registry projection must be rebuilt or reconciled
   from authoritative sources before normal Application list/search reads are
   admitted.
6. `api serve` takeover must not infer snapshot trust from a successful
   `/api/admin/shutdown` response or from process exit. Only an epoch-bound
   projection seal written by the retiring runtime proves trust.
7. Background validation is part of the design. It keeps read paths fast while
   preserving fail-closed semantics for install, update, publish, permission
   elevation, and access enforcement.
8. Permission profiles and Application role declarations are indexed because
   they are release-bound declarative facts.
9. Effective authorization decisions are not owned by the registry projection.
   They depend on dynamic access grants, memberships, devices, sessions,
   approvals, revocations, resource scopes, and global deny constraints.
10. Federation exchanges signed, freshness-bound Application facts and
    availability facts, not raw development files or arbitrary local state.
11. Yjs remains a browser-facing SyncChannel and materialized-view surface. It
    must not become the private Application registry or access-policy store.
12. Any Yjs export from this projection must be browser-safe, bounded, and
    redacted before it enters `ui`, `data`, `registry`, or other synchronized
    webspace roots.
13. Every projection row is disposable: it can be rebuilt from authoritative
    sources plus accepted remote fact envelopes.

## Ownership Boundaries

The projection owns:

- fast Application list/search/filter reads;
- Application catalog headers and display metadata;
- installed, available, pinned, retired, update-available, and local-beta
  summary fields;
- component ownership and reverse component lookup;
- entrypoint and presentation lookup;
- release/channel digest pointers suitable for read-only display and planning
  preflight;
- permission-profile digest, flat compatibility permissions, structured
  declaration summaries, and role matrices;
- validation status and last-known-good diagnostics;
- federated catalog and availability facts after signature/trust admission;
- startup snapshot trust state and projection-rebuild operation records.

The projection does not own:

- skill business data;
- per-skill relational stores;
- raw secrets or connected-account tokens;
- immutable release/package artifacts;
- actual install/update/remove mutations;
- Workspace activation or data migration truth;
- access grants, memberships, sessions, device trust, revocations, or audit;
- Yjs room/session authority;
- raw public browser state.

## Projection Store

The target local store is:

```text
state/applications/registry.sqlite3
```

The database should use SQLite transactions, WAL during normal operation, and a
checkpoint during graceful seal. JSON columns are acceptable for versioned
payloads, but fields needed for filtering, joining, sorting, access preflight,
or invalidation must be indexed as scalar columns.

The exact schema may evolve, but the storage contract must include these
concepts.

| Table or concept | Purpose |
| --- | --- |
| `projection_epoch` | Runtime epoch, schema version, open/dirty/closed state, trusted seal receipt, startup and shutdown evidence. |
| `projection_source` | Source identity, path or store ref, source kind, mtime, size, content digest, schema digest, parse status, and last observed time. |
| `application_index` | Application display, identity, publisher, visibility, lifecycle, channel pointers, installed state, local beta state, search fields, and payload digest. |
| `application_component_index` | Application to component refs, package digests, component roles, lifecycle, exposure, and reverse ownership lookup. |
| `application_entrypoint_index` | Launch targets, presentation refs, default entrypoint, supported surfaces, and binding summaries. |
| `application_permission_profile_index` | Release-bound permission profile digest, required/optional permission ids, compatibility flat list, privacy labels, secret/provider declarations, and validation state. |
| `application_role_index` | Release-bound Application role ids, titles, assignability, default rules, sensitive flags, and expanded app capabilities. |
| `validation_report` | Validator version, status, errors, warnings, checked digest, checked time, next retry, and last-known-good binding. |
| `federated_application_fact` | Signed remote catalog, release, permission-profile, component, channel, and availability facts with origin, trust, observed time, and expiry. |
| `projection_journal` | Rebuild, reconcile, validation, import, invalidation, and seal operations with idempotency and diagnostic payloads. |

Text search may use SQLite FTS5 where available. If FTS5 is unavailable, the
service may fall back to indexed normalized tokens and bounded `LIKE` queries,
but it must not fall back to scanning and reparsing every manifest in the hot
path.

## Trusted Snapshot And Epoch Seal

A registry snapshot is trusted only when the previous runtime epoch explicitly
closed it.

On startup, the service must:

1. Read the latest `projection_epoch`.
2. Verify `state=closed`, `shutdown_kind=graceful`, `seal_status=complete`,
   compatible `schema_version`, compatible `validator_version`, and successful
   SQLite integrity evidence.
3. Verify the stored projection generation, source watermark, and projection
   digest if present.
4. Only then expose last-known Application list/search reads from the
   snapshot.
5. Create a new current epoch with `state=open` before serving normal runtime
   traffic, so a later crash cannot be mistaken for a graceful stop.

If the latest epoch is absent, open, dirty, sealing, abandoned, expired,
corrupt, schema-incompatible, or missing a complete seal receipt, the
projection is untrusted. The API may still start enough to expose health,
diagnostics, and a `registry_projection=warming` state, but generic
Application catalog/list/search reads must not use the old snapshot as trusted
truth. Exact administrative commands may read one authoritative source record
directly when their own contract already requires synchronous validation.

The graceful seal sequence is:

```text
enter drain
stop accepting new registry mutations and imports
finish or cancel projection jobs with bounded timeouts
commit all accepted projection/journal writes
run integrity/checkpoint evidence
write projection_epoch seal receipt atomically
mark epoch closed/graceful
allow process shutdown
```

The seal receipt should include:

- `runtime_instance_id`;
- `projection_epoch_id`;
- `shutdown_request_id` when shutdown was requested over HTTP;
- `shutdown_reason` and `shutdown_scope`;
- `schema_version`;
- `validator_version`;
- `projection_generation`;
- `projection_digest` or equivalent high-water mark;
- `source_watermark`;
- `started_at`, `sealed_at`, and `completed_at`;
- integrity result and checkpoint result;
- whether background validation was complete or which sources remained stale.

## API Serve Takeover

`api serve` and autostart replacement already try to stop an existing local
runtime before binding the same host and port. That graceful process retirement
must not weaken snapshot trust.

Rules:

- The new process never marks the old projection trusted.
- The old runtime is the only writer allowed to seal its epoch.
- A successful HTTP shutdown response means only that shutdown was accepted.
  It is not proof that registry projection state was flushed.
- A stopped process with no matching seal receipt is treated as ungraceful for
  registry projection purposes, even if the stop path attempted a graceful
  shutdown first.
- `lifecycle_scope=runtime_retire` is a valid process-retirement scope, but it
  still needs the same projection seal if the successor wants to trust the
  previous snapshot.
- If takeover falls back to process-tree termination, the next runtime must
  treat the projection as untrusted and rebuild before normal list/search
  reads.

To make this deterministic, the shutdown request should carry a
`shutdown_request_id`. The retiring runtime writes that id into the projection
seal receipt after the final checkpoint. The successor can then distinguish
`process stopped` from `projection sealed by the process I asked to retire`.

The `/api/admin/shutdown` response may later expose seal status, but the
critical invariant is independent of HTTP response shape: trust follows the
durable seal receipt, not the request response.

## Startup And Reload Flow

The target startup sequence is:

1. Initialize core context and open the registry projection service.
2. Evaluate the previous epoch trust state.
3. If trusted, publish fast last-known Application catalog/inventory state
   from SQLite.
4. Mark current epoch open.
5. Start API, health, lifecycle, and diagnostics without waiting for full
   manifest parsing.
6. Start background reconcile and validation.
7. Promote `registry_projection` from `warming` to `ready` after source
   deltas and required validation complete.

If the snapshot is untrusted:

1. Mark `registry_projection=untrusted_rebuild`.
2. Do not serve generic Application list/search from the old snapshot.
3. Rebuild from authoritative sources.
4. Publish ready only after a valid projection generation is committed.
5. Preserve the old database file or rows as diagnostic evidence if useful,
   but do not mix them into the trusted read model without validation.

This removes the need to prewarm the Builder project catalog on the startup
critical path. Catalog and search consumers should read the projection service;
they should not independently glob and parse every `project.yaml` except in a
controlled rebuild operation.

## Reconcile And Background Validation

Background reconcile is responsible for making the projection current without
blocking ordinary startup.

Priorities:

1. Installed and active Applications.
2. Runtime-selected releases and entrypoints.
3. Permission profiles for installed or update-available releases.
4. Channel pointers and release availability.
5. Development Applications visible in Builder.
6. Remote federated facts.
7. Cold catalog rows and full text refresh.

Each source has a validation state:

```text
unknown | queued | validating | valid | stale | invalid | quarantined
```

Validation results are durable. A changed source must not silently inherit a
previous `valid` result unless the validated digest still matches.

Read behavior:

- `valid` rows can be used normally.
- `stale` rows can be shown in read-only UI with freshness warnings.
- `unknown` rows can be hidden, shown as pending, or read synchronously by
  exact-id commands depending on caller contract.
- `invalid` rows fail closed for install/update/publish/access elevation.
- `quarantined` rows require explicit repair or operator action.

Background validation may keep last-known-good display rows available for
human orientation, but strict mutations and permission expansion must bind to
the currently validated digest.

## Permissions, Roles, And Access Caching

The registry projection should index declarative Application access inputs.
These are stable for a release digest and cheap to reuse:

- structured permission profile;
- `permission_profile_digest`;
- required and optional permissions;
- flat compatibility `permissions`;
- Application role declarations;
- role to Application capability matrix;
- permission and role diff classification;
- data-practice, secret, provider, notification, background-action, and LLM
  use summaries;
- Builder final verification summary and release-readiness state.

This answers the frequent question, "what does this Application declare and
which roles can cover this capability?" without reparsing manifests or release
payloads.

Effective authorization remains outside the registry projection. A runtime
decision is allowed only after the dynamic access layer also admits:

- subject grant or default access profile;
- actor membership and platform capability;
- current Application role assignment;
- resource, webspace, workspace, or external audience scope;
- device/session trust;
- child, guest, tenant, and global deny constraints;
- approval or Pending Action state;
- revocation and expiry freshness.

The recommended cache boundary is compiled inputs, not blind decision truth:

```text
compiled_permission_profile = application release declaration
compiled_role_matrix = role -> app capability expansion
effective_grant_set = access facts for subject/scope with revision
```

A small in-memory or SQLite decision cache may be added only when keyed by all
freshness dependencies:

```text
subject_ref
actor_ref
application_id
release_digest
permission_id or action id
resource/webspace/workspace scope
permission_profile_digest
grant_revision
role_assignment_revision
membership_revision
session_revision
deny_policy_revision
approval_revision
```

Any revision change invalidates the decision. Short TTLs may be used as a
secondary guard, not as the correctness mechanism. Audit is never skipped:
every allow, deny, approval, revoke, and update-blocked decision still records
the actor chain and reason required by the Application Access architecture.

## Federation

Federation should exchange facts admitted into the projection, not source
trees.

Initial fact families:

- `ApplicationCatalogFact`: publisher, Application id, display metadata,
  visibility, compatibility, entrypoints, and public readiness summaries.
- `ApplicationReleaseFact`: immutable release digest, channel, package set
  digest, provenance digest, and signing evidence.
- `PermissionProfileFact`: release-bound permission profile digest, required
  permissions, optional permissions, role ids, role-impact summary, and
  disclosure labels.
- `ApplicationComponentFact`: component refs, package digests, lifecycle, and
  exposure.
- `ApplicationAvailabilityFact`: origin subnet/node, installed release,
  runtime selection, health, freshness, and capability summary.
- `ApplicationChannelFact`: stable or prerelease pointer, publisher authority,
  revision, observed time, and expiry.

Remote facts must include origin, authority, signature or equivalent trust
binding, observed time, expiry, and replay protection. Expired or unverifiable
facts may remain as diagnostics but must not drive install, update, or access
decisions.

Development source is not federated by default. Cross-subnet development uses
explicit Development Reports, Builder packets, or package/release artifacts
with their own policy and redaction.

## Yjs Boundary

Yjs is valuable as a SyncChannel and browser-facing materialized-view surface,
but it is not the Application Registry Projection store.

Allowed use:

- publish a bounded, redacted catalog summary for the current browser/session;
- publish installed badges, validation progress, stale indicators, and
  safe-to-display release summaries;
- publish operation progress already approved for browser visibility;
- carry UI state derived from the private projection.

Disallowed use:

- store registry truth only in Yjs;
- store raw manifests, package internals, local filesystem paths, secrets,
  tokens, access grants, actor chains, or private validation evidence in
  browser-synchronized roots;
- rely on CRDT deletion to remove leaked private facts;
- let member-provided Yjs updates become Application facts without typed
  admission, trust, and redaction.

If a future private Yjs namespace is introduced, it must be a separate
security boundary with explicit authentication, no default browser IndexedDB
persistence, no default subnet broadcast, redaction tests, and diagnostics that
distinguish persistence authority from browser SyncChannel authority. That is
not part of this medium step.

## Observability

The projection service must expose:

- `trusted_snapshot`: yes/no and reason;
- `projection_epoch_id` and `runtime_instance_id`;
- `projection_status`: `ready`, `warming`, `untrusted_rebuild`, `invalid`,
  `degraded`, or `quarantined`;
- source counts by validation status;
- manifest parse and validation timings;
- SQLite open, query, checkpoint, and integrity timings;
- background queue depth and oldest pending source age;
- last seal receipt and last ungraceful epoch;
- number of Application reads served from projection versus authoritative
  fallback;
- stale rows shown to UI;
- access declaration cache hit/miss and policy-cache invalidation counts.

Diagnostics must separate:

- local projection readiness;
- authoritative source availability;
- remote fact freshness;
- access-policy freshness;
- browser-facing Yjs materialization state.

## Roadmap

Priority tags:

- `[must]`: blocks the first safe fast-start Application projection;
- `[should]`: needed before broad repeated use or subnet federation;
- `[could]`: useful improvement that does not block the first proof;
- `[deferred]`: deliberately postponed until a later federation, marketplace,
  or private-sync milestone.

### APREG0. Contract And Ownership

**Outcome:** architecture, owners, and non-goals are explicit.

- [x] `[must]` `APREG0-01` Publish this target architecture and roadmap.
- [ ] `[must]` `APREG0-02` Record implementation issues for projection schema,
  startup integration, shutdown seal, background validation, permission
  indexes, and Yjs redaction tests.
- [ ] `[must]` `APREG0-03` Route existing Application catalog/list/search
  hot-path scans to this projection owner.

**Exit proof:** planning pages route Application registry projection and
startup snapshot decisions here.

### APREG1. SQLite Projection Kernel

**Outcome:** a private SQLite read model can answer Application catalog and
inventory queries without scanning manifests.

- [ ] `[must]` `APREG1-01` Add `state/applications/registry.sqlite3` schema
  with epoch, source, Application, component, entrypoint, validation, and
  journal tables.
- [ ] `[must]` `APREG1-02` Add deterministic projection row digests and source
  watermarks.
- [ ] `[must]` `APREG1-03` Add query APIs for list/search/detail preflight,
  component reverse lookup, installed summaries, and release/channel pointers.
- [ ] `[must]` `APREG1-04` Add rebuild command and service operation with
  bounded progress, cancellation, and journal records.
- [ ] `[should]` `APREG1-05` Add FTS5-backed text search with deterministic
  fallback when FTS5 is unavailable.

**Exit proof:** query tests prove no per-request `project.yaml` scan for common
Application list/search/component-owner reads.

### APREG2. Trusted Startup Snapshot

**Outcome:** startup can use a previous projection only after graceful seal.

- [ ] `[must]` `APREG2-01` Write `projection_epoch` open/dirty state before
  normal runtime traffic.
- [ ] `[must]` `APREG2-02` Implement graceful seal: drain, stop projection
  mutations, flush, checkpoint, integrity evidence, and atomic closed receipt.
- [ ] `[must]` `APREG2-03` Reject snapshot trust after absent, open, dirty,
  corrupt, schema-incompatible, or unsealed previous epochs.
- [ ] `[must]` `APREG2-04` Surface startup status as
  `ready|warming|untrusted_rebuild|invalid|degraded`.
- [ ] `[should]` `APREG2-05` Preserve untrusted/corrupt snapshot evidence for
  diagnostics without serving it as trusted catalog state.

**Exit proof:** forced-kill and normal-shutdown tests prove only the graceful
sealed path serves catalog/search from the prior snapshot.

### APREG3. API Serve And Runtime Retirement

**Outcome:** process replacement does not accidentally certify projection
trust.

- [ ] `[must]` `APREG3-01` Add `shutdown_request_id` to runtime shutdown flow or
  an equivalent deterministic retirement token.
- [ ] `[must]` `APREG3-02` Bind the projection seal receipt to the retiring
  runtime epoch and shutdown request id.
- [ ] `[must]` `APREG3-03` Treat successful `/api/admin/shutdown` plus process
  exit as insufficient for snapshot trust when the seal receipt is absent.
- [ ] `[must]` `APREG3-04` Ensure fallback process termination always leaves
  the next runtime in `untrusted_rebuild`.
- [ ] `[should]` `APREG3-05` Expose seal status in shutdown diagnostics without
  making the HTTP response the source of trust.

**Exit proof:** takeover tests cover graceful retire with seal, graceful request
without seal, forced fallback, pidfile-only stale process, and supervisor-owned
handoff.

### APREG4. Background Reconcile And Validation

**Outcome:** changed sources are parsed and validated off the hot path.

- [ ] `[must]` `APREG4-01` Add source scanner that records source identity,
  digest, mtime, size, and schema version without full validation when
  unchanged.
- [ ] `[must]` `APREG4-02` Parse and validate only changed sources, prioritizing
  active/installed Applications.
- [ ] `[must]` `APREG4-03` Persist `validation_report` rows and last-known-good
  bindings per digest.
- [ ] `[must]` `APREG4-04` Fail closed for invalid permission, install, update,
  publish, and access-elevation inputs.
- [ ] `[should]` `APREG4-05` Add incremental filesystem/eventbus invalidation
  so rebuilds do not depend only on periodic scans.

**Exit proof:** tests cover unchanged manifest fast path, changed digest
validation, invalid manifest quarantine, stale display, and strict mutation
denial.

### APREG5. Permission And Role Declaration Index

**Outcome:** access declaration reads are cheap and release-bound.

- [ ] `[must]` `APREG5-01` Index `permission_profile_digest`, required and
  optional permissions, flat compatibility permissions, and declaration
  validation status.
- [ ] `[must]` `APREG5-02` Index Application role declarations and expanded role
  capability matrices.
- [ ] `[must]` `APREG5-03` Feed Application Access install/update review and
  Builder permission profiler from the projection instead of reparsing release
  declarations.
- [ ] `[must]` `APREG5-04` Keep effective authorization in the access/policy
  layer with revision-keyed cache invalidation and full audit.
- [ ] `[should]` `APREG5-05` Add decision-input cache diagnostics and
  invalidation counters for grant, role, membership, session, deny, approval,
  and release changes.

**Exit proof:** permission/role profile tests prove digest stability, role
matrix expansion, update diff classification, cache invalidation, and no audit
loss.

### APREG6. Federation Facts

**Outcome:** remote Application catalog and availability can be observed
without federating raw source.

- [ ] `[must]` `APREG6-01` Define signed Application catalog, release,
  permission-profile, component, channel, and availability fact envelopes.
- [ ] `[must]` `APREG6-02` Import admitted remote facts into projection rows
  with origin, trust, observed time, expiry, and replay protection.
- [ ] `[must]` `APREG6-03` Keep remote facts read-only until a separate install,
  update, or access operation admits them through its own authority boundary.
- [ ] `[should]` `APREG6-04` Add subnet freshness diagnostics and stale remote
  fact behavior.
- [ ] `[could]` `APREG6-05` Add compact delta exchange after the full fact
  envelope is proven.

**Exit proof:** federation tests prove expired, unsigned, replayed, and
schema-incompatible facts do not drive install/update/access decisions.

### APREG7. Browser-Safe Projection Export

**Outcome:** Applications UI can render quickly without exposing private
registry or access facts through Yjs.

- [ ] `[must]` `APREG7-01` Define browser-safe Application catalog and
  installed summary shapes derived from the private projection.
- [ ] `[must]` `APREG7-02` Add redaction tests for manifests, local paths,
  secrets, grants, actor chains, validation evidence, and private package
  internals.
- [ ] `[must]` `APREG7-03` Publish only bounded summaries into Yjs or WebIO
  surfaces.
- [ ] `[should]` `APREG7-04` Prefer stream/snapshot routes for large catalog
  result sets and keep Yjs for compact first-paint state.
- [ ] `[deferred]` `APREG7-05` Private Yjs namespaces for registry internals.

**Exit proof:** browser tests and serialized Yjs snapshot inspections prove
only approved summary fields reach synchronized browser state.

### APREG8. Observability And Operations

**Outcome:** operators and Builder can understand projection freshness without
opening SQLite manually.

- [ ] `[must]` `APREG8-01` Add API/CLI diagnostics for trust state, epoch, seal
  receipt, rebuild operation, validation counts, stale rows, and query source.
- [ ] `[must]` `APREG8-02` Add timing metrics for SQLite open/query/checkpoint,
  manifest parse/validation, background queues, and startup readiness.
- [ ] `[should]` `APREG8-03` Add repair commands for rebuild, validate source,
  quarantine clear, and trusted snapshot discard.
- [ ] `[could]` `APREG8-04` Add performance gates using hundreds of manifest
  fixtures and cold-process startup measurements.

**Exit proof:** diagnostics identify whether latency comes from startup trust,
rebuild, validation, SQLite query, access-policy cache miss, or browser
projection export.

## Completion Definition

The Application Registry Projection medium step is complete when:

- normal graceful restart serves Application catalog/search from a sealed
  SQLite projection without reparsing all manifests;
- ungraceful termination, forced takeover, corrupt snapshot, and schema change
  all trigger untrusted rebuild instead of trusted stale reads;
- Builder catalog, Applications catalog, component ownership lookup, and
  permission-profile summary use the projection service;
- background validation updates changed sources and fails strict operations
  closed on invalid input;
- permission and role declaration indexes feed Application Access without
  replacing dynamic access-policy freshness or audit;
- federated Application facts are admitted as signed, freshness-bound
  projection inputs;
- Yjs carries only browser-safe, redacted, bounded summaries derived from the
  projection.
