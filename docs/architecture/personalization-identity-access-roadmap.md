# Personalization, Identity, and Access Roadmap

Status: implementation roadmap and progress tracker.

Target architecture:
[Personalization, Identity, and Access](personalization-identity-access.md).

This roadmap is intentionally phase-gated. The first goal is not to build a
large user-management UI; it is to land the minimum durable identity, policy,
audit, and privacy foundations that make later QR/link and personalization
flows safe to implement.

## Progress

- [x] 2026-07-02: target architecture and initial roadmap published.
- [x] 2026-07-02: roadmap refactored into explicit implementation phases,
  gates, and local verification requirements.
- [x] 2026-07-02: Phase 0 gate opened; next implementation work is versioned
  schemas, threat model, scope lattice, and migration notes.
- [x] 2026-07-02: Phase 0 foundation contracts implemented in
  `src/adaos/domain/personalization_access.py`,
  [Personalization Phase 0 Contracts](personalization-identity-access-phase0-contracts.md),
  and `tests/test_personalization_access_contracts.py`.
- [x] 2026-07-02: Phase 0 local verification passed with targeted pytest,
  adjacent domain tests, `git diff --check`, and MkDocs strict build.
- [x] Phase 0 foundation contracts implemented.
- [x] 2026-07-02: Phase 1 access kernel implemented in
  `src/adaos/services/personalization_access.py`,
  [Personalization Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md),
  and `tests/test_personalization_access_kernel.py`.
- [x] Phase 1 subject/session/grant store and policy kernel implemented.
- [x] 2026-07-02: Phase 2 profile/preferences slice implemented in
  `src/adaos/services/user/profile.py`,
  [Personalization Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md),
  and `tests/test_personalization_profile_phase2.py`.
- [x] 2026-07-02: Phase 3 guest join and targeted invite slice implemented in
  `src/adaos/services/personalization_access.py`,
  [Personalization Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md),
  and `tests/test_personalization_join_phase3.py`.

## Execution rules

- Each phase must have schema, service, SDK/API, UI/projection, audit, and test
  implications reviewed before it is marked complete.
- Later design work may proceed in docs, but runtime implementation should not
  skip a gate unless the skip is documented in this roadmap.
- A user-facing flow is not complete until policy enforcement and audit records
  exist below the UI or skill surface.
- `owner` remains a subnet-level technical superuser, but product surfaces must
  still preserve user-private data boundaries.
- Root-server or external identity can verify who a user is; subnet grants
  decide what that identity may do inside the subnet.

## Phase 0 - Foundation Contracts

Priority: `must`.

Goal: make the model implementable without freezing the wrong abstractions.
This phase produces versioned draft contracts, not irreversible final schemas.
Contract note:
[Personalization Phase 0 Contracts](personalization-identity-access-phase0-contracts.md).

Checklist:

- [x] Define the scope lattice: `subnet`, `workspace`, `webspace`, `scenario`,
  `skill`, `device/session`, `user_private`, and shared workspace data.
- [x] Define versioned draft schemas for `UserProfile`, `UserKey`,
  `DeviceKey`, `SessionKey`, `Membership`, `Grant`, `Capability`,
  `Preference`, `Invite`, `RecoveryAction`, and `ExternalIdentityBinding`.
- [x] Define schema-version and migration rules for identity/access records.
- [x] Document migration from current `Settings.owner_id`, `local-owner`,
  `UserProfileService`, `profile.settings` projections, `access_links`, and
  browser scoped storage.
- [x] Define the first capability vocabulary and role presets:
  `owner`, `co_owner`, `admin`, `member`, `child`, and `guest`.
- [x] Define grant constraints: `expires_at`, `requires_approval_for`,
  `child_mode`, allowed scopes, allowed skill/tool classes, and delegation.
- [x] Define actor semantics: `actor`, `current_user`, `subject_user`,
  service identity, and skill `on_behalf_of`.
- [x] Define the threat model for public QR abuse, targeted invite leakage,
  stolen devices, owner/co-owner key loss, skill privilege abuse, revoked live
  sessions, and cross-subnet identity correlation.
- [x] Define audit event schemas, redaction rules, retention defaults, and
  query dimensions.
- [x] Define the required security regression matrix for later phases.

Exit gate:

- [x] Architecture and roadmap documents describe every contract above with a
  versioning and migration stance.
- [x] No runtime implementation is blocked on undefined terms for subject,
  session, membership, grant, capability, or audit.

Local verification:

- [x] `git diff --check`
- [x] targeted docs build or link check completes locally, or the failure is
  recorded with exact command and cause.

## Phase 1 - Subject, Session, Grant Store, and Policy Kernel

Priority: `must`.

Goal: land the backend decision layer before user-facing access flows.
Kernel note:
[Personalization Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md).

Checklist:

- [x] Add durable storage/service contracts for users, profiles, user keys,
  device keys, sessions, memberships, grants, invites, and revocations.
- [x] Add a minimal policy evaluator:
  `is_allowed(actor, action, subject, scope, resource, context)`.
- [x] Implement owner implicit subnet-admin semantics.
- [x] Implement initial `co_owner`, `admin`, `member`, `child`, and `guest`
  role presets as grants/capability bundles.
- [x] Return structured allow/deny decisions with reason codes.
- [x] Add append-only audit records for grants, denials, profile/preference
  changes, join/invite actions, device actions, and recovery actions.
- [x] Add audit query helpers by actor, subject, scope, device/session, source,
  decision, and time range.
- [x] Implement revocation propagation rules for grants, sessions, and devices.
- [x] Add replay/stale-write protections for invite and recovery material.

Exit gate:

- [x] A local test can create a user, grant scoped membership, allow an
  authorized action, deny an unauthorized action, revoke the grant, and observe
  the audit trail.
- [x] Policy decisions do not depend on UI state or skill-local state.

Local verification:

- [x] unit tests for policy allow/deny/revoke/audit paths.
- [x] migration test from the existing owner/local profile baseline.

## Phase 2 - Profile and Preferences Vertical Slice

Priority: `must`.

Goal: turn current profile/settings mechanisms into the first user-visible
personalization slice without mixing role into profile data.
Slice note:
[Personalization Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md).

Checklist:

- [x] Migrate `UserProfileService` onto the versioned profile/preference
  contract while preserving existing SDK compatibility.
- [x] Keep `role` and membership out of profile settings.
- [x] Add `ctx.current_user`, `ctx.profile`, and `ctx.preferences` SDK surface
  backed by policy-checked services.
- [x] Keep existing `profile_get_settings` and `profile_update_settings`
  helpers as compatibility wrappers.
- [x] Project current-user profile/preferences through KV/Yjs using the existing
  projection mechanism.
- [x] Add client header settings for display name, locale/language, theme,
  memory/privacy preferences, current subnet/workspace, role status, and device
  trust status.
- [x] Add the service/SDK target for browser-scoped UI preferences as user
  preferences plus device overrides; client localStorage fallback remains a UI
  integration concern.
- [x] Emit redacted audit records for profile and preference updates.

Exit gate:

- [x] Current user can edit self-service profile/preferences through SDK
  helpers; client UI wiring remains outside this phase.
- [x] Role/access preset is visible as status but cannot be edited as a profile
  field.
- [x] Profile/preference updates survive restart and project to Yjs.

Local verification:

- [x] profile SDK tests.
- [x] KV/Yjs projection tests.
- [x] service/SDK header settings smoke test; browser UI wiring remains outside
  this phase.

## Phase 3 - Guest Join and Targeted Invite Flows

Priority: `must`.

Goal: implement safe QR/link entry before device pairing and recovery.
Slice note:
[Personalization Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md).

Checklist:

- [x] Implement `guest_join_link` as public, temporary, scope-limited, and not
  profile-bound.
- [x] Implement `targeted_invite_link` as personal, expiring, one-time, and
  auditable.
- [x] Add `profile_hint` support without treating the hint as proof of identity.
- [x] Require joining devices to display the target subnet/workspace, role
  preset, lifetime, and consent/acceptance action.
- [x] Add owner/co-owner flow for binding an unknown session to a new or
  existing local profile.
- [x] Add invite/link rate limits, max session constraints, and bulk guest
  revocation.
- [x] Reject expired, reused, revoked, stale, or wrong-scope invite material.
- [x] Cut off live browser/Yjs access when the backing guest or invite grant is
  revoked, via session revocation and an access-link denial hook.

Exit gate:

- [x] Owner can create a public guest join and revoke all sessions created from
  it.
- [x] Owner can create a targeted invite, user can accept it once, reuse is
  rejected, and audit records show issuer, subject, scope, role preset, and
  constraints.

Local verification:

- [x] guest join policy tests.
- [x] targeted invite expiry/reuse/revoke tests.
- [x] live session cutoff test for revoked guest/invite grant.

## Phase 4 - Device Pairing and Admin Recovery

Priority: `must`.

Goal: let trusted users add devices and let owner/co-owner recover users without
creating unsafe account-takeover shortcuts.

Checklist:

- [ ] Implement `device_pairing_link` for adding a new device to an existing
  trusted user.
- [ ] Support member self-service device pairing when policy allows
  `devices.add.self`.
- [ ] Require owner/co-owner approval for child device pairing by default.
- [ ] Implement `admin_recovery_link` for owner/co-owner assisted recovery.
- [ ] Add lost-device flow: bind replacement device, revoke old device, and
  invalidate active sessions.
- [ ] Add device key lifecycle: generation, storage, rotation hooks,
  revocation, last-used metadata, and session invalidation.
- [ ] Document owner key backup, co-owner recovery, and ownership transfer as
  explicit later work if not implemented in this phase.

Exit gate:

- [ ] A user can add a second device through an already trusted device when
  policy allows it.
- [ ] Owner/co-owner can bind a replacement device to an existing profile and
  revoke the lost device in the same flow.
- [ ] Lost/revoked devices cannot keep active browser/Yjs/API access.

Local verification:

- [ ] device pairing tests.
- [ ] child approval tests.
- [ ] lost-device revoke/session invalidation tests.

## Phase 5 - Owner and Admin User Management Surface

Priority: `must`.

Goal: expose the foundation through a usable owner/admin control surface without
letting the skill or UI become the source of truth.

Checklist:

- [ ] Add a shared runtime service API for users, profiles, devices,
  memberships, grants, invites, and recovery actions.
- [ ] Add owner/co-owner/admin-facing user management skill or control-plane UI.
- [ ] Use access presets for common flows: family member, child, guest, admin,
  and custom.
- [ ] Show active grants, expired grants, revoked grants, pending invites,
  devices, sessions, and audit history.
- [ ] Allow role preset changes through policy-checked shared services.
- [ ] Allow device/session revoke through shared services.
- [ ] Show admin-visible privacy metadata without showing private content.
- [ ] Use Pending Actions for sensitive conversational requests such as binding
  a device, granting membership, changing active user, or invoking dangerous
  tools.

Exit gate:

- [ ] Owner/co-owner can manage users, memberships, devices, invites, and
  revocation without direct database edits.
- [ ] All actions route through the shared policy/audit services.

Local verification:

- [ ] API/service tests.
- [ ] skill/UI action tests.
- [ ] audit query smoke tests.

## Phase 6 - Skill, Tool, and SDK Enforcement

Priority: `must`.

Goal: make personalization and access checks part of skill design and tool
execution, not only user-management flows.

Checklist:

- [ ] Extend skill manifest vocabulary for declared personalization usage,
  required permissions, optional permissions, role variants, user variants, and
  device variants.
- [ ] Add SDK helpers for `ctx.actor`, `ctx.current_user`, `ctx.subject_user`,
  `ctx.profile`, `ctx.preferences`, `ctx.require`, and `ctx.policy.explain`.
- [ ] Model service identities and skill `on_behalf_of` behavior.
- [ ] Enforce both skill permission and actor capability before sensitive tool
  invocation.
- [ ] Add policy gates for memory reads/writes, profile writes, device actions,
  browser automation, skill installation, and workspace writes.
- [ ] Return policy explanations to user-visible surfaces where appropriate.
- [ ] Add generated-skill examples that use Pending Actions for long-term
  personalization writes.

Exit gate:

- [ ] A skill cannot read or write another user's private data without a grant.
- [ ] A tool invocation can be denied based on actor role/capability even when
  the skill exists and is installed.
- [ ] Manifest-declared permissions are validated before activation or use.

Local verification:

- [ ] manifest validation tests.
- [ ] SDK policy tests.
- [ ] sensitive tool denial tests.
- [ ] memory/profile cross-user denial tests.

## Phase 7 - Privacy Zone Enforcement and User Data Management

Priority: `should`.

Goal: enforce privacy below UI conventions and give users control over their
own data.

Checklist:

- [ ] Add service-level data classification for shared workspace data,
  user-private data, admin-visible metadata, and encrypted/private future data.
- [ ] Enforce user-private read/write policy in memory, conversation, profile,
  and preference services.
- [ ] Add user-owned memory/profile search, edit, export, and redaction flows.
- [ ] Add admin-visible metadata views that show existence, usage, policy
  events, and retention without revealing private content.
- [ ] Add retention defaults and redaction audit trails for user-private data.
- [ ] Add compatibility checks so existing owner-superuser paths do not become
  ordinary product read APIs for private user data.

Exit gate:

- [ ] Product UI respects privacy zones.
- [ ] Service/API paths enforce the same privacy zones.
- [ ] User can inspect and manage their own private profile/memory data.

Local verification:

- [ ] user-private access tests.
- [ ] admin metadata/no-content tests.
- [ ] export/redaction tests.

## Phase 8 - Optional Global Identity and Root-Server Trust

Priority: `could`.

Goal: add remote trust and cross-device convenience without making root-server
accounts mandatory for local subnets.

Checklist:

- [ ] Add `ExternalIdentityBinding` service and API.
- [ ] Add pairwise public key support to reduce cross-subnet correlation.
- [ ] Add optional root-server verification for targeted remote invites.
- [ ] Add passkey-backed global identity as an optional recovery provider.
- [ ] Let login choose between known local subnet identity, global identity,
  guest join, and access request.
- [ ] Enforce the rule: root verifies identity, subnet grants access.
- [ ] Add profile portability tooling between subnets with destination owner
  acceptance.

Exit gate:

- [ ] A global identity can be bound to a local profile.
- [ ] A verified global identity still has no subnet access without a local
  membership/grant.
- [ ] Pairwise identity behavior is tested or explicitly deferred.

Local verification:

- [ ] external identity binding tests.
- [ ] local-grant-required tests.
- [ ] invite verification tests where implemented.

## Phase 9 - Enterprise and Advanced Governance

Priority: `could`.

Goal: support organization-grade identity and governance without distorting the
local-first household model.

Checklist:

- [ ] Add `TrustProvider` SDK/service interface for OIDC, SAML, LDAP, and local
  directory providers.
- [ ] Add first SSO/IdP pilot that maps external claims to proposed local
  memberships and capabilities.
- [ ] Add admin scopes such as workspace admin, device admin, and guest
  moderator.
- [ ] Add policy simulation UI before committing grants.
- [ ] Add time-window constraints for classrooms, museums, events, and guests.
- [ ] Add richer localization for invite, consent, recovery, and denial flows.
- [ ] Add compliance export/reporting only after the core audit model is stable.

Exit gate:

- [ ] External IdP can propose, but not silently grant, local subnet access.
- [ ] Admin scopes are enforced through the same policy engine as household
  roles.

Local verification:

- [ ] trust-provider contract tests.
- [ ] claim-to-proposed-grant tests.
- [ ] admin-scope policy tests.

## Deferred

These items should remain visible but should not block phases 0-9:

- [ ] Full cryptographic isolation of user-private data from the subnet owner.
- [ ] Secret-store redesign for per-user and per-device encryption keys.
- [ ] Root-server mandatory account system.
- [ ] Fully autonomous recovery with no owner, co-owner, trusted device,
  recovery code, or external identity provider.
- [ ] Cross-subnet federation where a remote subnet can grant access without a
  local owner/admin decision.
- [ ] Quorum-based administration for high-security deployments.
- [ ] Hardware-backed key management requirements for all trusted devices.
- [ ] Mature SSO group-to-capability synchronization and deprovisioning.

## MoSCoW coverage

### Must

- [x] Phase 0 foundation contracts.
- [x] Phase 1 subject/session/grant store and policy/audit kernel.
- [x] Phase 2 profile/preferences vertical slice.
- [x] Phase 3 guest join and targeted invite flows.
- [ ] Phase 4 device pairing and admin recovery.
- [ ] Phase 5 owner/admin user management surface.
- [ ] Phase 6 skill, tool, and SDK enforcement.

### Should

- [ ] Phase 7 privacy-zone enforcement and user data management.
- [ ] Recovery codes generated while a user still has a trusted device.
- [ ] Stronger admin-visible privacy metadata and user-private export/redaction.
- [ ] More complete policy explanations and user-facing denial messages.

### Could

- [ ] Phase 8 optional global identity and root-server trust.
- [ ] Phase 9 enterprise and advanced governance.
- [ ] Pairwise global identity bindings.
- [ ] Policy simulation UI.
- [ ] Profile portability between subnets.

### Deferred

- [ ] Cryptographic isolation and secret-store redesign.
- [ ] Mandatory root-server accounts.
- [ ] Autonomous recovery without any prior trust factor.
- [ ] Cross-subnet federation without local owner/admin grants.
- [ ] Quorum/hardware-backed high-security administration.

## Required security regression matrix

These tests should be added across phases as the corresponding surfaces land:

- [x] Expired invite is rejected.
- [x] Reused targeted invite is rejected.
- [x] Public guest join cannot bind a personal profile.
- [x] Revoked guest grant loses live browser/Yjs access.
- [ ] Revoked device loses live browser/Yjs/API access.
- [ ] Child cannot add a device without approval when policy requires it.
- [x] Member can add own device only with `devices.add.self`.
- [x] Member cannot invite users without `users.invite`.
- [ ] Skill cannot read another user's private memory without a grant.
- [ ] Skill cannot write long-term memory without the required policy path.
- [ ] Owner/admin UI can see user-private metadata but not private content.
- [ ] Global identity verification does not grant subnet access by itself.
- [x] Denied tool invocation records a policy decision and reason code.

## Completion definition

This roadmap is complete when:

- a user can be added as member/child/guest through the documented QR/link
  flows;
- owner and co-owner can manage users, devices, memberships, and revocation
  through shared runtime services;
- skills can declare personalization and permission needs in manifests;
- SDK calls expose actor/current-user/subject/service semantics;
- policy enforcement protects profile, memory, device, skill, tool, and
  workspace operations;
- privacy zones are enforced below product UI;
- all access changes and denied decisions produce queryable audit events;
- optional root/global identity can verify identity without bypassing local
  subnet grants.
