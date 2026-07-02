# Personalization, Identity, and Access Roadmap

Status: implementation roadmap and progress tracker.

Target architecture:
[Personalization, Identity, and Access](personalization-identity-access.md).

This roadmap is intentionally phase-gated. The first goal is not to build a
large user-management UI; it is to land the minimum durable identity, policy,
audit, and privacy foundations that make later QR/link and personalization
flows safe to implement.

2026-07-02 critical revision: the first Phase 2 and Phase 3 implementation
passes landed backend/service/SDK slices, not browser-visible product flows.
This roadmap now separates backend readiness from user-facing acceptance. A
checked backend slice does not mean the feature is visible in client settings,
AdaOS Connect, or the Join Browser until the corresponding API/UI phase is
checked.

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
- [x] 2026-07-02: roadmap critically revised after user-facing review; Phase 2
  and Phase 3 are explicitly backend slices, and new must-have phases cover
  Web API/client settings and AdaOS Connect join UX before the roadmap can
  claim visible personalization.

## Execution rules

- Each phase must have schema, service, SDK/API, UI/projection, audit, and test
  implications reviewed before it is marked complete.
- A phase may be checked as a backend slice only when its exit gate says so
  explicitly. Any wording such as "user can", "owner can", "display", or
  "visible" requires API/client integration evidence or an explicit boundary
  note.
- Later design work may proceed in docs, but runtime implementation should not
  skip a gate unless the skip is documented in this roadmap.
- A user-facing flow is not complete until policy enforcement and audit records
  exist below the UI or skill surface.
- `owner` remains a subnet-level technical superuser, but product surfaces must
  still preserve user-private data boundaries.
- Root-server or external identity can verify who a user is; subnet grants
  decide what that identity may do inside the subnet.
- Prefer standard identity patterns where they fit: WebAuthn/passkeys for
  browser public-key authenticators, OAuth device authorization semantics for
  QR/device flows, OIDC/OAuth for external authentication, and SCIM-style
  provisioning for enterprise users and groups.

## Practice Anchors

The AdaOS model is local-first, but it should not invent avoidable identity
machinery:

- [W3C WebAuthn](https://www.w3.org/TR/webauthn-3/) is the reference shape for
  browser-origin-scoped public-key credentials and future passkey-backed user
  or device authenticators.
- [OAuth 2.0 Device Authorization Grant, RFC 8628](https://datatracker.ietf.org/doc/html/rfc8628)
  is the reference interaction model for QR/code flows where one device starts
  and another trusted surface approves or completes the flow.
- [OAuth 2.0, RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749) and
  [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html)
  are the reference model for external authentication and claims. In AdaOS,
  those claims can propose or verify identity; local grants still authorize
  subnet access.
- [SCIM, RFC 7644](https://datatracker.ietf.org/doc/html/rfc7644) is the
  reference pattern for enterprise user/group provisioning. AdaOS should expose
  adapters instead of inventing a separate enterprise directory protocol.
- [NIST SP 800-63B](https://csrc.nist.gov/pubs/sp/800/63/b/upd2/final)
  anchors authenticator lifecycle thinking: enrollment, revocation, loss,
  recovery, and reauthentication are part of the same model.

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

## Phase 2 - Profile and Preferences Backend Slice

Priority: `must`.

Goal: turn current profile/settings mechanisms into the first policy-checked
service and SDK slice without mixing role into profile data. This phase does
not make the feature visible in the browser settings panel by itself.
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
- [x] Add the client-facing header settings service model for display name,
  locale/language, theme, memory/privacy preferences, current subnet/workspace,
  role status, and device trust status.
- [x] Add the service/SDK target for browser-scoped UI preferences as user
  preferences plus device overrides; client localStorage fallback remains a UI
  integration concern.
- [x] Emit redacted audit records for profile and preference updates.

Exit gate:

- [x] Current user can edit self-service profile/preferences through service
  and SDK helpers; browser UI wiring remains outside this phase.
- [x] Role/access preset is visible as status but cannot be edited as a profile
  field.
- [x] Profile/preference updates survive restart and project to Yjs.

Local verification:

- [x] profile SDK tests.
- [x] KV/Yjs projection tests.
- [x] service/SDK header settings smoke test; browser UI wiring remains outside
  this phase and is tracked in Phase 4.

## Phase 3 - Guest Join and Targeted Invite Backend Slice

Priority: `must`.

Goal: implement safe QR/link entry at the backend layer before device pairing,
recovery, and AdaOS Connect UI wiring.
Slice note:
[Personalization Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md).

Checklist:

- [x] Implement `guest_join_link` as public, temporary, scope-limited, and not
  profile-bound.
- [x] Implement `targeted_invite_link` as personal, expiring, one-time, and
  auditable.
- [x] Add `profile_hint` support without treating the hint as proof of identity.
- [x] Provide consent-preview data that joining devices can display: target
  subnet/workspace, role preset, lifetime, and acceptance status.
- [x] Add owner/co-owner flow for binding an unknown session to a new or
  existing local profile.
- [x] Add invite/link rate limits, max session constraints, and bulk guest
  revocation.
- [x] Reject expired, reused, revoked, stale, or wrong-scope invite material.
- [x] Cut off live browser/Yjs access when the backing guest or invite grant is
  revoked, via session revocation and an access-link denial hook.

Exit gate:

- [x] Backend service can create a public guest join and revoke all sessions
  created from it.
- [x] Backend service can create a targeted invite, a subject can accept it
  once, reuse is rejected, and audit records show issuer, subject, scope, role
  preset, and constraints.

Local verification:

- [x] guest join policy tests.
- [x] targeted invite expiry/reuse/revoke tests.
- [x] live session cutoff hook test for revoked guest/invite grant. Browser UI
  and direct websocket disconnect integration are tracked in Phase 5.

## Phase 4 - Current-User Settings API and Browser UI

Priority: `must`.

Goal: make the Phase 2 profile/preference slice visible and usable from the
web client without turning the UI into the source of truth.

Checklist:

- [x] Add shared runtime/API routes for current-user profile, preferences,
  header settings, and policy explanations.
- [x] Load header settings in the browser shell and show display name,
  role/access status, active subnet/workspace, and device trust status.
- [x] Mark current-user identity resolution as owner fallback until session
  identity binding exists for invited/guest users.
- [ ] Add avatar/initial rendering polish for the current-user header chip.
- [x] Add a current-user settings panel for display name, preferred name,
  language/locale/timezone, theme, UI density, memory/privacy preferences, and
  accessibility preferences.
- [x] Back language, locale, timezone, device, and invite-scope controls with
  runtime/API options instead of free-form text where concrete options exist.
- [x] Apply saved theme preferences in the browser shell immediately instead
  of only persisting them server-side.
- [x] Keep role and membership read-only in current-user settings; route any
  access change to owner/admin flows.
- [x] Treat browser localStorage as migration/fallback only. The service store
  remains authoritative for user preferences.
- [x] Show policy denial messages from structured decisions instead of generic
  UI failures.
- [ ] Add an identity switcher only for sessions with multiple valid local or
  external identities; switching identity must be an explicit authenticated
  action and must be audited.
- [x] Add API tests for profile/preference load, edit, refresh, denied edit,
  policy explanation, and audit-covered service writes.
- [x] Add browser build verification for the header/settings panel.

Exit gate:

- [x] A signed-in user can edit allowed profile/preferences in the web UI and
  sees the updated header after reload/restart.
- [x] Role/access status is visible but cannot be edited through profile
  settings.
- [x] API and UI paths route through the Phase 1-2 policy/audit services.

Local verification:

- [x] API tests for current-user profile/preference/header routes.
- [x] Browser build smoke test for settings panel and header refresh.
- [x] Audit smoke test for profile/preference updates and denied edits through
  the Phase 2 service tests.

## Phase 5 - AdaOS Connect Join UX and Link Management

Priority: `must`.

Goal: make Phase 3 join/invite semantics usable through AdaOS Connect and the
Join Browser, following standard device-flow interaction patterns.

Checklist:

- [x] Add API routes for guest join creation, targeted invite creation, invite
  preview, invite claim, invite revoke, and guest-session bulk revoke.
- [x] Parameterize link generation by flow kind, scope, role preset, expiry,
  max sessions, and optional profile hint.
- [x] Generate invite URLs from the public app base with target subnet and
  root/hub endpoint parameters instead of local loopback API origins.
- [ ] Add actual QR rendering for created invite links.
- [x] Let owner/co-owner create a public guest link for classrooms, museums,
  events, demos, and temporary visitors.
- [x] Let owner/co-owner create a targeted invite by entering a local
  profile/user id hint, choosing scope, choosing access preset, and setting
  expiry.
- [ ] Add profile picker/create UX instead of free-form profile id entry.
- [x] Make the joining device show target scope, role preset, expiry, profile
  hint when present, and consent/acceptance action.
- [x] Keep public guest joins session-bound and profile-unbound.
- [x] Make targeted invites one-time by default and visibly unsafe to display
  publicly.
- [x] List pending, accepted, expired, and revoked links for owner/co-owner,
  with revoke actions.
- [ ] Add audit-history drill-down to the link management panel.
- [x] Wire invite/session revocation to access-link denial and browser/Yjs
  admission so revoked sessions are denied without manual database edits.
- [ ] Add direct websocket disconnect orchestration for already-connected
  browser sessions.
- [ ] Model the UX after OAuth device authorization: short-lived material,
  pending/accepted/expired states, user-visible scope, and explicit consent.

Exit gate:

- [x] Owner can display a public guest link and later revoke all sessions
  created from it through AdaOS Connect.
- [x] Owner can invite a named user such as Masha with a local profile id,
  scope, role preset, and expiry; the joining browser can preview and accept
  the invite once.
- [x] Revoked guest/invite sessions lose browser/Yjs admission without manual
  database edits.

Local verification:

- [x] API tests for create/preview/claim/revoke flows.
- [x] Browser build smoke test for guest link and targeted invite panel.
- [x] Revoked-session admission/cutoff test through the access-link runtime
  path, not only the service hook.
- [x] Audit query smoke tests for issuer, subject, scope, role preset,
  constraints, and revoked sessions through Phase 3 service coverage.

## Phase 6 - Device Pairing and Authenticator Lifecycle

Priority: `must`.

Goal: let trusted users add devices and let owner/co-owner recover users
without creating unsafe account-takeover shortcuts.

Checklist:

- [x] Implement `device_pairing_link` for adding a new device to an existing
  trusted user.
- [x] Use the OAuth device-flow interaction shape for pairing: new device opens
  a short-lived link/code, trusted device approves, backend records
  pending/accepted/expired. QR image rendering remains deferred.
- [x] Support member self-service device pairing when policy allows
  `devices.add.self`.
- [x] Require owner/co-owner approval for child device pairing by default.
- [ ] Add optional WebAuthn/passkey-backed device authenticators where browser
  platform support is available; keep local device keys as the AdaOS authority.
- [x] Implement `admin_recovery_link` for owner/co-owner assisted recovery.
- [x] Add lost-device flow: bind replacement device, revoke old device, and
  invalidate active sessions.
- [x] Add device key lifecycle storage, revocation, last-used metadata, and
  session invalidation.
- [ ] Add device key generation and rotation hooks beyond local key refs.
- [ ] Add recovery-code design for users who still have a trusted session or
  device; recovery without any previous trust factor remains deferred.
- [x] Document owner key backup, co-owner recovery, and ownership transfer as
  explicit later work if not implemented in this phase.

Exit gate:

- [x] A user can add a second device through an already trusted device when
  policy allows it.
- [x] Owner/co-owner can bind a replacement device to an existing profile and
  revoke the lost device in the same flow.
- [x] Lost/revoked devices cannot keep active browser/Yjs/API access.

Local verification:

- [x] device pairing tests.
- [ ] child approval tests beyond default policy denial.
- [x] lost-device revoke/session invalidation tests.
- [ ] recovery-code lifecycle tests if recovery codes are included.

## Phase 7 - Owner and Admin User Management Surface

Priority: `must`.

Goal: expose the foundation through a usable owner/admin control surface without
letting the skill or UI become the source of truth.

Checklist:

- [x] Add a shared runtime service API for users, profiles, devices,
  memberships, grants, invites, and recovery actions.
- [x] Add owner/co-owner/admin-facing user management skill or control-plane UI.
- [x] Use access presets for common flows: family member, child, guest, and
  admin. Custom capability editing remains future work.
- [x] Show active grants, expired grants, revoked grants, pending invites,
  devices, sessions, and audit history.
- [x] Allow role preset changes through policy-checked shared services.
- [x] Allow device/session revoke through shared services.
- [x] Show admin-visible privacy metadata without showing private content.
- [ ] Use Pending Actions for sensitive conversational requests such as binding
  a device, granting membership, changing active user, or invoking dangerous
  tools.
- [x] Support multiple administrators explicitly: owner, co_owner, scoped admin,
  workspace admin, device admin, and guest moderator are grants, not profile
  fields.

Exit gate:

- [x] Owner/co-owner can manage users, memberships, devices, invites, and
  revocation without direct database edits.
- [x] All actions route through the shared policy/audit services.
- [x] Admin surfaces make common presets easy while still allowing a future
  expanded capability view.

Local verification:

- [x] API/service tests.
- [x] browser UI build smoke test.
- [ ] dedicated skill action tests.
- [ ] multi-admin grant and denial tests beyond owner/co-owner preset creation.
- [x] audit query smoke tests.

## Phase 8 - Privacy Zone Enforcement and User Data Management

Priority: `must`.

Goal: enforce privacy below UI conventions and give users control over their
own data before multi-user personalization is treated as product-complete.

Checklist:

- [ ] Add service-level data classification for shared workspace data,
  user-private data, admin-visible metadata, and encrypted/private future data.
- [ ] Enforce user-private read/write policy in memory, conversation, profile,
  preference, and projection services.
- [ ] Add user-owned memory/profile search, edit, export, and redaction flows.
- [ ] Add admin-visible metadata views that show existence, usage, policy
  events, and retention without revealing private content.
- [ ] Add retention defaults and redaction audit trails for user-private data.
- [ ] Add compatibility checks so existing owner-superuser paths do not become
  ordinary product read APIs for private user data.
- [ ] Add UI copy and policy explanations for private-data denials that are
  clear to both owner and non-owner users.

Exit gate:

- [ ] Product UI respects privacy zones.
- [ ] Service/API/projection paths enforce the same privacy zones.
- [ ] User can inspect and manage their own private profile/memory data.
- [ ] Owner/admin UI can manage access and retention metadata without exposing
  private content through ordinary product APIs.

Local verification:

- [ ] user-private access tests.
- [ ] admin metadata/no-content tests.
- [ ] export/redaction tests.
- [ ] projection leakage tests.

## Phase 9 - Skill, Tool, and SDK Enforcement

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

## Phase 10 - Optional Global Identity and Root-Server Trust

Priority: `could`.

Goal: add remote trust and cross-device convenience without making root-server
accounts mandatory for local subnets.

Checklist:

- [ ] Add `ExternalIdentityBinding` service and API.
- [ ] Add pairwise public key support to reduce cross-subnet correlation.
- [ ] Add optional root-server verification for targeted remote invites.
- [ ] Add passkey/WebAuthn-backed global identity as an optional authentication
  and recovery provider.
- [ ] Let login choose between known local subnet identity, global identity,
  guest join, and access request.
- [ ] Enforce the rule: root verifies identity, subnet grants access.
- [ ] Treat OIDC/OAuth providers as external identity verifiers, not local
  authorization sources.
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
- [ ] identity-choice login tests.

## Phase 11 - Enterprise and Advanced Governance

Priority: `could`.

Goal: support organization-grade identity and governance without distorting the
local-first household model.

Checklist:

- [ ] Add `TrustProvider` SDK/service interface for OIDC, SAML, LDAP, SCIM, and
  local directory providers.
- [ ] Add first SSO/IdP pilot that maps external claims to proposed local
  memberships and capabilities.
- [ ] Add SCIM-style user/group provisioning adapter for enterprise-managed
  subjects without making SCIM mandatory for household subnets.
- [ ] Add deprovisioning behavior for external users: disable memberships,
  revoke sessions/devices where policy requires it, and emit audit records.
- [ ] Add admin scopes such as workspace admin, device admin, and guest
  moderator.
- [ ] Add policy simulation UI before committing grants.
- [ ] Add time-window constraints for classrooms, museums, events, and guests.
- [ ] Add richer localization for invite, consent, recovery, and denial flows.
- [ ] Add compliance export/reporting only after the core audit model is stable.

Exit gate:

- [ ] External IdP or SCIM provider can propose, but not silently grant, local
  subnet access unless a local policy explicitly allows automatic provisioning
  for that provider and scope.
- [ ] Admin scopes are enforced through the same policy engine as household
  roles.

Local verification:

- [ ] trust-provider contract tests.
- [ ] claim-to-proposed-grant tests.
- [ ] SCIM provision/deprovision adapter tests.
- [ ] admin-scope policy tests.

## Deferred

These items should remain visible but should not block phases 0-11:

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
- [ ] Custom identity federation protocols where OIDC/OAuth, WebAuthn,
  OAuth device authorization, or SCIM already fit the use case.

## MoSCoW coverage

### Must

- [x] Phase 0 foundation contracts.
- [x] Phase 1 subject/session/grant store and policy/audit kernel.
- [x] Phase 2 profile/preferences backend slice.
- [x] Phase 3 guest join and targeted invite backend slice.
- [x] Phase 4 current-user settings API and browser UI.
- [x] Phase 5 AdaOS Connect join UX and link management.
- [x] Phase 6 device pairing and authenticator lifecycle.
- [x] Phase 7 owner/admin user management surface.
- [ ] Phase 8 privacy-zone enforcement and user data management.
- [ ] Phase 9 skill, tool, and SDK enforcement.

### Should

- [ ] Recovery codes generated while a user still has a trusted device.
- [ ] Stronger admin-visible privacy metadata and user-private export/redaction.
- [ ] More complete policy explanations and user-facing denial messages.
- [ ] More complete policy simulation before committing grants.
- [ ] Richer invite, recovery, and denial localization.

### Could

- [ ] Phase 10 optional global identity and root-server trust.
- [ ] Phase 11 enterprise and advanced governance.
- [ ] Pairwise global identity bindings.
- [ ] Policy simulation UI.
- [ ] Profile portability between subnets.
- [ ] SCIM-style enterprise provisioning adapter.

### Deferred

- [ ] Cryptographic isolation and secret-store redesign.
- [ ] Mandatory root-server accounts.
- [ ] Autonomous recovery without any prior trust factor.
- [ ] Cross-subnet federation without local owner/admin grants.
- [ ] Quorum/hardware-backed high-security administration.
- [ ] Custom replacement protocols for mature identity standards.

## Required security regression matrix

These tests should be added across phases as the corresponding surfaces land:

- [x] Expired invite is rejected.
- [x] Reused targeted invite is rejected.
- [x] Public guest join cannot bind a personal profile.
- [x] Revoked guest grant loses live browser/Yjs access.
- [ ] Browser settings UI cannot write role/membership as profile data.
- [ ] Browser settings UI survives refresh/restart and remains backed by the
  shared profile/preference store.
- [ ] Public guest QR is scope-limited and visibly temporary in the joining UI.
- [ ] Targeted invite preview shows subnet/workspace, issuer, role preset,
  expiry, and consent before claim.
- [ ] Revoked device loses live browser/Yjs/API access.
- [ ] Child cannot add a device without approval when policy requires it.
- [x] Member can add own device only with `devices.add.self`.
- [x] Member cannot invite users without `users.invite`.
- [ ] Recovery without owner/co-owner, trusted device, recovery code, passkey,
  or external identity provider is rejected.
- [ ] Skill cannot read another user's private memory without a grant.
- [ ] Skill cannot write long-term memory without the required policy path.
- [ ] Owner/admin UI can see user-private metadata but not private content.
- [ ] Global identity verification does not grant subnet access by itself.
- [ ] External IdP/SCIM claim cannot silently grant local access unless local
  policy explicitly allows automatic provisioning for that provider and scope.
- [x] Denied tool invocation records a policy decision and reason code.

## Completion definition

This roadmap is complete when:

- a user can be added as member/child/guest through the documented QR/link
  flows;
- current-user profile/preferences are visible and editable in the browser
  settings surface through shared policy/audit services;
- AdaOS Connect can create, preview, claim, list, and revoke guest and targeted
  links without direct database edits;
- owner and co-owner can manage users, devices, memberships, and revocation
  through shared runtime services;
- a user can add a trusted device or recover through a documented prior-trust
  factor;
- skills can declare personalization and permission needs in manifests;
- SDK calls expose actor/current-user/subject/service semantics;
- policy enforcement protects profile, memory, device, skill, tool, and
  workspace operations;
- privacy zones are enforced below product UI;
- all access changes and denied decisions produce queryable audit events;
- optional root/global identity can verify identity without bypassing local
  subnet grants.
