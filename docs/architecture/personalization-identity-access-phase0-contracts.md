# Personalization Phase 0 Contracts

Status: implemented contract anchor for Phase 0.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchor:
`src/adaos/domain/personalization_access.py`.

Contract version:
`adaos.personalization_access.contract.v0`.

## Purpose

Phase 0 does not enforce authorization yet. It defines the versioned vocabulary
that later services, SDK helpers, UI surfaces, and tests must use.

The contract deliberately stays in `adaos.domain` so Phase 1 can build a
storage and policy service on top without importing UI, API, or skill runtime
code.

## Scope Lattice

The first scope lattice is:

- `subnet`
- `workspace`
- `webspace`
- `scenario`
- `skill`
- `device_session`
- `user_private`
- `shared_workspace`

Rules:

- `owner` is an implicit subnet administrator.
- Non-owner subjects receive scoped grants.
- `user_private` is a distinct scope from shared workspace data.
- Browser/device sessions are policy subjects and revocation targets, not just
  transport details.

## Versioned Schemas

The Phase 0 contract defines draft schemas for:

- `UserProfile`
- `UserKey`
- `DeviceKey`
- `SessionKey`
- `Membership`
- `Grant`
- `GrantConstraint`
- `Preference`
- `Invite`
- `RecoveryAction`
- `ExternalIdentityBinding`
- `ActorContext`
- `PolicyDecision`
- `AuditRecord`

All records carry `schema_version = adaos.personalization_access.contract.v0`.
Phase 1 storage must persist enough version information to migrate these records
without rewriting unrelated profile, access-link, or conversation-memory state.

## Migration Stance

Existing runtime data maps into this contract as follows:

- `Settings.owner_id` and `local-owner` become the bootstrap owner subject.
- Existing `UserProfileService` settings become `UserProfile.settings`, except
  access policy fields such as `role`, `roles`, `membership`, and `grants` are
  rejected.
- Existing `profile.settings` projections remain compatibility views over the
  future profile/preference service.
- Existing `access_links` become device/session facts and revocation inputs.
- Browser scoped storage becomes user preferences plus device overrides, with
  local fallback during migration.

## Role Presets and Capabilities

Roles are presets, not enforcement primitives. The contract defines the initial
presets:

- `owner`
- `co_owner`
- `admin`
- `member`
- `child`
- `guest`

Capabilities are lower-level strings such as:

- `profile.read.self`
- `profile.write.self`
- `preferences.write.self`
- `users.invite`
- `users.manage`
- `memberships.grant`
- `devices.add.self`
- `devices.add.any`
- `skills.invoke.allowed`
- `tools.invoke.browser_automation`
- `memory.write.skill_user`
- `workspace.read`
- `workspace.write`

Phase 1 may add capabilities, but it should not change the role/profile
separation: role and membership never belong in profile settings.

## Actor Semantics

The contract separates:

- `actor`: authenticated subject performing the action;
- `current_user`: user attached to the current session;
- `subject_user`: user the operation is about;
- `service`: service identity performing background work;
- `on_behalf_of`: user or service whose authority is being used;
- `session` and `device`: concrete technical entry points.

This is required before sensitive conversational flows can propose membership,
device, recovery, or tool actions.

## Join Flow Contracts

The contract names four flows:

- `guest_join_link`
- `targeted_invite_link`
- `device_pairing_link`
- `admin_recovery_link`

`guest_join_link` is public and must not carry profile binding. Targeted invite,
device pairing, and admin recovery are not public and must be auditable.

## Threat Model

The Phase 0 threat model covers:

- public QR abuse;
- targeted invite leakage;
- stolen or lost trusted devices;
- owner or co-owner key loss;
- skill privilege abuse;
- revoked live browser/Yjs/API sessions;
- cross-subnet identity correlation;
- unsafe recovery with no previous trust factor.

The response is not full cryptographic isolation yet. The immediate response is
clear scopes, grants, revocation, policy decisions, and audit records.

## Audit Contract

Audit records are append-only domain facts with:

- actor;
- subject;
- scope;
- device/session;
- source;
- policy decision;
- reason code;
- redacted diff;
- trace id;
- retention hint.

Generic audit logs must not store raw private content. Private data systems may
have separate retention policies, but those are outside the generic access
audit contract.

## Required Local Verification

Phase 0 is locally verified by:

- `python -m pytest tests/test_personalization_access_contracts.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
