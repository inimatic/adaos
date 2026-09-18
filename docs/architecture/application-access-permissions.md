# Application Access, Permissions, and Roles

Status: target architecture with the V1 non-deferred vertical slice implemented.

Last reviewed: 2026-09-18.

This document defines the target AdaOS architecture for Application-level
permissions, Application-defined roles, per-user and guest access, child-safe
constraints, secrets and external account consent, conversational approval
surfaces, and access observability.

[Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md)
owns Application identity, release, install, update, subscription, and
publication lifecycle. [Personalization, Identity, and Access](personalization-identity-access.md)
owns local users, profiles, devices, sessions, memberships, grants, privacy
zones, and audit facts. This page owns the bridge between them:

```text
Application declares what it may do
  -> owner grants who may use it and in which Application role
  -> runtime checks subject, app, role, resource, device, and approval policy
  -> Applications and Users & Access expose observability and revocation
```

The implementation sequence lives in
[Application Access, Permissions, and Roles Roadmap](application-access-permissions-roadmap.md).
Fast reads of release-bound permission profiles, flat compatibility
permissions, Application role declarations, role matrices, validation state,
and Builder permission-profiler inputs are indexed by
[Application Registry Projection](application-registry-projection.md). This
page still owns effective authorization, dynamic grant freshness, revocation,
and audit.

## Current Baseline

AdaOS already has useful lower-level foundations:

- Application releases already carry a flat permission list through
  `ProjectRelease.permissions`, compatibility summaries, and install/update
  operation plans.
- Runtime tool calls can declare side effects and durable `approval_scope`
  boundaries. A first approved Pending Action can become a reusable grant for
  the same subject, action, resource, and webspace.
- Skill manifests have component-level capability admission through
  `skill.yaml` and the skill capability grant profile.
- Personalization access already models `owner`, `co_owner`, `admin`,
  `member`, `child`, and `guest`, together with subjects, scopes, grants,
  memberships, invites, device trust, session keys, constraints, data zones,
  and audit.
- Guest joins are session-bound and profile-unbound by default. Targeted
  invites are one-time by default and show scope, role preset, expiry, and
  acceptance before claim.
- The Applications DEV prototype now has permission, access, role, connected
  account, release-readiness, activity, and Users & Access surfaces over the
  shared access service.
- Secrets remain in the vault and per-skill runtime injection mechanism;
  Application permission profiles and connected-account metadata now govern
  declared purpose, provider, scope, state, and redacted observability.

The shared product and policy contract now turns those foundations into an
Application-level permission profile and per-subject access model. Method-level
prompts such as `skill_name:tool_name (network)` remain valuable as fallback
enforcement detail, but they are no longer the normal consent experience for a
covered Application grant.

## Current Implementation Boundary

As of 2026-09-16, AdaOS has the complete non-deferred V1 slice:

- release-bound `ApplicationPermissionProfile` and `application_roles`;
- deterministic permission-profile and role-model digests;
- ABI schemas for permission profiles, access grants, access decisions,
  access-profile diffs, and Builder verification reports;
- Application-scoped grants with permission ceilings, explicit denies,
  expiry, constraints, reviewed-profile digest, revoke state, policy
  explanations, actor-chain fields, child floors, guest floors, resource scope,
  webspace scope, and trusted device/session constraints;
- Application access audit records for grant, revoke, and allow/deny/pending
  decisions;
- Applications SDK helpers for listing, granting, revoking, deciding, and
  auditing Application access;
- registry-projection indexes for permission profiles and Application roles
  from DEV project manifests and ApplicationStore releases;
- Builder verification report generation for profile schema, declared versus
  observed capabilities, role declarations, regression evidence, access-matrix
  evidence, Pending Action fallback evidence, and auditability evidence.
- verified Application context and grant evaluation in the common runtime tool
  bridge before method-level approval fallback;
- one management service exposed through API, SDK, Root MCP, Applications,
  Users & Access, and conversational entry points;
- connected-account lifecycle, privacy and anomaly projections, access review,
  policy simulation, snapshots, role templates, and content-redacted user
  diagnostics;
- responsive EN/RU DEV surfaces and Application-aware Pending Action routing
  for chat/Telegram keyboard and trusted-device voice handoff;
- Builder CLI/UI Final Verification, release evidence bundles,
  in-toto-compatible statement digests, and Trial/publication admission gates;
- a release-bound AAPR6 proof and reproducible machine evidence in
  [`docs/evidence/application-access-v1-20260916.md`](../evidence/application-access-v1-20260916.md).

## Standard Practice Anchors

AdaOS is local-first, but the user and developer model should align with
established authorization patterns:

- Android separates manifest declaration, contextual runtime requests, repeated
  permission checks, graceful denial handling, privacy indicators, and runtime
  notification permission. See
  [Android runtime permissions](https://developer.android.com/training/permissions/requesting)
  and [Android notification permission](https://developer.android.com/develop/ui/compose/notifications/notification-permission).
- Chrome extensions declare required and optional permissions, host
  permissions, and warning-level update changes in the extension manifest. See
  [Chrome declare permissions](https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions)
  and [permission warning guidelines](https://developer.chrome.com/docs/extensions/develop/concepts/permission-warnings).
- GitHub Apps have no API permissions by default, ask for minimum permissions,
  show requested permissions during install, intersect app and user authority,
  and require owner approval when permissions change. See
  [GitHub App permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app).
- Slack scopes distinguish bot identity from user identity and require
  reauthorization when new scopes are added. See
  [Slack scopes](https://docs.slack.dev/reference/scopes/).
- Google OAuth policy requires least-access scopes, secure token handling,
  accurate app identity, scope changes in configuration, and revocation when
  access is no longer needed. See
  [Google OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies).
- Microsoft identity distinguishes delegated permissions from application
  permissions and treats consent, admin consent, and application review as
  first-class governance. See
  [Microsoft permissions and consent](https://learn.microsoft.com/en-us/entra/identity-platform/permissions-consent-overview).
- OAuth Rich Authorization Requests provide structured
  `authorization_details` beyond coarse scopes. OAuth Resource Indicators bind
  grants to resource audiences. OAuth Token Exchange provides an actor chain
  model for delegated or on-behalf-of execution. DPoP demonstrates
  proof-of-possession token binding. See
  [RFC 9396](https://www.rfc-editor.org/rfc/rfc9396.html),
  [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html),
  [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693.html), and
  [RFC 9449](https://www.rfc-editor.org/rfc/rfc9449.html).
- OWASP recommends least privilege, deny by default, authorization checks on
  every request, logging, tests, and early preference for ABAC/ReBAC over
  plain RBAC where object relationships matter. See
  [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
  and [OWASP Multi-Tenant Security](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html).
- AI agents need explicit tool mediation, high-impact action approval, prompt
  injection resistance, memory/data-exfiltration defenses, and monitoring. See
  [OWASP AI Agent Security](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html).
- Secure software development practice treats verification, security tests,
  traceable evidence, and release readiness as development artifacts rather
  than informal memory. See
  [NIST SP 800-218 SSDF](https://csrc.nist.gov/pubs/sp/800/218/final) and
  [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/).
- Software supply-chain practice uses provenance and attestations so a release
  consumer can inspect how an artifact was produced and verified. AdaOS does
  not need full marketplace signing in V1, but the report shape should be
  compatible with that direction. See [SLSA](https://slsa.dev/spec/v1.2/)
  and [in-toto](https://in-toto.io/).
- Release platforms and source-control systems increasingly make pre-release
  checks visible and enforceable through pre-launch reports, required status
  checks, and review gates. See
  [Google Play pre-launch reports](https://play.google.com/console/about/pre-launchreports/)
  and
  [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
- Sensitive approvals should be able to require user presence, authentication
  intent, and reauthentication. See
  [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html).
- App marketplaces expose privacy practices and data collection labels before
  install and on update. See
  [Apple App Privacy details](https://developer.apple.com/app-store/app-privacy-details/)
  and [Google Play Data safety](https://support.google.com/googleplay/android-developer/answer/10787469).
- Child access needs explicit guardian control, high privacy defaults, data
  minimization, and revocation. See the
  [COPPA Rule](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-312)
  and the [ICO Children's code](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/childrens-information/childrens-code-guidance-and-resources/age-appropriate-design-a-code-of-practice-for-online-services/).
- Relationship-based authorization systems such as Zanzibar and OpenFGA show
  why role assignments should be stored as relationship facts, not hidden
  application-local profile fields. See
  [Google Zanzibar](https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/)
  and [OpenFGA concepts](https://openfga.dev/docs/concepts).
- Cedar-style policy languages are useful long-term references for
  policy-as-code, but AdaOS should not require a full external policy engine
  for the first local-first Application proof. See
  [Cedar](https://docs.cedarpolicy.com/).

## Design Principles

1. User consent is Application-scoped; enforcement remains action, resource,
   actor, and component scoped.
2. A skill manifest declares component capabilities. The Application
   declaration composes them into a product-level permission profile.
3. Profiles are identity and preference records. Access policy lives in grants,
   memberships, role assignments, constraints, consent, and audit facts.
4. Deny by default. Validate on every request. Explicit denies, revocation,
   expired grants, child floors, guest floors, and tenant/resource mismatch win
   over broad allows.
5. Application roles are user-facing relation presets inside one Application.
   They are not platform roles and are not sufficient authority by themselves.
6. The platform access service is the source of truth. Applications may request
   role changes or show embedded management UI, but final grants are issued and
   audited by AdaOS.
7. The same grant model must serve Applications, Users & Access, Pending
   Actions, Builder, application settings, chat, Telegram, and voice routes.
8. Secrets and connected accounts are managed in the same access surface, but
   secret presence is not permission. A grant authorizes a subject/application
   to use a secret for a declared purpose; the secret value remains hidden.
9. Declared behavior and observed behavior are both first-class. Builder and
   runtime must surface undeclared observed access as a defect or release gate.
10. Child and guest access is not a cosmetic role label. It changes default
    permissions, approval routing, retention, device trust, and data exposure.

## Core Terms

`ApplicationPermissionProfile`
: Versioned, release-bound declaration of the permissions, data practices,
  external providers, secrets, notifications, LLM/model usage, background work,
  and high-impact actions an Application may require or optionally request.

`PermissionCapability`
: Stable machine-readable token used by enforcement, such as `workspace.read`,
  `workspace.write`, `llm.generate`, `network.egress`, `secrets.use`,
  `notifications.send`, or `app.task.complete`.

`AuthorizationDetails`
: Structured permission detail inspired by OAuth RAR: actions, resources,
  datatypes, locations/providers, purpose, retention, recurrence, and risk.

`ApplicationRole`
: Role declared by an Application release, such as `viewer`, `editor`,
  `assignee`, `student`, `teacher`, `reviewer`, or `moderator`. It expands to
  app capabilities and optional permission requirements, but is assigned and
  enforced by the platform.

`ApplicationAccessGrant`
: Policy fact that binds a subject to an Application scope, Application roles,
  permission ceilings or denies, constraints, expiry, issuer, and the release
  permission profile digest it was reviewed against.

`ApplicationAccessProfile`
: Owner-facing preset that combines a platform role ceiling with Application
  roles and constraints for common cases: owner full access, member editor,
  child supervised, guest readonly, temporary classroom participant, and so on.

`Users & Access`
: Target product Application for observing and managing users, guests,
  children, devices, sessions, memberships, Application access, Application
  roles, approvals, and audit across the subnet.

`Pending Action`
: Durable request for a human decision. It is used when an operation is not
  covered by an existing Application grant, requires one-time or step-up
  approval, changes sensitive policy, or needs guardian approval.

`ApplicationVerificationReport`
: Versioned release-bound evidence produced by Builder final verification. It
  records check ids, gate levels, results, evidence refs, permission/profile
  digests, observed-capability digests, test commands, manual attestations,
  residual risks, and whether the candidate may enter Trial, publication, or
  install/update review.

## Target Declaration Shape

The first version should be intentionally small but structured enough to avoid
flat-scope lock-in:

```yaml
permission_profile:
  schema: adaos.application.permission_profile.v1
  required:
    - id: llm.generate
      title: Generate content with an LLM
      purpose: Generate project README and release notes
      authorization_details:
        actions: [generate]
        datatypes: [project_metadata, source_summaries]
        resources: [root_llm_proxy]
        retention: transient
        recurrence: user_initiated
      approval_policy: grant_on_install
    - id: workspace.write
      purpose: Save generated application metadata
      authorization_details:
        actions: [read, write]
        resources: [application_source]
  optional:
    - id: notifications.send
      purpose: Notify assigned users about completed review
      approval_policy: ask_in_context
  secrets:
    - id: github_token
      provider: github
      scopes: [contents.read]
      purpose: Import release metadata from a repository
      required: false
  data_practices:
    collected: [user_content, usage_data]
    sent_off_device: [project_metadata]
    linked_to_user: [usage_data]
    tracking: false
```

Flat `permissions` remains a compatibility projection derived from this
profile. New release digests should eventually bind the structured profile and
its digest directly.

## Application Roles

Applications may declare their own role model, but the platform issues the
assignments:

```yaml
application_roles:
  - id: viewer
    title: Viewer
    grants: [app.view]
    assignable_to: [owner, co_owner, admin, member, child, guest]
    default_for:
      guest: viewer

  - id: editor
    title: Editor
    grants: [app.view, app.write]
    assignable_to: [owner, co_owner, admin, member]
    default_for:
      owner: editor
    requires_permissions: [workspace.write]

  - id: moderator
    title: Moderator
    grants: [app.view, app.comment.hide, app.user.warn]
    assignable_to: [owner, co_owner, admin, member]
    sensitive: true
```

Rules:

- Role ids are immutable within a release and versioned with the Application.
- A role-bearing Application declares exactly one owner-compatible
  `default_for.owner`. Builder may use a unique maximal owner-compatible role
  only for a legacy release; incomparable owner roles block Trial placement
  until the declaration is explicit.
- Unknown roles and unknown app capabilities fail closed.
- A role grants only Application-local capabilities. Platform capabilities
  still require platform grants.
- Role assignments are relationship facts:

  ```text
  user:masha has assignee on application:family_tasks
  session:guest-123 has viewer on application:event_dashboard until T
  ```

- Role model changes are update-impacting. If a role gains broader
  permissions, update requires review. If a role is removed, affected users
  require owner remapping or fail closed.
- App-embedded role management is allowed only as a platform-controlled
  component or through platform APIs that enforce actor authority and audit.

## Effective Authorization Decision

Runtime access is allowed only when all relevant layers admit it:

```text
effective_permission =
  application declares the permission
  AND installation or owner grant admits that permission profile
  AND actor has platform membership/capability for the requested scope
  AND subject has an ApplicationAccessGrant or default access profile
  AND assigned Application role grants the app capability
  AND resource/webspace/workspace ownership matches the request
  AND device/session trust is sufficient and current
  AND child/guest/tenant/global deny constraints do not prohibit it
  AND approval policy is satisfied
```

Every decision records:

- `actor`: who initiated or approved;
- `subject`: whose access or data is affected;
- `application_id` and release digest;
- `component_id` and tool/action when applicable;
- `actor_chain`: user -> application -> skill -> agent/service -> external API;
- `scope`, `resource`, `webspace_id`, `workspace_id`, and external audience;
- `device_id`, `session_id`, holder binding, and credential purpose;
- permission id, Application role, platform role, decision, reason, and
  `approval_id` when present.

The actor chain is required even when the operation originates from an LLM or
background worker. A model-generated intent never grants authority; the runtime
checks the normalized action after planning and before execution.

## Install, First Use, Runtime, and Update

Install or first use shows the Application permission profile:

- required permissions;
- optional permissions;
- declared Application roles;
- data practices and external providers;
- LLM/model use, including data categories sent to the model path;
- secrets and connected accounts;
- notifications;
- background or recurring actions;
- release-readiness status, warnings, attestations, and residual risks when a
  Builder verification report exists;
- child and guest default behavior.

The local publisher path uses the same policy records. Before Builder places
its own Candidate as Beta, it shows the exact profile, role model, data
practices, diff and Final Verification result. The admitted placement creates
or updates one managed grant for the verified local owner, selects only the
declared owner-default role and binds the grant to that exact permission-profile
digest. This is part of the reviewed Builder transition and does not require a
second Applications approval. It does not grant another user or subnet access.
An elevated profile, ambiguous owner role or missing verification blocks the
transition instead of falling back to node-owner authority.

Runtime action flow:

1. The tool/action gate reads the installed Application context and normalized
   action.
2. It checks an Application-level grant first.
3. If covered, the action runs and emits allowed audit.
4. If not covered and the permission is optional or one-time approvable, it
   creates a Pending Action with user-facing Application language and method
   details only in the advanced disclosure.
5. If policy denies, the action fails closed with a reason that can be shown in
   Applications, Users & Access, Builder, chat, or logs.

Successful local tool and attachment responses expose the selected runtime
source plus exact release/package provenance. Independent acceptance verifies
these headers; UI rendering or an active desktop tile alone is not execution
evidence.

Update flow:

- Removed permissions reduce authority immediately or on next activation.
- Added or elevated permissions require owner/admin review before they become
  effective.
- Role model changes show affected users and grants.
- Auto-update may continue only when the permission profile and role-impact
  diff is compatible with current grants and policy.
- Old grants are bound to the permission profile digest they reviewed. They may
  continue for unchanged permissions, but they do not silently cover newly
  added or elevated permissions.
- Builder updates its managed publisher-owner grant only after the new release
  passes the same review/admission boundary. Manually assigned member, child or
  guest grants remain independent and follow the normal update-impact flow.

The lifecycle plan is the machine-readable consent boundary. Install and
update plans carry `adaos.application.permission_review.v1`, including the
structured required/optional declarations, permission-profile digest,
classified profile/role diff, and the exact newly added or elevated
permissions that require approval. Applications renders that structure rather
than collapsing it into one comma-separated field. The apply command remains
disabled until the required review is acknowledged. An unchanged update does
not ask the owner to approve the same permission set again; an old grant never
implicitly admits a newly added or elevated permission.

This acknowledgement is currently bound to the immutable operation plan and
its digest. Per-optional-permission grants and durable step-up approval remain
separate access-policy operations; the UI must not present independent
allow/deny controls until those decisions are represented and enforced by the
platform grant model.

## Multi-User, Child, and Guest Access

Platform roles are coarse subnet/workspace presets:

```text
owner | co_owner | admin | member | child | guest
```

Application roles are app-defined:

```text
viewer | editor | assignee | teacher | student | moderator | reviewer
```

The first implementation must support both axes without role explosion.

### Child Access

`child` mode is supervised access, not just a role label:

- high privacy defaults;
- no external data sharing, LLM/model use, public posting, secrets, or
  background processing without guardian or owner approval;
- child device pairing requires guardian/owner approval by default;
- guardian approval routes to a trusted guardian device, not to the child's
  device alone;
- owner/admin views may show child access metadata and safety state, but not
  child private content unless a separate privacy-zone policy allows it;
- new permissions affecting child data require renewed guardian review.

### Guest Access

Guest access is temporary and scope-limited:

- session-bound by default;
- profile-unbound by default;
- TTL required for public guest joins;
- no durable grants;
- no secrets or connected accounts;
- no LLM/network/write/background actions by default;
- revoke cuts live browser/Yjs/API access;
- guest acceptance may acknowledge visible scope, but cannot broaden app
  authority.

### Owner, Co-Owner, and Admin

`owner` remains the local technical superuser. `co_owner` and `admin` are
scoped grants with explicit capabilities and audit. Privileged actions such as
installing a high-risk Application, approving new permissions, connecting a
secret, assigning sensitive app roles, or overriding child policy may require
step-up approval even from an already authenticated administrator.

## Users & Access Application

Applications answers: "What does this Application request, and who has access
to it?"

Users & Access answers: "What can this person, child, guest, device, or
session access, and what have they used?"

Target surfaces:

- People: owner, co-owner, admins, members, children, guests, and anonymous or
  external subjects where applicable.
- User detail: profile metadata, memberships, Application access grants,
  Application roles, devices, sessions, invites, approvals, and audit.
- Guest access: active public links, targeted invites, TTL, issuer, scope,
  sessions, and bulk revoke.
- Children: supervised constraints, guardian approval queue, child-safe apps,
  LLM/network/notification limits, and recent denied attempts.
- Devices and sessions: trust level, last used, holder binding, revoke, lost
  device recovery, and session cutoff status.
- Access reviews: long-lived guest sessions, stale devices, inactive users,
  newly elevated Application permissions, unused grants, and broad app roles.
- Activity: allowed/denied permission use, app launches, role changes,
  connected-account use, and Pending Action outcomes.

Applications and Users & Access must share the same access records. Each view
is a different projection, not a separate source of truth.

## Conversational and Voice Surfaces

Conversation is a presentation and navigation layer over the same policy
service:

- Chat may list, explain, revoke, and request low-risk permission changes.
- Chat approvals must use structured buttons or keyboard actions, not
  free-form text as final consent.
- Telegram can mirror the same Pending Action card and keyboard for low or
  medium risk.
- High-risk actions route to a trusted device and may require step-up.
- Voice should notify and route approval, not serve as the sole strong consent
  channel for sensitive actions.

Examples:

```text
"Show Masha's Application access."
"Allow Family Tasks for Masha as assignee, but no LLM or external APIs."
"Give this guest viewer access to Dashboard for two hours."
"Why did Builder ask for LLM access?"
```

The response path still writes normal grants, decisions, and audit records.

## Secrets and Connected Accounts

Secrets appear in the same permission center because users experience them as
part of access:

- show provider, account, scopes, required/optional state, status, last used,
  last rotated, and revoke/replace actions;
- never show secret values;
- keep secret storage in the vault/backend, not Application source;
- require both secret availability and permission grant before use;
- support app-owned provider credentials separately from user delegated
  connected accounts;
- include external provider scopes in Application permission profile and update
  diff.

Full rotation workflows and enterprise secret-manager integration are deferred,
but the V1 surface must avoid creating a separate, invisible secret authority.

## Observability and Builder

Builder needs a permission profiler:

- statically declared permissions from Application declaration and skills;
- inferred permissions from tool side effects, LLM/content APIs, network
  destinations, data routes, notifications, background jobs, and secrets;
- observed runtime permission use;
- observed but undeclared use;
- permissions unused by any observed path;
- child and guest compatibility;
- role model diff and affected user estimates;
- privacy label and data-safety preview.

Release gates:

- undeclared observed high-risk access blocks release;
- newly added or elevated permissions require review;
- generated Applications must include permission and role fixtures for owner,
  member, child, and guest before being treated as access-aware;
- method-level approvals are acceptable fallback evidence, not a substitute
  for Application permission profile coverage.

## Builder Final Verification

Before an ApplicationRelease becomes installable outside source DEV, Builder
must produce an `ApplicationVerificationReport`. The report is the durable
release-readiness artifact for Builder-created or Builder-updated
Applications. It is not a chat summary and not a human completion verdict.

DEV prototypes may show an incomplete report as readiness guidance. Candidate,
Trial, publication, and marketplace submission must respect the report's hard
gates.

Every check has one result:

- `passed`: the invariant was verified;
- `failed`: the invariant was violated and evidence was captured;
- `inconclusive`: the environment, credentials, test data, or diagnostics were
  insufficient for a valid decision;
- `skipped`: the check was outside the declared release scope and the report
  names why.

Every check has one gate level:

- `hard_gate`: failure or inconclusive result blocks Candidate/Trial or
  publication for the applicable channel;
- `warning`: visible debt that does not block the current release stage but is
  shown in Builder, Applications, and review surfaces;
- `attestation`: explicit developer or owner confirmation for an area that is
  not yet fully machine-verifiable.

The first mandatory checklist is:

- permission profile schema, normalization, flat compatibility projection, and
  deterministic `permission_profile_digest`;
- declared versus statically inferred versus observed permissions, with
  undeclared high-risk LLM, network, write, secret, notification, background,
  or external-provider access as hard-gate defects;
- Application role declaration, role ids, role capability expansion,
  `assignable_to`, child/guest compatibility, and role-update impact;
- install/update disclosure preview: required and optional permissions, data
  practices, secrets, connected accounts, notifications, background work,
  LLM/model use, and affected users;
- Pending Action fallback preview for actions not covered by general
  Application grants, including user-facing text and approval routing;
- secrets and connected-account declaration, scope, binding, missing/revoked
  states, and proof that secret values are not exposed in source, logs, or
  report output;
- access matrix checks for owner, member, child, guest, and at least one
  declared custom Application role when roles exist;
- regression-test evidence for changed behavior, with focused affected tests
  required for DEV/Candidate and broader release tests required before
  publication;
- auditability for allowed, denied, approved, revoked, update-blocked, child,
  guest, secret, and external-provider decisions;
- UI disclosure readiness for Applications, Users & Access, Pending Actions,
  chat/Telegram keyboard actions, and voice handoff notifications.

The V1 report should have a small structured shape:

```yaml
verification:
  schema: adaos.application.verification_report.v1
  application_id: family_tasks
  release_digest: sha256:...
  source_commit: ...
  permission_profile_digest: sha256:...
  observed_capabilities_digest: sha256:...
  overall: passed
  checks:
    - id: permission_profile.schema
      gate: hard_gate
      result: passed
      evidence: artifacts/verification/permission-profile.json
    - id: permissions.declared_vs_observed
      gate: hard_gate
      result: passed
      evidence: artifacts/verification/permission-drift.json
    - id: regression.changed_behavior
      gate: hard_gate
      result: passed
      evidence: artifacts/verification/tests-junit.xml
    - id: privacy.data_practices_reviewed
      gate: attestation
      result: passed
      actor: user:developer
  warnings: []
  residual_risks: []
```

The report must be immutable once bound to an ApplicationRelease. A subsequent
source, permission, role, data-practice, dependency, secret, or runtime
behavior change creates a new report or invalidates the old one for release
purposes. Long-term, the report can be signed or embedded as an in-toto/SLSA
style attestation. V1 only needs deterministic serialization, evidence refs,
and digest binding.

## Product Boundary

The first product shape is:

```text
Applications
  -> Installed, Catalog, Updates, Operations
  -> Application detail
     -> Permissions
     -> Access
     -> Roles
     -> Connected accounts
     -> Release readiness

Users & Access
  -> People
  -> Invitations
  -> Guests
  -> Children
  -> Devices and sessions
  -> Application access
  -> Access reviews
  -> Activity

AdaOS Connect
  -> claim an incoming invitation
  -> pair or recover a device
  -> hand off ongoing administration to Users & Access

Pending Actions
  -> per-decision card
  -> deep link to Applications or Users & Access

Builder
  -> permission profiler
  -> final verification report
  -> app role declaration
  -> access fixtures
```

Applications owns the Application-centric projection. Users & Access owns the
subject-centric projection and the owner-governed platform-role, invitation,
device, session, and access-review controls. AdaOS Connect is the bounded entry
surface for joining, pairing, and recovery; it is not a second administration
console. Pending Actions owns time-sensitive decisions. Builder owns authoring
observability. All of these surfaces call the same policy services and must not
persist independent authorization facts.

The first Users & Access product uses the Root MCP `users_access.*` plane as a
typed UI adapter over `PersonalizationAccessService` plus the shared
Application-access projection. Read and mutation tools require an authenticated
typed actor, an explicit subnet/scope, owner-granted capabilities, idempotency
for writes, and redacted operation results. Application-specific role changes
remain in Applications; Users & Access may show those grants across subjects.

## Non-Goals for the First Implementation

The first implementation does not include:

- a full IAM or policy-as-code engine;
- general groups and teams;
- enterprise SSO, SAML, OIDC, LDAP, or SCIM provisioning;
- full DPoP/OAuth-compatible token server behavior;
- complete row-level ACLs inside every Application;
- hierarchical Application roles;
- quorum, threshold, or delegated approval workflows;
- full voice approval for high-risk actions;
- complete signed SLSA/in-toto/SBOM admission and marketplace certification;
- commercial entitlement and billing policy;
- marketplace-wide trust, malware, privacy-label, and compliance review.

The ABI should leave room for these without requiring them for the first
Applications and Users & Access vertical proof.
