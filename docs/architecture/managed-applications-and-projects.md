# Managed Applications and Projects

Status: core contract implemented; product integrations tracked by Dev Tickets.

Last reviewed: 2026-09-26.

This document defines the relationship used when one AdaOS Application creates
and manages launchable software objects that have no useful standalone product
meaning. The product term for those objects is **Project**; the platform term is
**managed Application**.

Research Workbench is the first consumer. The contract is deliberately generic
so Media Center, Notebook, or a future workbench can use it without adding a
Research-specific type to Application Core.

## Decision

An ordinary Application may own zero or more managed Projects:

```text
Application: Research Workbench
  -> Project: TLP implementation
  -> Project: Replication benchmark
```

Both nodes retain stable Application identities, entry points, releases,
runtime selections, permissions, and diagnostic projections. The relationship
changes product discovery and lifecycle authority; it does not merge storage,
permissions, release identity, or runtime data.

The canonical Application fields are:

```yaml
application:
  application_id: research_project_tlp
  kind: project
  owner_application_id: research_workbench
```

`kind` is `application | project`. Missing `kind` in an existing v1 record is
read as `application`. `owner_application_id` is required only for `project`.
The relationship is immutable in the initial contract.

This is not a package dependency. A dependency says that one implementation
component is required by another release. Ownership says that a user reaches,
creates, and governs a Project through its owner Application.

## Invariants

- a Project has exactly one registered ordinary Application as owner;
- the owner and Project have the same publisher authority;
- v1 does not allow Project-to-Project nesting;
- a Project cannot be a protected system Application;
- a Project cannot select its own stable/prerelease update track;
- a Project installation is admitted only while its owner has an active
  installation or selected local Trial;
- removing an owner is rejected while any owned Project is installed or has an
  active runtime selection;
- deleting or retiring an owner definition is rejected while owned Project
  definitions remain;
- a Project is excluded from ordinary Applications inventory and Catalog by
  default, even if its internal release metadata is public;
- registry hydration cannot import a Project as a standalone Catalog product;
- Project data, grants, secrets, permissions, migrations, and release evidence
  remain independently scoped. Ownership grants no implicit storage access.

The initial Core path supports owner-first provisioning. A workbench installs
or selects first and then provisions Projects. A future single reviewed plan may
install an owner and a declared Project set together, but it must preserve the
same preconditions, receipts, rollback boundary, and per-Application evidence.

## Read Projections

`applications.list` hides Projects by default and accepts:

- `include_projects=true` for an explicit all-items/advanced view;
- `owner_application_id=<id>` for the direct Projects of one owner.

`applications.show` exposes both directions:

- an owner includes `managed_projects[]` summaries;
- a Project includes `owner_application` for the **Managed by** link.

Application Registry Projection stores `kind` and
`owner_application_id` in its rebuildable payload and indexes the Application
row with the correct kind. Authoritative ownership remains in the Application
aggregate, not in SQLite projection state.

Builder creation accepts the same fields through
`applications.development.create`. Idempotent replay preserves them; it cannot
replay an old create intent as a different kind or owner.

## Product Surfaces

### Applications

The ordinary inventory remains Application-only. A user can enable an explicit
**Show projects** filter for inspection. Application detail renders:

- **Projects** on an owner, using the owner-scoped list projection;
- **Managed by _Application_** on a Project, with an Application navigation
  target;
- no standalone Install, update-track, or Catalog action for a Project;
- technical release, permissions, placement, operation, and diagnostics detail
  where it remains useful.

### Desktop and Home

Desktop stores a per-user **Show projects** preference, default `false`. This
controls discovery lists only. An explicit Home pin is stronger than the
preference: a pinned Project remains visible on that Home until unpinned.

Pin state remains a Webspace presentation overlay. It does not install a
Project, prove runtime health, or change the ownership relationship. Existing
`applications.set_home_pin` is the only mutation authority.

### Owner Workbench

An owner presents its own domain label; Research Workbench uses **Projects**.
It creates managed Applications with its own `application_id` as
`owner_application_id`, lists only that direct relation, and offers Pin/Unpin
for selected Homes/Webspaces through the shared Applications plane.

A ResearchDirection is still a live research-domain aggregate. It is not
automatically a Project. A Project represents a launchable/versioned
implementation or work product when such an identity is useful. Directions,
tasks, candidates, or experiments must not be promoted to Applications merely
to obtain a shortcut.

## Lifecycle and Failure UX

Owner removal must show the blocking Project identities and require the user to
remove/archive them through the owning workflow before retrying. Core does not
silently cascade deletion.

If owner availability is lost after a Project install plan but before apply,
Core rechecks the relationship and refuses execution. If projection state is
stale or missing, clients show the exact unavailable condition and do not infer
an owner by title, component, category, or entry point.

Uninstalling or deleting a Project removes its obsolete Home pins through the
normal Application lifecycle. Updating/reinstalling the owner must not delete
Project data. Backup, retention, and migration remain explicit per-Project
contracts until a future aggregate backup contract is reviewed.

## Acceptance Matrix

The model is accepted only when tests prove:

- v1 records without `kind` remain readable;
- invalid, self-owned, nested, cross-publisher, and missing-owner records fail;
- owner-first Project install succeeds and owner-absent install fails at plan
  and apply;
- active Projects block owner removal;
- Project update-track selection and standalone registry import fail;
- default list/Catalog omit Projects, while explicit and owner-scoped reads
  return them;
- owner and Project details expose navigable inverse relations;
- Show projects is off by default and never hides an explicitly pinned Project;
- Research Workbench can create, list, open, pin, unpin, restart, and recover a
  Project without direct Desktop or Application-store writes.

## Related Documents

- [Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md)
- [Application Lifecycle and Distribution Roadmap](application-lifecycle-and-distribution-roadmap.md)
- [AdaOS Product Terminology](product-terminology.md)
- [Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
- [AdaOS Research Fabric](research-fabric.md)
- [Research Fabric Roadmap](research-fabric-roadmap.md)
