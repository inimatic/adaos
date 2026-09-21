# CBS0 Capability, Binding, and State Compatibility Inventory

Status: validated-local architecture inventory for `CBS0`.

Last reviewed: 2026-09-21.

This document is the implementation handoff from
[Capability, Binding, and State Separation](capability-binding-state-separation.md)
to `CBS1` through `CBS5`. Its machine-readable source is
[capability-binding-state-cbs0-inventory.json](capability-binding-state-cbs0-inventory.json).

The inventory describes tracked Core source at
`bf0d21a6714f5ef810ec027f9aeb1db96c6b8d2f`. It performs no runtime migration,
state allocation, package activation, or authority change.

## Decisions

1. The reference fixture is `flowboard_skill.work_items` from the existing
   local Resource Workbench tests.
2. `skill.yaml:capabilities` remains requested SDK/runtime authority. It is not
   a provided semantic capability declaration.
3. No current runtime record is renamed into a new architectural identity.
   Existing records become inputs to explicit projections in later milestones.
4. `WorkspaceLock` remains the sole active-workspace authority.
5. New records reuse the current canonical JSON/SHA-256 convention, with
   schema-first validation and finite-JSON enforcement added by CBS1.
6. Web UI `state_ref` is never used for persistent state identity.
7. Simulation and production receive different `state_space_ref` values.

## Selected CRUD Fixture

The selected fixture is the `flowboard_skill` declaration built in
`tests/test_local_resource_workbench.py`. It is preferable to the current Dev
Ticket and Notebook candidates for the first architectural proof:

- it already uses `adaos.resource.local_crud.v1` and
  `adaos.resource.definition.v1`;
- it exercises list, show, create, update, and delete;
- its records use optimistic revisions;
- it validates owner, resource namespace, declaration paths, schemas, roles,
  and incompatible refreshes;
- it can be assembled as an actual temporary skill package, which makes
  Package A to Package B relocation testable;
- it carries no production user data and no domain lifecycle that would hide
  the architecture behind Dev Ticket or Notebook behavior.

### Current production materialization

The current production-shaped fixture is not SQLite-backed. This distinction
is important.

```text
LocalCrudResourceService
  state: <state_dir>/resources/local/registry.json
  lock:  <state_dir>/resources/local/.local-resources.lock
  write: whole-registry atomic JSON replacement
```

The current identity is `resource_type`, scoped inside one Core registry. The
owner and authority binding are both derived from the skill name:

```text
resource_type       skill.flowboard_skill.work_items
owner_ref           skill:flowboard_skill
authority.provider  local_crud
authority.binding   flowboard_skill
source_of_truth     local_skill_state
```

Declaration refresh preserves records. An incompatible schema is rejected as
requiring migration, but the service has no migration executor. Removing the
declaration or package does not detach or destroy the retained registry entry.

### Current simulation materialization

`PrototypeResourceService` is the simulation-side seam. It stores Preview
state through `ResourceStorage` in SQLite:

```text
<state_dir>/resources/prototypes/resources.sqlite3
```

It is disposable and is bound to project, change, semantic UI revision, and
prototype bundle digests. CBS1/CBS2 will project the same semantic record
contract through a simulation binding while retaining a distinct disposable
simulation `StateSpace`.

### Target projection

```text
requirement:flowboard/manage-work-items
  -> capability:resource.records.manage
     -> state-contract:flowboard.work-items

simulation:
  binding-definition:resource.records.prototype
  state-space:preview/<change-id>/flowboard/work-items

production:
  binding-definition:resource.records.local-json
  state-space:workspace/flowboard/work-items
```

The first proof does not claim that the current JSON registry is the final
storage provider. It proves semantic, package, binding, and state identity
boundaries around a mechanism that already works.

## Existing Uses Of “Capability”

The repository currently uses “capability” for several unrelated concepts.
They remain valid in their own domains but cannot share semantic identity.

| Current use | Classification | Meaning | Separation rule |
| --- | --- | --- | --- |
| `skill.yaml:capabilities` | requested SDK authority | Runtime/SDK operations requested by a skill | Never interpreted as a provided `CapabilityContract` |
| Application roles and component capability evidence | actor authorization | Whether an actor/Application may perform an operation | Authorization remains a separate decision after resolution |
| Resource `required_capabilities` | actor authorization | Permission needed for a field or resource operation | Does not prove semantic implementation |
| UI capability catalog and gaps | UI affordance / planning diagnostic | Client components, recipes, and unsupported UI behavior | Remains compiler/presentation vocabulary |
| `RelationalProviderCapabilities` | provider feature | Durability, transactions, isolation, backup, and related guarantees | Contributes to effective state guarantees only |
| node/endpoint capabilities | placement feature | Placement and environment eligibility | Constrains environment, not Application meaning |
| distributed `provided_contracts` and `required_capabilities` | runtime service advertisement | Runtime topology/service compatibility | May be referenced by a binding, never aliased to it |

The machine-readable inventory records authoritative source paths and examples
for each category. CBS1 must introduce a new `capability:` namespace rather
than infer contracts from any current list.

## Existing State And Binding Surfaces

### Local CRUD registry

- Identity: `resource_type`.
- Owner: `owner_ref`, constrained to `skill:<skill-name>`.
- Physical location: Core local resource registry JSON.
- Schema: inline record schema plus definition/declaration digests.
- Migration: reject incompatible existing records.
- Gap: no independent state identity, revision chain, lifecycle authority, or
  fencing epoch.

### Prototype resources

- Identity: prototype resource type plus project/change/UI revision facts.
- Owner: project reference.
- Physical location: SQLite `ResourceStorage`.
- Lifecycle: disposable Preview materialization.
- Target: simulation `BindingInstance` and separate disposable `StateSpace`.

### Skill data lifecycle

`adaos.skill.data_lifecycle.v1` already owns package-declared database paths and
forward migration chains. `ProjectRelease` derives exact migration locks, and
`LocalApplicationDataLifecycle` owns Beta/Stable transition and recovery.

The Flowboard fixture does not use this contract because its registry is
Core-owned. Its future `StateContract` records that fact explicitly; it does
not invent a fake `data_lifecycle` declaration.

### Relational storage binding

`RelationalStorageBinding` already separates provider ID, owner, logical name,
opaque locator, secret reference, migration owner, requirements, and negotiated
capabilities. It is an input to future `BindingInstance` and effective
guarantees. It is not itself a `StateSpace`.

### Package schema and migration locks

`ArtifactPackageRef` and `ProjectRelease` already pin schema and migration
content by digest. `StateContract` will reference these locks where applicable,
not duplicate their content.

## Package And Authority Map

| Current record | Existing authority | Target relationship |
| --- | --- | --- |
| `ArtifactPackageRef` | Immutable package identity and materialization path | Package delivery facts |
| `ProjectRelease` | Exact release and dependency-locked package set | Package resolver output |
| `ProjectCompositionLock` | Exact members, roles, dependencies, entry points, lifecycle, and access declarations | Remains package-composition authority |
| `adaos.artifact.activation_plan.v1` | Read-only package transition bound to observed lock | Execution substrate below `ResolutionPlan` |
| activation operation / lock history | Durable phase, rollback, and recovery journal | Executes one exact `ResolutionPlan` later |
| `WorkspaceLock` | Active packages and dependency bindings | Remains the only active-workspace authority |

There is no current `ApplicationResolution`. A package `ReleasePlan` and
artifact activation plan are lower-level inputs, not aliases for semantic
admission or state transition intent.

## Target Mapping

| Target concept | Current status | CBS action |
| --- | --- | --- |
| `ApplicationRequirement` | missing | CBS3 adds implementation-free requirements |
| `CapabilityContract` | missing | CBS1 creates a new semantic namespace |
| `StateContract` | partial facts | CBS1 composes resource schemas, locks, lifecycle, and guarantees |
| `BindingDefinition` | partial/coupled | CBS1 adds logical package-independent definition and delivery mapping |
| `PackageRelease` | existing authority | Reuse package/release records |
| `BindingInstance` | partial local bindings | CBS2 adds stable refs and immutable revisions |
| `StateSpace` | missing | CBS2 adds independent local identity |
| `EvidenceClaim` | partial evidence/attestations | CBS1 adds exact subjects, environment, dependencies, and freshness |
| `ApplicationResolution` | missing | CBS3 admits after semantic, package, and evidence stages |
| `ResolutionPlan` | package-only partial | CBS4 adds semantic/local state transition intent |
| `WorkspaceLock` | existing authority | Extend or digest-link; never replace |
| `SemanticGraph` | absent derived view | CBS8 only |

## Reserved Namespaces

These names are reserved by CBS0 but are not claimed as implemented schemas.

| Record | Schema |
| --- | --- |
| Application requirement | `adaos.application.requirement.v1` |
| Environment profile | `adaos.environment.profile.v1` |
| Capability contract | `adaos.capability.contract.v1` |
| State contract | `adaos.state.contract.v1` |
| Binding definition | `adaos.binding.definition.v1` |
| Package delivery mapping | `adaos.binding.delivery.v1` |
| Evidence claim | `adaos.evidence.claim.v1` |
| Binding instance revision | `adaos.binding.instance.v1` |
| State-space revision | `adaos.state.space.v1` |
| Application resolution | `adaos.application.resolution.v1` |
| Resolution plan | `adaos.resolution.plan.v1` |
| Evidence assessment | `adaos.evidence.assessment.v1` |

Reserved reference prefixes:

```text
requirement:
capability:
state-contract:
binding-definition:
profile:
binding-instance:
state-space:
evidence-claim:
application-resolution:
resolution-plan:
```

The portable/local boundary is part of the namespace contract.
`binding-instance:` and `state-space:` never appear in portable contracts.

## Canonical Serialization And Compatibility

New records use the existing AdaOS convention:

```text
schema validation
  -> normalized JSON value
  -> UTF-8 JSON, sorted object keys, compact separators
  -> SHA-256
  -> sha256:<64 lowercase hex>
```

Arrays preserve order. A model normalizes set-like arrays before digesting.
Only finite JSON values are valid. The current generic helper does not reject
NaN by itself, so CBS1 validators must reject non-finite values before calling
it.

The frozen test vector is:

```json
{"items":["b","a"],"name":"flowboard","nested":{"a":1,"z":2},"schema":"adaos.cbs0.digest_vector.v1"}
```

```text
sha256:6984cc9177ce86eb2a8d56b9eb13c7ab4d06de2c7de7b1715b9619976a3efef8
```

Compatibility rules:

- schema names carry the major version and unknown majors fail closed;
- v1 records reject unknown fields except in an explicitly typed extension
  object;
- stable refs identify lineage, while digests identify exact revisions;
- runtime decisions pin digests;
- portable contract versions use SemVer, but package version alone never proves
  semantic compatibility;
- exact digest is the default admission rule;
- compatible ranges and explicit compatibility edges are resolver decisions;
- `BindingDefinition` owns the logical entry point, while package delivery
  metadata owns the physical member;
- local instance and state revisions are append-only;
- `WorkspaceLock` remains the sole active authority.

## Known Gaps Carried Into CBS1/CBS2

- Flowboard state identity is still coupled to skill/resource naming.
- Production state has no `state_space_ref`, revision history, authority epoch,
  or fencing token.
- The selected fixture has no explicit migration executor.
- Current package evidence has no uniform environment or freshness semantics.
- Current artifact planning does not describe semantic resolution or state
  attachments.
- Prototype and production resource types are not yet projections of one
  semantic contract.

These are inputs to the next milestones, not reasons to broaden CBS0.

## Validation Evidence

`tests/test_capability_binding_state_inventory.py` validates:

- inventory shape and pinned source paths;
- complete target-concept mapping;
- capability-use classification;
- namespace uniqueness and prohibited aliases;
- the canonical serialization test vector;
- the selected fixture's current schema/service constants;
- the explicit distinction between prototype SQLite and production JSON state.

Focused validation command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_capability_binding_state_inventory.py `
  tests/test_local_resource_workbench.py `
  tests/test_prototype_resource_workbench.py `
  tests/test_artifact_release_contracts.py
```

## CBS1 Handoff

CBS1 starts from these exact boundaries:

- one capability: `capability:resource.records.manage`;
- one state contract: `state-contract:flowboard.work-items`;
- one local environment: `profile:local/default`;
- one simulation binding and one local-JSON production binding;
- only `capability_conformance` and `state_compatibility` evidence;
- existing package resolver and `WorkspaceLock` authority;
- no graph, external monitor, general solver, sandbox provider, or storage
  migration.

Any implementation that needs a broader abstraction before this fixture can
run must record the concrete blocking invariant rather than silently expanding
the framework.
