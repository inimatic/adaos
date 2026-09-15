# Application Access, Permissions, and Roles Roadmap

Status: target implementation roadmap.

Last reviewed: 2026-09-15.

Target architecture:
[Application Access, Permissions, and Roles](application-access-permissions.md).

This roadmap sequences the first enforceable AdaOS slice for Application-level
permissions, Application-defined roles, per-user/child/guest Application access,
runtime enforcement, Users & Access observability, and conversational approval
entry points. It intentionally does not build a general enterprise IAM system
before the local Applications proof exists.

## Priority Model

- `[must]`: blocks the first coherent Application access proof;
- `[should]`: required before repeated household/classroom/team use;
- `[could]`: useful product or governance improvement that does not block the
  first proof;
- `[deferred]`: deliberately postponed until the first local proof, a broader
  marketplace, or an enterprise deployment requires it.

A checkbox is complete only with tests, operation receipts, browser evidence,
or documentation evidence named in the item.

## Current Baseline

The repository already has:

- flat Application release permissions in Application install/update plans;
- runtime action approval scopes and durable resource/action grants;
- skill manifest capability admission;
- personalization access subjects, scopes, grants, memberships, role presets,
  invites, device trust, sessions, child and guest foundations, and audit;
- Applications prototype surfaces that show requested permissions during
  review;
- per-skill secrets and vault plumbing;
- Pending Actions and browser retry behavior for method-level action approval.

The missing vertical path is:

```text
Application declaration -> install/update review -> access grant/role assignment
-> runtime enforcement -> audit/observability -> revoke/update review
```

## Sequencing Rules

1. Contract and normalization precede UI management.
2. Application roles enter V1 as declarative, assignable, enforceable facts,
   not as a custom role editor.
3. Application grants complement skill capabilities; they do not replace
   component-level capability admission.
4. Users & Access is a product projection over the shared access service, not a
   second policy store.
5. Runtime enforcement must land before broad conversational approval.
6. Child and guest floors are mandatory in the first proof.
7. Method-level Pending Actions remain fallback for uncovered or high-risk
   operations.
8. Advanced policy engines, enterprise identity, and marketplace compliance
   follow evidence from the local proof.

## AAPR0. Architecture and Ownership

**Outcome:** the contract owner and cross-domain boundaries are explicit.

- [x] `[must]` `AAPR0-01` Publish this target architecture and roadmap with
  SOTA references, ownership boundaries, and first/deferred implementation
  split.
- [x] `[must]` `AAPR0-02` Link Application lifecycle, Personalization, Roadmap
  Inventory, Product Terminology, Builder, Pending Actions, and Users & Access
  planning to this architecture without duplicating its checklist.
- [ ] `[must]` `AAPR0-03` Record the first implementation issue bundle with
  named evidence gates for backend contracts, runtime enforcement, Applications
  UI, Users & Access UI, and Builder permission profiling.

**Exit proof:** planning pages route Application permissions, Application
roles, and Application access grants to this roadmap.

## AAPR1. Permission and Role Profile Contract

**Outcome:** Application releases can declare structured permissions and roles
without a full policy engine.

- [ ] `[must]` `AAPR1-01` Add
  `adaos.application.permission_profile.v1` with required/optional
  permissions, structured authorization details, data practices, LLM/model use,
  notifications, background actions, secrets, external providers, and approval
  policy.
- [ ] `[must]` `AAPR1-02` Preserve the existing flat `permissions` list as a
  compatibility projection derived from the structured profile.
- [ ] `[must]` `AAPR1-03` Add a deterministic `permission_profile_digest` and
  bind install/update grants to the digest reviewed by the owner.
- [ ] `[must]` `AAPR1-04` Add a minimal `application_roles` declaration:
  role id, title, app capabilities, `assignable_to`, `default_for`,
  `requires_permissions`, and `sensitive`.
- [ ] `[must]` `AAPR1-05` Validate role ids, permission ids, and app capability
  ids; unknown ids fail closed.
- [ ] `[must]` `AAPR1-06` Add role and permission diff classification:
  unchanged, removed, added, elevated, sensitive-added, affected-users,
  remap-required.
- [ ] `[should]` `AAPR1-07` Add privacy label and data-safety projection fields
  mapped from `data_practices`.
- [ ] `[could]` `AAPR1-08` Add developer-facing examples for Builder-generated
  Applications with owner/member/child/guest role fixtures.

**Exit proof:** schema tests cover normalization, digest stability, flat-list
compatibility, role validation, and update diffs.

## AAPR2. Application Grants and Policy Kernel

**Outcome:** AdaOS can grant and revoke Application access per subject,
Application role, scope, and permission profile.

- [ ] `[must]` `AAPR2-01` Add `application` to the access scope vocabulary or
  provide an equivalent Application-scoped grant object with deterministic
  refs such as `application:<id>`.
- [ ] `[must]` `AAPR2-02` Add `ApplicationAccessGrant` records for subject,
  Application id, Application roles, permission ceiling, explicit denies,
  constraints, issuer, status, expiry, and reviewed permission profile digest.
- [ ] `[must]` `AAPR2-03` Implement effective decision evaluation across
  declared permission, installed grant, actor membership/capability,
  Application role, resource scope, device/session trust, child/guest floors,
  and explicit denies.
- [ ] `[must]` `AAPR2-04` Record actor chain fields on every Application access
  decision: user, application, component, tool/agent/service, external
  provider, device/session, and approval id.
- [ ] `[must]` `AAPR2-05` Enforce child floors: guardian approval for sensitive
  permissions, high privacy defaults, and no silent external data sharing.
- [ ] `[must]` `AAPR2-06` Enforce guest floors: TTL/session-bound access, no
  profile binding by public guest join, no durable grants, no secrets, and no
  LLM/network/write/background actions by default.
- [ ] `[must]` `AAPR2-07` Add audit records for grant create/revoke, role
  assign/change, permission allow/deny, child/guardian approval, guest access,
  and update review.
- [ ] `[should]` `AAPR2-08` Add policy explanations suitable for Applications,
  Users & Access, Builder, chat, and logs.
- [ ] `[could]` `AAPR2-09` Add coarse access-review detectors for long-lived
  guests, stale devices, unused grants, and sensitive app roles.

**Exit proof:** policy tests prove owner install, member access, child denial
or guardian approval, guest read-only TTL access, revoke cutoff, and update
permission digest behavior.

## AAPR3. Runtime Enforcement

**Outcome:** runtime actions consult Application grants before falling back to
method-level Pending Actions.

- [ ] `[must]` `AAPR3-01` Pass verified Application context, actor, subject,
  device/session, webspace, and release digest into tool/action authorization.
- [ ] `[must]` `AAPR3-02` Check Application permission profile and
  ApplicationAccessGrant before runtime action approval fallback.
- [ ] `[must]` `AAPR3-03` Keep skill manifest/profile capability admission in
  the decision path; an Application grant cannot authorize an undeclared skill
  capability.
- [ ] `[must]` `AAPR3-04` Gate LLM/content generation, model access, network
  egress, notifications, background actions, and secrets behind explicit
  Application permissions and component capabilities.
- [ ] `[must]` `AAPR3-05` Emit user-facing denial and approval contracts using
  Application language, with tool/method id available only as technical detail.
- [ ] `[must]` `AAPR3-06` Reuse durable method/resource approval only when it
  matches the Application grant, subject, resource, webspace, holder, and
  expiry.
- [ ] `[must]` `AAPR3-07` Add integration tests for previously noisy prompts:
  an approved Application permission should prevent repeated method-level
  prompts for covered actions.
- [ ] `[should]` `AAPR3-08` Add holder binding for trusted device/session on
  sensitive approvals.
- [ ] `[could]` `AAPR3-09` Add proof-of-possession compatible fields without
  requiring a full OAuth token server.

**Exit proof:** tool calls are allowed, denied, or converted to Pending Actions
only by the unified decision path, with audit and no cross-subject idempotency
replay.

## AAPR4. Product Surfaces

**Outcome:** Applications and Users & Access expose the same policy state from
Application-centric and subject-centric views.

- [ ] `[must]` `AAPR4-01` Extend Application install/update review to show
  structured permissions, data practices, LLM/network use, secrets,
  notifications, background work, and role model.
- [ ] `[must]` `AAPR4-02` Add Application detail tabs or sections:
  Permissions, Access, Roles, Connected Accounts, and Activity.
- [ ] `[must]` `AAPR4-03` Add minimal role assignment management from
  Applications: assign declared Application role, change role, revoke access,
  and show affected child/guest constraints.
- [ ] `[must]` `AAPR4-04` Add Users & Access V1 with People, Guests, Children,
  Devices/Sessions, User Detail, Application Access, and Activity projections.
- [ ] `[must]` `AAPR4-05` Connect Pending Actions to Application permission
  and Users & Access detail instead of showing only raw tool ids.
- [ ] `[must]` `AAPR4-06` Add Builder permission profiler: declared,
  statically inferred, observed, undeclared observed, unused, child/guest
  compatibility, and role diff.
- [ ] `[should]` `AAPR4-07` Add responsive compact/wide UI, keyboard flows, and
  EN/RU i18n fixtures for long permission, role, provider, and denial labels.
- [ ] `[could]` `AAPR4-08` Add app-embedded role management component that
  delegates all writes to platform APIs.

**Exit proof:** browser tests cover Application-centric and user-centric
access management without direct database edits.

## AAPR5. First End-to-End Proof

**Outcome:** the first access-aware Applications slice works through real
install, grant, runtime, audit, and revoke flows.

- [ ] `[must]` `AAPR5-01` Owner installs an Application with LLM/network/write
  permissions, sees the profile, grants access, and covered runtime actions do
  not request raw method approval.
- [ ] `[must]` `AAPR5-02` Owner grants a child access with an app role that can
  read or complete assigned work but cannot use LLM/network/secrets without
  guardian approval.
- [ ] `[must]` `AAPR5-03` Owner grants guest access through a TTL link with a
  readonly app role, proves no profile binding, and revokes live access.
- [ ] `[must]` `AAPR5-04` Application update adds or elevates a permission and
  a role capability; auto-update pauses for review and shows affected users.
- [ ] `[must]` `AAPR5-05` Secret/connected-account use shows missing,
  connected, revoked, and denied states without exposing secret values.
- [ ] `[must]` `AAPR5-06` Users & Access shows the same facts from a user,
  child, guest, device/session, and Application perspective.
- [ ] `[must]` `AAPR5-07` Builder reports declared versus observed permissions
  and blocks release on undeclared observed high-risk access.
- [ ] `[should]` `AAPR5-08` Conversational read/explain/revoke works, with
  approval routed to Pending Actions or trusted device for sensitive changes.
- [ ] `[could]` `AAPR5-09` Telegram keyboard mirrors low-risk Pending Action
  decisions with the same grant and audit records.

**Exit proof:** one evidence bundle captures release/profile digests, grants,
role assignments, runtime decisions, Pending Actions, child/guest cases,
revoke cutoff, update diff, browser views, and audit queries.

## Should-Level Follow-Up

- [ ] `[should]` `AAPR-S-01` Add scheduled or prompted access reviews for
  stale grants, inactive users, unused connected accounts, long-lived guests,
  and newly elevated permissions.
- [ ] `[should]` `AAPR-S-02` Add Application privacy report projections:
  declared versus observed data categories, model calls, network destinations,
  secrets use, notifications, and background runs.
- [ ] `[should]` `AAPR-S-03` Add SDK helpers:
  `ctx.actor`, `ctx.current_user`, `ctx.application`, `ctx.require_app_role`,
  `ctx.require_app_capability`, `ctx.policy.explain`.
- [ ] `[should]` `AAPR-S-04` Add access-scoped Builder preview modes:
  owner, member, child, guest, and selected custom Application role.
- [ ] `[should]` `AAPR-S-05` Add admin-visible but content-redacted child and
  user-private data diagnostics in Users & Access.
- [ ] `[should]` `AAPR-S-06` Add per-provider connected-account policy:
  delegated user account versus app-owned service account, token expiry,
  revoked state, and user-visible scope changes.

## Could-Level Follow-Up

- [ ] `[could]` `AAPR-C-01` Add a policy simulation UI before committing
  grants or role changes.
- [ ] `[could]` `AAPR-C-02` Add role templates for common app families:
  classroom, household tasks, dashboards, moderation, research review, and
  media queues.
- [ ] `[could]` `AAPR-C-03` Add marketplace-style privacy labels and safety
  badges generated from permission profile plus observed runtime report.
- [ ] `[could]` `AAPR-C-04` Add export/import of access policy snapshots for
  review or backup.
- [ ] `[could]` `AAPR-C-05` Add anomaly detection for unexpected permission
  use, sudden network destinations, or broad role assignment changes.

## Deferred

- [ ] `[deferred]` `AAPR-D-01` Full IAM or policy-as-code engine adoption.
- [ ] `[deferred]` `AAPR-D-02` Enterprise SSO, SAML, OIDC, LDAP, and SCIM
  provisioning.
- [ ] `[deferred]` `AAPR-D-03` General groups, teams, nested roles, and role
  hierarchy.
- [ ] `[deferred]` `AAPR-D-04` Full OAuth-compatible authorization server,
  token introspection, DPoP enforcement, and external resource-server protocol
  compatibility.
- [ ] `[deferred]` `AAPR-D-05` Fine-grained row, field, document, or object
  ACLs for every Application.
- [ ] `[deferred]` `AAPR-D-06` Quorum, threshold, delegated, or emergency-break
  glass approval workflows.
- [ ] `[deferred]` `AAPR-D-07` Full voice-only approval for high-risk actions.
- [ ] `[deferred]` `AAPR-D-08` Full secret rotation workflows, enterprise
  secret-manager adapters, and policy-based automatic credential rotation.
- [ ] `[deferred]` `AAPR-D-09` Open marketplace compliance review, malware
  scanning, privacy-label audit, age-rating, and trust ranking.
- [ ] `[deferred]` `AAPR-D-10` Commercial entitlements, billing roles, and
  licensing enforcement.

## Completion Definition

This roadmap is complete for V1 when:

- every installed Application has a structured permission profile and digest;
- Applications install/update review shows permission, data, secret,
  notification, LLM/network, and role impact;
- users, children, and guests can receive Application access and roles through
  platform-issued grants;
- runtime decisions intersect Application permission, Application role, skill
  capability, actor membership, resource scope, device/session trust, child and
  guest floors, and approval policy;
- method-level approvals are fallback rather than normal flow for covered
  Application permissions;
- Applications and Users & Access show the same grants, roles, decisions, and
  revocation state;
- Builder can explain declared, inferred, observed, and undeclared
  permissions;
- every allowed, denied, approved, revoked, and update-blocked access decision
  is auditable.
