# Personalization, Identity, and Access

Status: target architecture and vocabulary anchor.

This document defines the target model for personalization, user identity,
device enrollment, roles, access grants, and observability in AdaOS. It extends
the existing personalization concept with an explicit local-first trust model
and a roadmap-compatible authorization vocabulary.

The implementation roadmap lives in
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

## Goals

AdaOS should support household, classroom, museum, and small-team flows without
making global cloud identity mandatory. The user-facing experience can stay
QR/link-first, while the platform model must keep identity, devices,
membership, policy, and personal data boundaries separate.

The target architecture must:

- keep the subnet usable without the root server;
- treat the subnet owner as the local technical superuser;
- let non-owner users have private data and scoped memberships;
- support public guest joins, targeted invites, device pairing, and recovery;
- make access decisions through roles, capabilities, constraints, and audit;
- leave room for optional root-server identity, SSO, and secret isolation.

## Current implementation baseline

AdaOS already has several pieces that this architecture builds on:

- `UserProfileService` stores MVP profile settings and emits
  `user.profile.changed`.
- Scenario data projections can route `current_user/profile.settings` into KV
  and Yjs.
- Conversation memory has scoped records, consent state, pending write review,
  redaction, and audit events.
- Access-link/device work already models browser and member endpoint access,
  lifetime, revocation, and device observability.
- Named entities and aliases provide localized human-facing names.
- The browser client has subnet-scoped UI preferences for several settings.
- Phase 0 contracts define the shared scope, subject, role, capability, invite,
  recovery, decision, and audit vocabulary.
- Phase 1 implements a reusable backend access kernel for persisted identity
  facts, session/device-aware policy decisions, revocation facts, replay guards,
  and audit queries.
- Phase 2 keeps profile settings compatibility while adding versioned profile
  and preference records, current-user SDK helpers, header settings, and
  redacted profile/preference audit.
- Phase 3 implements backend guest join and targeted invite flows with consent
  preview data, scoped claims, grant issuance, bulk revocation, and
  session/access-link cutoff hooks.

Those mechanisms are useful foundations, but they do not yet form a single
end-to-end profile UI, join flow, API middleware, and SDK enforcement surface.

## Trust model

### Subnet root of trust

Every subnet has a local trust root. Inside the subnet, this root is the
authoritative issuer for:

- owner and co-owner grants;
- user keys;
- device keys;
- browser session keys;
- workspace memberships;
- capability grants;
- revocation records.

The root server can help discover, route, or verify external identities, but it
does not grant access to a subnet by itself. Subnet access is always a decision
made by the subnet owner, co-owner, admin policy, or an already trusted device
acting under a grant.

### Owner as subnet superuser

`owner` is a subnet-level technical superuser, not a normal workspace role. The
owner has effective administrative access because they control the hub machine,
server files, source code, and local runtime.

The product UI should still distinguish:

- technical administration;
- ordinary read access to another user's private data;
- policy decisions such as inviting users and granting capabilities.

This lets AdaOS start with an honest owner-superuser model while preserving a
future path toward encrypted private data and stronger separation.

### Optional external trust providers

Root-server identity and enterprise SSO are optional trust providers. They can
verify that an external public key or account belongs to a user, but the subnet
still decides whether that identity is trusted.

External providers should implement a common provider shape:

```text
TrustProvider
  verify_identity(assertion)
  map_claims_to_subjects(assertion)
  propose_membership_grants(subject)
```

Examples:

- `inimatic_root`;
- OIDC/SAML/LDAP enterprise IdP;
- future local directory service.

## Core objects

### User profile

A `UserProfile` is local user-facing data:

- display name and preferred name;
- locale, language, timezone;
- avatar or visual identity;
- self-editable profile fields;
- personal preferences that are not access policy.

The profile is not proof of identity. It is a human-facing record attached to a
user subject.

### User key

A `UserKey` is the cryptographic identity of a user inside a subnet. A profile
can be created before the user has a trusted key, but access grants should bind
to keys, devices, or sessions, not only to names.

### Device key

A `DeviceKey` identifies a trusted device or endpoint for a user. A phone,
laptop browser, ReDevice endpoint, or future agent endpoint can each have its
own key and lifecycle.

### Session key

A `SessionKey` identifies a short-lived browser or endpoint session. It can be
anonymous, guest-scoped, or attached to a user/device after approval.

### Membership

A `Membership` connects a subject to a scope:

```text
subject: user:masha
scope: workspace:family
role: member
status: active
```

Membership is scoped. A user can be a member in one workspace and a guest in
another. The subnet owner has implicit admin access across subnet scopes.

### Grant

A `Grant` is an authorization record. It can be role-based, capability-based,
or constrained:

```yaml
grant:
  subject: user:masha
  scope: workspace:family
  role: member
  capabilities:
    - profile.read.self
    - profile.write.self
    - devices.add.self
    - skills.invoke.allowed
  constraints:
    expires_at: null
    requires_owner_approval_for:
      - users.invite
      - tools.invoke.browser_automation
    child_mode: false
```

### Preference

Preferences are settings selected by or for a user. They should not contain
membership or role state. Examples:

- theme;
- language;
- UI density;
- assistant display name;
- privacy and memory preferences;
- accessibility preferences.

Device-specific overrides can be layered on top of user preferences.

## Roles and capabilities

Roles are human-facing presets. Capabilities are the enforcement vocabulary.

Recommended starting roles:

- `owner`: subnet superuser and root administrator;
- `co_owner`: trusted administrator for recovery and user management, without
  necessarily owning the server machine;
- `admin`: manages users, workspaces, skills, and policies within assigned
  scopes;
- `member`: trusted named user;
- `child`: named user with stricter defaults and owner approval constraints;
- `guest`: temporary or limited subject, often session-bound.

`invited` should be treated as an invitation status, not as a role:

- `pending`;
- `accepted`;
- `expired`;
- `revoked`.

Capabilities should be explicit and stable enough for SDK, skill manifests,
tool invocation, and audit:

```text
profile.read.self
profile.write.self
profile.read.members
users.invite
users.manage
memberships.grant
devices.add.self
devices.add.any
skills.invoke.allowed
skills.install
tools.invoke.browser_automation
memory.read.self
memory.write.self
memory.write.skill_user
workspace.read
workspace.write
```

Role presets expand to capabilities plus constraints. Advanced UI can expose
the expanded capability set later, but normal owner flows should present simple
presets.

## Join and login flows

AdaOS should support four different QR/link flows. They must not be collapsed
into one generic "join" action because they carry different security semantics.

### Public guest join

Use case: lecture hall, museum, public kiosk, temporary demo.

Properties:

- QR or link can be displayed publicly;
- joins as `guest` or `visitor`;
- grants are temporary and scope-limited;
- no personal profile binding is implied;
- the joining device must still see what subnet/workspace it is joining.

### Targeted invite

Use case: owner invites Masha as a family member or workspace member.

Properties:

- invite is personal, one-time, and expires;
- invite can include `profile_hint` or a preselected local profile;
- the joining device accepts the invite;
- final membership is recorded with issuer, subject, scope, role preset, and
  constraints.

Targeted links must not be displayed publicly. Whoever claims such a link may
become the intended user unless an additional proof or confirmation is required.

### Device pairing

Use case: Masha is already signed in on her phone and wants to add a PC.

Properties:

- new device shows QR;
- already trusted device signs or approves the new device key;
- policy decides whether owner approval is also required;
- resulting device is attached to the same user profile and memberships.

Useful policies:

- members may add their own devices;
- children require owner approval;
- sensitive workspaces require admin approval.

### Admin recovery

Use case: Masha lost her phone and needs a new trusted device.

Properties:

- owner or co-owner scans the new device's QR;
- existing profile is selected;
- new device key is attached to the profile;
- old device can be revoked in the same flow;
- the recovery action is audited as privileged.

Without owner/co-owner, another trusted device, a recovery code, or external
identity provider, secure recovery is not possible. A "simple" fallback with no
previous trust factor would allow profile takeover.

## Global and subnet identities

AdaOS should keep local subnet identity as the default and allow global
identity as an optional binding.

```text
LocalProfile
  subnet_user_id
  display_name
  local_user_key

ExternalIdentityBinding
  provider
  external_subject_id
  external_public_key
  pairwise_public_key
  bound_by
  bound_at
```

Important rule:

```text
Root server verifies identity.
Subnet grants access.
```

When a user has both local and global identity, the login UI may need to offer:

- continue as a known local subnet user;
- use global identity;
- join as guest;
- request access.

To reduce cross-subnet correlation, the architecture should support pairwise
keys: one global account can present different subnet-specific public keys,
with the root server verifying the relationship only when needed.

## Personal data zones

The data model should distinguish visibility even before cryptographic
isolation is implemented.

### Shared workspace data

Data intentionally shared within a workspace:

- shared scenario state;
- collaborative documents;
- public skill outputs;
- workspace-level settings.

### User-private data

Data owned by a user:

- personal memory;
- personal conversation history;
- private preferences;
- private notes and profile details.

Owner/admin UI should not casually expose user-private content. Metadata,
policy events, storage usage, and revocation controls can be admin-visible.

### Secret and encrypted data

Future stronger isolation:

- user-held encryption keys;
- secret store access scoped to user/device;
- encrypted private memory;
- owner cannot decrypt through ordinary product APIs.

This is deferred for implementation, but the architecture should avoid baking
in product-level read access to every user's private data.

## Actor, current user, and subject user

Runtime and SDK calls must not confuse the person acting with the person being
edited or discussed.

```text
actor
  authenticated subject that performs the action

current_user
  user attached to the current client/session

subject_user
  user that an operation is about
```

Examples:

- Masha edits her own language: `actor == current_user == subject_user`.
- Owner binds a new device to Masha: `actor == owner`, `subject_user == Masha`.
- A skill writes memory for the active agent/user pair: actor and subject must
  be explicit in the write policy.

Conversational commands may propose privileged actions, but must not silently
change actor identity or complete sensitive grants without confirmation.

## Client settings surface

The client header settings should become the normal entry point for the current
user's profile and preferences.

It should expose:

- display name and avatar;
- language and locale;
- theme and UI density;
- personal memory/privacy preferences;
- current subnet/workspace;
- current role or access preset as read-only status;
- device trust status.

It should not treat role as a profile field. Role and membership belong to
access policy.

## User management skill

AdaOS should provide an owner/admin-facing user management skill or control
plane surface. It should operate through shared runtime services, not own the
authorization model.

Capabilities:

- list users, devices, memberships, and pending invites;
- create targeted invites;
- approve public guest joins when required;
- bind a session to an existing profile;
- create local profiles;
- change role presets;
- revoke devices and sessions;
- inspect policy decisions and audit trails;
- manage child-mode constraints and temporary access expiry.

## SDK and manifest surface

Skills need a stable contract for personalization and access-aware behavior.

Target SDK shape:

```python
ctx.actor.id
ctx.actor.roles
ctx.actor.capabilities

ctx.current_user.id
ctx.profile.get()
ctx.profile.update(patch)

ctx.preferences.get("theme")
ctx.preferences.set("theme", "dark")

ctx.require("memory.write.skill_user")
ctx.selected_user.id
ctx.webspace.id
```

Target manifest shape:

```yaml
personalization:
  uses:
    - profile.locale
    - profile.preferred_name
    - preferences.theme
  variants:
    by_role: true
    by_user: true
    by_device: false

permissions:
  required:
    - profile.read.self
  optional:
    - memory.write.skill_user
    - devices.add.self
```

The SDK should let a skill declare that it adapts by role, user, device, or
workspace without letting the skill bypass policy checks.

## Observability and audit

Personalization needs explicit observability because profile changes and grants
affect trust.

Recommended events:

```text
profile.updated
preferences.updated
join.requested
join.approved
invite.created
invite.accepted
invite.revoked
membership.granted
membership.revoked
role.changed
capability.granted
capability.denied
device.paired
device.revoked
recovery.started
recovery.completed
auth.session.started
auth.session.switched
memory.write.proposed
memory.write.committed
memory.forgotten
tool.invocation.allowed
tool.invocation.denied
```

Audit records should include:

- actor;
- subject;
- scope;
- device/session;
- source skill or client surface;
- request id and trace id;
- policy decision;
- redacted diff for profile/preference changes;
- expiration and constraints for grants.

Audit logs must avoid storing raw private content unless a specific subsystem
has a retention policy for it.

## Target architecture summary

The clean model has four layers:

```text
Identity
  local profile, local user key, optional global/external identity

Device
  browser session, trusted device key, endpoint access link

Membership
  subject access to subnet/workspace scopes

Policy
  role presets, capabilities, constraints, approvals, audit
```

The default UX should stay simple:

```text
QR/link -> approve or accept -> choose profile/access preset -> record grants
```

The platform contract underneath must stay precise enough to support privacy,
delegation, child profiles, temporary guests, multiple administrators, external
identity, and future secret isolation.
