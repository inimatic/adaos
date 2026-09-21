# Application Access, Permissions, and Roles Roadmap

Status: policy/runtime V1 and the first Applications/Users & Access Trial
surfaces implemented; complete install/update and child/guest proof remains open.

Last reviewed: 2026-09-17.

Target architecture:
[Application Access, Permissions, and Roles](application-access-permissions.md).
Companion fast-read projection architecture and embedded roadmap:
[Application Registry Projection](application-registry-projection.md).

This roadmap sequences the first enforceable AdaOS slice for Application-level
permissions, Application-defined roles, per-user/child/guest Application access,
runtime enforcement, Users & Access observability, conversational approval
entry points, and Builder final verification before Trial/publication. It
intentionally does not build a general enterprise IAM system before the local
Applications proof exists.

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
- a Builder verification guide and MVP evidence-bundle convention that can be
  extended with Application-specific release-readiness checks.

The missing vertical path is:

```text
Application declaration -> install/update review -> access grant/role assignment
-> runtime enforcement -> Builder final verification -> audit/observability
-> revoke/update review
```

## Implementation Evidence: 2026-09-17 V1 Vertical Slice

The policy and runtime contracts are implemented as a release-bound vertical
slice. Exact local Trial candidates now establish the first user-facing
Applications and Users & Access products, but do not establish the complete
install/update, Pending Actions, child/guest, or remote-consumer proof.
The durable contracts, policy path, management surfaces, Builder gates, and
machine evidence use the same Application and permission-profile digests.
Evidence:

- `src/adaos/domain/application_access.py` defines permission profiles,
  Application roles, Application grants, access decisions, update diffs, and
  Builder verification reports.
- `src/adaos/domain/application.py` binds structured permission profiles,
  profile digests, Application roles, and role-model digests into
  `ApplicationRelease` while preserving legacy `ProjectRelease.permissions`.
- `src/adaos/abi/application.permission-profile.v1.schema.json`,
  `application.access-grant.v1.schema.json`,
  `application.access-decision.v1.schema.json`,
  `application.access-profile-diff.v1.schema.json`,
  `application.verification-report.v1.schema.json`, and the extended
  `application.release.v1.schema.json`/`project.v1.schema.json` publish the
  ABI boundary.
- `src/adaos/services/applications/access.py` and
  `src/adaos/services/applications/store.py` persist and evaluate
  Application-scoped grants, revoke state, profile-review pauses, child/guest
  floors, policy explanations, actor-chain fields, and audit records.
- `src/adaos/sdk/applications.py` exposes reviewed Application-access helpers
  without raw path/process parameters.
- `src/adaos/services/application_registry_projection.py` indexes permission
  profiles and Application roles from DEV projects and ApplicationStore
  releases for Applications/Users & Access read models.
- `src/adaos/apps/api/tool_bridge.py`,
  `src/adaos/services/skill/tool_contract.py`, and
  `src/adaos/services/runtime_action_grants.py` resolve a verified Application,
  subject, release, grant, device/session holder, and actor chain before the
  common runtime decision. Covered Application permissions suppress repeated
  method prompts; uncovered or step-up actions create Application-aware
  Pending Actions.
- `src/adaos/services/applications/access_management.py`,
  `src/adaos/apps/api/application_access.py`, `src/adaos/sdk/access.py`, and
  `src/adaos/services/root_mcp/applications_plane.py` expose one management
  model to Applications, Users & Access, Builder, API, SDK, Root MCP, and
  conversation entry points. Connected-account values are metadata-only and
  secret-redacted.
- `src/adaos/services/root_mcp/users_access_plane.py` exposes owner-governed,
  actor-bound read/invite/manage operations over the same
  `PersonalizationAccessService` and Application-access projection. The DEV
  `users_access` project composes the first separate EN/RU product surface with
  people, invitations, roles, devices, sessions, Application access, and
  activity. Exact candidate `users_access-0-1-3-4000e53e07fe` passes focused
  source tests, four live reversible Root MCP checks, Final Verification, local
  Trial placement, and wide/compact browser review against its full release
  digest.
- The next Users & Access Beta resolves subnet/workspace/webspace choices from
  Root authority instead of asking users to type opaque scope ids. Selecting a
  person in Application access now exposes revision-checked Application-role,
  permission-ceiling and explicit-deny editing plus revoke through the shared
  Applications access service. These controls require fresh wide/compact live
  browser qualification before replacing the retained Trial evidence above.
- `src/adaos/services/builder/domain_packs/application_manager_legacy.py` and
  `applications.compatibility.v1.json` publish the DEV Applications and Users
  & Access surfaces, including assignment/change/revoke, simulation, privacy,
  review, profiler, and Final Verification controls. Exact candidate
  `applications-0-1-8-a627bcd57e25` passes Final Verification, local Trial
  placement, and wide/compact browser review with live list/detail data and no
  renderer or document-overflow failure. The unauthenticated node-status probe
  remains a warning and keyboard qualification remains open.
- `adaos builder application-permissions` and
  `adaos builder application-verify` produce the profiler and blocking-first
  checklist. Trial/publication admission consumes persisted reports and the
  release evidence bundle, including deterministic in-toto-compatible
  statement digests.
- `adaos.sdk.builder.applications` now materializes one managed local
  publisher-owner grant after the exact Trial/publication verification gate.
  New role-bearing Applications declare `default_for.owner`; only a unique
  maximal owner-compatible role is accepted as a legacy fallback. The grant is
  rebound to the reviewed profile digest on a later release and does not alter
  independently assigned subject grants.
- `docs/examples/application-access/project.yaml` provides the complete
  owner/member/child/guest declaration fixture. The focused tests include
  `test_application_access_v1_end_to_end_evidence_bundle`, runtime integration,
  distribution admission, Root MCP, API/SDK, registry isolation, declarative UI,
  and browser tests. The reproducible local run is recorded in
  `docs/evidence/application-access-v1-20260916.md`.

## Sequencing Rules

1. Contract and normalization precede UI management.
2. Application roles enter V1 as declarative, assignable, enforceable facts,
   not as a custom role editor.
3. Application grants complement skill capabilities; they do not replace
   component-level capability admission.
4. Users & Access is a product projection over the shared access service, not a
   second policy store.
5. Runtime enforcement must land before broad conversational approval.
6. Builder final verification is the release boundary; a green chat summary or
   successful prototype is not enough to publish.
7. Child and guest floors are mandatory in the first proof.
8. Method-level Pending Actions remain fallback for uncovered or high-risk
   operations.
9. Advanced policy engines, enterprise identity, and marketplace compliance
   follow evidence from the local proof.
10. Permission-profile and Application-role declaration indexes are read-model
    inputs owned by the Application Registry Projection. Effective
    authorization, dynamic grants, revocation freshness, and audit remain owned
    here.

## AAPR0. Architecture and Ownership

**Outcome:** the contract owner and cross-domain boundaries are explicit.

- [x] `[must]` `AAPR0-01` Publish this target architecture and roadmap with
  SOTA references, ownership boundaries, and first/deferred implementation
  split.
- [x] `[must]` `AAPR0-02` Link Application lifecycle, Personalization, Roadmap
  Inventory, Product Terminology, Builder, Pending Actions, and Users & Access
  planning to this architecture without duplicating its checklist.
- [x] `[must]` `AAPR0-03` Record the first implementation issue bundle with
  named evidence gates for backend contracts, runtime enforcement, Applications
  UI, Users & Access UI, Builder permission profiling, and Builder final
  verification.

**Exit proof:** planning pages route Application permissions, Application
roles, and Application access grants to this roadmap.

## AAPR1. Permission and Role Profile Contract

**Outcome:** Application releases can declare structured permissions and roles
without a full policy engine.

- [x] `[must]` `AAPR1-01` Add
  `adaos.application.permission_profile.v1` with required/optional
  permissions, structured authorization details, data practices, LLM/model use,
  notifications, background actions, secrets, external providers, and approval
  policy.
- [x] `[must]` `AAPR1-02` Preserve the existing flat `permissions` list as a
  compatibility projection derived from the structured profile.
- [x] `[must]` `AAPR1-03` Add a deterministic `permission_profile_digest` and
  bind install/update grants to the digest reviewed by the owner.
- [x] `[must]` `AAPR1-04` Add a minimal `application_roles` declaration:
  role id, title, app capabilities, `assignable_to`, `default_for`,
  `requires_permissions`, and `sensitive`.
- [x] `[must]` `AAPR1-05` Validate role ids, permission ids, and app capability
  ids; unknown ids fail closed.
- [x] `[must]` `AAPR1-06` Add role and permission diff classification:
  unchanged, removed, added, elevated, sensitive-added, affected-users,
  remap-required.
- [x] `[should]` `AAPR1-07` Add privacy label and data-safety projection fields
  mapped from `data_practices`.
- [x] `[could]` `AAPR1-08` Add developer-facing examples for Builder-generated
  Applications with owner/member/child/guest role fixtures.

**Exit proof:** schema tests cover normalization, digest stability, flat-list
compatibility, role validation, and update diffs.

## AAPR2. Application Grants and Policy Kernel

**Outcome:** AdaOS can grant and revoke Application access per subject,
Application role, scope, and permission profile.

- [x] `[must]` `AAPR2-01` Add `application` to the access scope vocabulary or
  provide an equivalent Application-scoped grant object with deterministic
  refs such as `application:<id>`.
- [x] `[must]` `AAPR2-02` Add `ApplicationAccessGrant` records for subject,
  Application id, Application roles, permission ceiling, explicit denies,
  constraints, issuer, status, expiry, and reviewed permission profile digest.
- [x] `[must]` `AAPR2-03` Implement effective decision evaluation across
  declared permission, installed grant, actor membership/capability,
  Application role, resource scope, device/session trust, child/guest floors,
  and explicit denies.
- [x] `[must]` `AAPR2-04` Record actor chain fields on every Application access
  decision: user, application, component, tool/agent/service, external
  provider, device/session, and approval id.
- [x] `[must]` `AAPR2-05` Enforce child floors: guardian approval for sensitive
  permissions, high privacy defaults, and no silent external data sharing.
- [x] `[must]` `AAPR2-06` Enforce guest floors: TTL/session-bound access, no
  profile binding by public guest join, no durable grants, no secrets, and no
  LLM/network/write/background actions by default.
- [x] `[must]` `AAPR2-07` Add audit records for grant create/revoke, role
  assign/change, permission allow/deny, child/guardian approval, guest access,
  and update review.
- [x] `[should]` `AAPR2-08` Add policy explanations suitable for Applications,
  Users & Access, Builder, chat, and logs.
- [x] `[could]` `AAPR2-09` Add coarse access-review detectors for long-lived
  guests, stale devices, unused grants, and sensitive app roles.

**Exit proof:** policy tests prove owner install, member access, child denial
or guardian approval, guest read-only TTL access, revoke cutoff, and update
permission digest behavior.

## AAPR3. Runtime Enforcement

**Outcome:** runtime actions consult Application grants before falling back to
method-level Pending Actions.

- [x] `[must]` `AAPR3-01` Pass verified Application context, actor, subject,
  device/session, webspace, and release digest into tool/action authorization.
- [x] `[must]` `AAPR3-02` Check Application permission profile and
  ApplicationAccessGrant before runtime action approval fallback.
- [x] `[must]` `AAPR3-03` Keep skill manifest/profile capability admission in
  the decision path; an Application grant cannot authorize an undeclared skill
  capability.
- [x] `[must]` `AAPR3-04` Gate LLM/content generation, model access, network
  egress, notifications, background actions, and secrets behind explicit
  Application permissions and component capabilities.
- [x] `[must]` `AAPR3-05` Emit user-facing denial and approval contracts using
  Application language, with tool/method id available only as technical detail.
- [x] `[must]` `AAPR3-06` Reuse durable method/resource approval only when it
  matches the Application grant, subject, resource, webspace, holder, and
  expiry.
- [x] `[must]` `AAPR3-07` Add integration tests for previously noisy prompts:
  an approved Application permission should prevent repeated method-level
  prompts for covered actions.
- [x] `[should]` `AAPR3-08` Add holder binding for trusted device/session on
  sensitive approvals.
- [x] `[could]` `AAPR3-09` Add proof-of-possession compatible fields without
  requiring a full OAuth token server.

**Exit proof:** tool calls are allowed, denied, or converted to Pending Actions
only by the unified decision path, with audit and no cross-subject idempotency
replay.

## AAPR4. Product Surfaces

**Outcome:** Applications and Users & Access expose the same policy state from
Application-centric and subject-centric views.

- [ ] `[must]` `AAPR4-01` Extend Application install/update review to show
  structured permissions, data practices, LLM/network use, secrets,
  notifications, background work, role model, and release-readiness summary.
- [x] `[must]` `AAPR4-01a` Bind install/update plans to
  `adaos.application.permission_review.v1`, render required and optional
  declarations as structured rows, and block apply until newly added or
  elevated permissions are acknowledged. Unchanged updates do not request the
  same approval again. Data-practice, provider, secret, background-work and
  role-impact disclosure remain under `AAPR4-01`.
- [x] `[must]` `AAPR4-02` Add Application detail tabs or sections:
  Permissions, Access, Roles, Connected Accounts, Release Readiness, and
  Activity.
- [x] `[must]` `AAPR4-03` Add minimal role assignment management from
  Applications: assign declared Application role, change role, revoke access,
  and show affected child/guest constraints.
- [x] `[must]` `AAPR4-04` Add Users & Access V1 with People, Guests, Children,
  Devices/Sessions, User Detail, Application Access, and Activity projections.
- [ ] `[must]` `AAPR4-05` Connect Pending Actions to Application permission,
  Users & Access detail, and the exact invoking modal instead of showing only
  raw tool ids. Telegram pairing already fails closed with a network-risk
  Pending Action; inline explanation, deep link, and exact-command resume are
  still open.
- [x] `[must]` `AAPR4-06` Add Builder permission profiler: declared,
  statically inferred, observed, undeclared observed, unused, child/guest
  compatibility, role diff, and inputs for Builder final verification.
- [ ] `[should]` `AAPR4-07` Add responsive compact/wide UI, keyboard flows, and
  EN/RU i18n fixtures for long permission, role, provider, and denial labels.
  Users & Access Beta `0.1.11` passes real-data wide/compact browser journeys
  and generic avatar-initial fallback. Keyboard traversal, long-label and
  denial-state matrices remain open.
- [ ] `[could]` `AAPR4-08` Add app-embedded role management component that
  delegates all writes to platform APIs.

**Current boundary:** the shared management API, projections, Applications
integration, dedicated Root MCP Users & Access plane, and standalone exact
Trial products now exist. Applications performs grant/change/revoke and shows
the Application-centric policy sections; Users & Access exposes the
subject-centric six-section workbench through shared services without direct
database edits. Install/update plans now carry structured permission rows and
an added/elevated-permission gate; complete disclosure (`AAPR4-01`), Pending Action
routing (`AAPR4-05`), keyboard/long-label qualification (`AAPR4-07`), and the
child/guest end-to-end matrix remain open. AdaOS Connect retains join, pairing,
and recovery entry flows and links ongoing administration to Users & Access.

## AAPR5. Builder Final Verification

**Outcome:** Builder can produce a release-bound verification report that
blocks unsafe Application candidates before Trial, publication, or external
install.

- [x] `[must]` `AAPR5-01` Add
  `adaos.application.verification_report.v1` with result states
  `passed|failed|inconclusive|skipped`, gate levels
  `hard_gate|warning|attestation`, report digest, release digest,
  source commit, permission profile digest, observed capability digest,
  evidence refs, warnings, attestations, and residual risks.
- [x] `[must]` `AAPR5-02` Add Builder Final Verification UI/CLI output that
  renders the report as a checklist, names blocking checks first, and links
  each item to evidence or required follow-up.
- [x] `[must]` `AAPR5-03` Validate permission profile schema, normalization,
  flat compatibility projection, digest stability, install/update disclosure
  preview, and permission/role diff classification.
- [x] `[must]` `AAPR5-04` Compare declared, statically inferred, and observed
  capabilities. Undeclared high-risk LLM, network, workspace write, secret,
  notification, background, external-provider, or child-data access is a hard
  gate.
- [x] `[must]` `AAPR5-05` Verify Application role declarations, role capability
  expansion, `assignable_to`, `default_for`, sensitive flags, update impact,
  and owner/member/child/guest/custom-role access matrix fixtures.
- [x] `[must]` `AAPR5-06` Require regression-test evidence for changed
  behavior. For DEV/Candidate, focused affected tests may pass the gate; for
  publication, the report must name the broader release test profile or explain
  a bounded `skipped` scope.
- [x] `[must]` `AAPR5-07` Verify secrets, connected accounts, external provider
  scopes, notifications, background jobs, and data-practice disclosures,
  including missing/revoked states and secret-value redaction in source, logs,
  and report output.
- [x] `[must]` `AAPR5-08` Verify Pending Action fallback cards and routing for
  uncovered or step-up actions, including chat/Telegram keyboard affordances
  and voice handoff text for trusted-device approval.
- [x] `[must]` `AAPR5-09` Verify auditability for allow, deny, approval,
  revoke, update-blocked, child, guest, secret, and external-provider
  decisions, including actor chain and reviewed profile digest.
- [x] `[should]` `AAPR5-10` Integrate the report into the MVP release evidence
  bundle and CI status checks so required checks can be run outside the Builder
  UI.
- [x] `[could]` `AAPR5-11` Emit an in-toto/SLSA-compatible attestation for the
  report after deterministic serialization and signing boundaries exist.

**Exit proof:** one Application candidate has a persisted verification report
with hard gates, warnings, attestations, evidence refs, report digest, and
release/profile/observed-capability digests. Publication rejects a candidate
with an undeclared high-risk observed permission and accepts one that passes.

## AAPR6. First End-to-End Proof

**Outcome:** the first access-aware Applications slice works through real
install, grant, runtime, audit, and revoke flows.

- [ ] `[must]` `AAPR6-01` Owner installs an Application with LLM/network/write
  permissions, sees the profile, grants access, and covered runtime actions do
  not request raw method approval.
- [ ] `[must]` `AAPR6-02` Owner grants a child access with an app role that can
  read or complete assigned work but cannot use LLM/network/secrets without
  guardian approval.
- [ ] `[must]` `AAPR6-03` Owner grants guest access through a TTL link with a
  readonly app role, proves no profile binding, and revokes live access.
- [ ] `[must]` `AAPR6-04` Application update adds or elevates a permission and
  a role capability; auto-update pauses for review and shows affected users.
- [ ] `[must]` `AAPR6-05` Secret/connected-account use shows missing,
  connected, revoked, and denied states without exposing secret values.
- [ ] `[must]` `AAPR6-06` Users & Access shows the same facts from a user,
  child, guest, device/session, and Application perspective.
- [x] `[must]` `AAPR6-07` Builder final verification reports declared versus
  observed permissions, records regression/access-matrix evidence, and blocks
  release on undeclared observed high-risk access.
- [ ] `[should]` `AAPR6-08` Conversational read/explain/revoke works, with
  approval routed to Pending Actions or trusted device for sensitive changes.
- [ ] `[could]` `AAPR6-09` Telegram keyboard mirrors low-risk Pending Action
  decisions with the same grant and audit records.
- [x] `[must]` `AAPR6-10` Run two local Builder cycles on a second generated
  Application with real runtime enforcement. Volunteer Roster proves managed
  publisher-owner access, separate viewer/coordinator credentials, denied
  writes/uploads/private-contact reads, allowed public reads and coordinator
  mutations, exact Trial/Workspace provenance, and profile-bound audit facts.
  Wide/compact Stable browser acceptance also preserves uploaded photo bytes.
  This proof is local and does not claim child/guest, remote consumer or public
  prerelease qualification.

**Exit proof:** one evidence bundle captures release/profile digests, grants,
role assignments, runtime decisions, Pending Actions, child/guest cases,
revoke cutoff, update diff, Builder final verification report, browser views,
and audit queries.

## Should-Level V1

- [x] `[should]` `AAPR-S-01` Add scheduled or prompted access reviews for
  stale grants, inactive users, unused connected accounts, long-lived guests,
  and newly elevated permissions.
- [x] `[should]` `AAPR-S-02` Add Application privacy report projections:
  declared versus observed data categories, model calls, network destinations,
  secrets use, notifications, and background runs.
- [x] `[should]` `AAPR-S-03` Add SDK helpers:
  `ctx.actor`, `ctx.current_user`, `ctx.application`, `ctx.require_app_role`,
  `ctx.require_app_capability`, `ctx.policy.explain`.
- [x] `[should]` `AAPR-S-04` Add access-scoped Builder preview modes:
  owner, member, child, guest, and selected custom Application role.
- [x] `[should]` `AAPR-S-05` Add admin-visible but content-redacted child and
  user-private data diagnostics in Users & Access.
- [x] `[should]` `AAPR-S-06` Add per-provider connected-account policy:
  delegated user account versus app-owned service account, token expiry,
  revoked state, and user-visible scope changes.

## Could-Level V1 Enhancements

- [x] `[could]` `AAPR-C-01` Add a policy simulation UI before committing
  grants or role changes.
- [x] `[could]` `AAPR-C-02` Add role templates for common app families:
  classroom, household tasks, dashboards, moderation, research review, and
  media queues.
- [x] `[could]` `AAPR-C-03` Add marketplace-style privacy labels and safety
  badges generated from permission profile plus observed runtime report.
- [x] `[could]` `AAPR-C-04` Add export/import of access policy snapshots for
  review or backup.
- [x] `[could]` `AAPR-C-05` Add anomaly detection for unexpected permission
  use, sudden network destinations, or broad role assignment changes.

## V1 Boundaries

Completion of a non-deferred item means the smallest enforceable product slice,
not the corresponding deferred platform expansion:

- Application roles are declared by the Application and their assignments can
  be granted, changed, simulated, explained, and revoked. A general custom-role
  editor and role hierarchy remain deferred.
- Users & Access is a shared, metadata-only projection over Personalization and
  Application grants. It does not duplicate identity data or expose child/user
  private content.
- Chat and Telegram use the same typed Pending Action choices, response route,
  grant revision, and audit path. V1 publishes channel-neutral keyboard
  affordances and uses the existing Telegram inline-keyboard projection; it
  does not introduce a separate Telegram authorization store or bot protocol.
- Voice reads the reason and hands sensitive approval to a selected trusted
  device. Voice-only high-risk approval remains deferred.
- Device/session holder fields and grant binding are proof-of-possession
  compatible, but AdaOS does not act as a full OAuth/DPoP token server in V1.
- Final Verification emits deterministic in-toto-compatible statement and
  evidence-bundle digests. Signing, transparency logs, and marketplace
  certification remain deferred.
- Access reviews and anomaly findings are computed on read or invoked by a
  caller in V1. A general scheduler and notification campaign are not required
  for this local proof.

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
- Builder final verification persists a release-bound report with hard gates,
  warnings, attestations, evidence refs, regression evidence, access-matrix
  evidence, and release/profile/observed-capability digests;
- every allowed, denied, approved, revoked, and update-blocked access decision
  is auditable.
