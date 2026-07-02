# Personalization, Identity, and Access Roadmap

Status: implementation roadmap.

Target architecture:
[Personalization, Identity, and Access](personalization-identity-access.md).

This roadmap uses MoSCoW categories:

- `must`: required to make the architecture coherent and safe for the first
  product slice;
- `should`: important follow-up work that materially improves usability,
  observability, or safety;
- `could`: useful extensions that should not block the first implementation;
- `deferred`: intentionally postponed work that needs architectural room now.

## Must

- [ ] Define canonical schemas for `UserProfile`, `UserKey`, `DeviceKey`,
  `SessionKey`, `Membership`, `Grant`, `Capability`, `Preference`, and
  `ExternalIdentityBinding`.
- [ ] Keep `role` out of profile settings; model role as a scoped membership or
  grant attribute.
- [ ] Introduce capability vocabulary for profile, preferences, users, devices,
  skills, tools, memory, and workspace access.
- [ ] Define initial role presets: `owner`, `co_owner`, `admin`, `member`,
  `child`, and `guest`.
- [ ] Treat `owner` as subnet-level technical superuser with implicit admin
  access across subnet scopes.
- [ ] Add first-class `co_owner` or equivalent recovery/admin role.
- [ ] Separate `actor`, `current_user`, and `subject_user` in policy decisions,
  audit events, and SDK terminology.
- [ ] Split join flows into distinct types: `guest_join_link`,
  `targeted_invite_link`, `device_pairing_link`, and `admin_recovery_link`.
- [ ] Make targeted invite links expiring, one-time, and auditable.
- [ ] Ensure public guest links do not imply personal profile binding.
- [ ] Add owner/co-owner flow for binding an unknown session to a new or
  existing local profile.
- [ ] Add device revocation semantics for lost or replaced devices.
- [ ] Define privacy zones: shared workspace data, user-private data,
  admin-visible metadata, and future encrypted private data.
- [ ] Prevent normal admin/owner UI from browsing user-private memory and
  conversation content by default.
- [ ] Move client header settings toward a current-user profile/preferences
  surface.
- [ ] Expose current role/access preset as status in user settings, not as an
  editable profile field.
- [ ] Create a user-management skill or control-plane surface for owners/admins.
- [ ] Route user-management actions through shared runtime services, not
  skill-local state.
- [ ] Define audit events for profile changes, preference changes, invites,
  memberships, roles, capability decisions, device pairing, revocation, and
  recovery.
- [ ] Store redacted diffs and policy decisions in audit records; avoid raw
  private content in generic audit logs.
- [ ] Extend skill manifest vocabulary for declared personalization usage and
  required/optional permissions.
- [ ] Extend SDK vocabulary with `ctx.actor`, `ctx.current_user`,
  `ctx.profile`, `ctx.preferences`, `ctx.require`, and explicit selected/subject
  user semantics.
- [ ] Add tests for profile updates through the SDK, KV/Yjs projection, and
  audit event emission.
- [ ] Add tests for role/capability denial on at least one sensitive tool path.

## Should

- [ ] Add preset-based owner UX for access grants: family member, child, guest,
  admin, and custom.
- [ ] Add constraints to grants: `expires_at`, `requires_approval_for`,
  `child_mode`, allowed workspace ids, and allowed skill/tool classes.
- [ ] Support member self-service device pairing when policy allows
  `devices.add.self`.
- [ ] Require owner approval for child self-service device pairing.
- [ ] Add one-step lost-device flow: bind replacement device and offer revoking
  old devices/sessions.
- [ ] Add a read-only user/device/membership inventory projection for the
  owner/admin UI.
- [ ] Connect access-link/device inventory with user profile and membership
  records.
- [ ] Normalize browser-scoped UI preferences into user preferences plus device
  overrides, while keeping localStorage fallback.
- [ ] Add policy explanations to denied tool/skill calls.
- [ ] Add pending actions for sensitive conversational requests such as
  changing active user, binding a device, granting a membership, or invoking a
  dangerous tool.
- [ ] Add child-mode defaults for memory writes, browser automation, external
  communication, and device pairing.
- [ ] Add temporary guest access with automatic expiry and visible session
  status.
- [ ] Add user-private memory search/edit UI for the owning user.
- [ ] Add admin-visible privacy metadata without exposing private content.
- [ ] Add import/export and redaction flows for user profile and memory data.
- [ ] Add optional recovery codes generated while the user still has a trusted
  device.
- [ ] Add policy decision tests for public guest joins, targeted invites,
  device pairing, and admin recovery.

## Could

- [ ] Add pairwise global identity bindings so one global account can use
  different subnet-specific public keys.
- [ ] Add optional root-server backed invite verification for remote targeted
  invites.
- [ ] Add passkey-backed global identity as a recovery provider.
- [ ] Add enterprise `TrustProvider` implementations for OIDC, SAML, LDAP, or
  domain-specific directories.
- [ ] Add multiple admin scopes such as workspace admin, device admin, and
  guest moderator.
- [ ] Add delegation policies such as "Masha can add her own devices but cannot
  invite users".
- [ ] Add time-window constraints for guests, children, classrooms, museums, and
  events.
- [ ] Add policy simulation UI: show what a user/role/device can do before
  committing a grant.
- [ ] Add richer localization for invite, consent, recovery, and denial flows.
- [ ] Add profile portability tooling between subnets with explicit owner
  acceptance in the destination subnet.

## Deferred

- [ ] Full cryptographic isolation of user-private data from the subnet owner.
- [ ] Secret-store redesign for per-user and per-device encryption keys.
- [ ] Root-server mandatory account system.
- [ ] Fully autonomous recovery with no owner, co-owner, trusted device,
  recovery code, or external identity provider.
- [ ] Cross-subnet federation where a remote subnet can grant access without a
  local owner/admin decision.
- [ ] Fine-grained enterprise compliance reporting beyond the core audit event
  model.
- [ ] Quorum-based administration for high-security deployments.
- [ ] Hardware-backed key management requirements for all trusted devices.
- [ ] Mature SSO group-to-capability synchronization and deprovisioning.

## Recommended sequence

- [ ] Phase 0: freeze vocabulary and schemas for profile, identity, device,
  membership, grants, capabilities, preferences, and audit.
- [ ] Phase 1: land current-user profile/preferences settings in the client and
  SDK, backed by existing profile settings storage and projections.
- [ ] Phase 2: implement owner/co-owner user-management surface with local
  profiles, targeted invites, public guest joins, and device revocation.
- [ ] Phase 3: introduce policy evaluation for role presets, capabilities, and
  constraints on sensitive skill/tool/data paths.
- [ ] Phase 4: add device pairing and admin recovery flows, including lost-device
  revocation.
- [ ] Phase 5: add privacy-zone enforcement in UI and memory surfaces, with
  user-private data hidden from normal admin browsing.
- [ ] Phase 6: add optional root-server/global identity bindings and pairwise
  key support.
- [ ] Phase 7: add enterprise trust-provider SDK and first SSO/IdP pilot.

## Completion definition

This roadmap is complete when:

- a user can be added as member/child/guest through the documented QR/link
  flows;
- owner and co-owner can manage users, devices, memberships, and revocation
  through shared runtime services;
- skills can declare personalization and permission needs in manifests;
- SDK calls expose actor/current-user/subject semantics;
- policy enforcement protects at least profile, memory, device, skill, and tool
  operations;
- privacy zones are respected by product UI;
- all access changes and denied decisions produce auditable events.
