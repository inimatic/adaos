# Персонализация, identity и доступ: дорожная карта

Статус: дорожная карта реализации и трекер прогресса.

Целевая архитектура:
[Персонализация, identity и доступ](personalization-identity-access.md).

Эта дорожная карта намеренно построена как набор phase gates. Первая цель - не
сразу сделать большой UI управления пользователями, а посадить минимальные
durable identity, policy, audit и privacy foundations, чтобы последующие QR/link
и personalization flows было безопасно реализовывать.

## Прогресс

- [x] 2026-07-02: опубликованы целевая архитектура и первичная дорожная карта.
- [x] 2026-07-02: дорожная карта разложена на явные implementation phases,
  gates и local verification requirements.
- [x] 2026-07-02: открыт Phase 0 gate; следующая implementation work -
  versioned schemas, threat model, scope lattice и migration notes.
- [ ] Phase 0 foundation contracts implemented.

## Правила выполнения

- Для каждой фазы перед закрытием должны быть рассмотрены последствия для
  schema, service, SDK/API, UI/projection, audit и tests.
- Дизайн следующих фаз можно вести в документации заранее, но runtime
  implementation не должна перескакивать через gate без явной записи в этой
  roadmap.
- User-facing flow не считается завершенным, пока под UI или skill surface нет
  policy enforcement и audit records.
- `owner` остается subnet-level technical superuser, но product surfaces все
  равно должны сохранять границы user-private data.
- Root-server или external identity могут подтверждать, кто пользователь;
  subnet grants решают, что эта identity может делать внутри подсети.

## Phase 0 - Foundation Contracts

Priority: `must`.

Цель: сделать модель реализуемой, не заморозив неверные абстракции. Эта фаза
порождает versioned draft contracts, а не необратимые final schemas.

Checklist:

- [ ] Определить scope lattice: `subnet`, `workspace`, `webspace`, `scenario`,
  `skill`, `device/session`, `user_private` и shared workspace data.
- [ ] Определить versioned draft schemas для `UserProfile`, `UserKey`,
  `DeviceKey`, `SessionKey`, `Membership`, `Grant`, `Capability`,
  `Preference`, `Invite`, `RecoveryAction` и `ExternalIdentityBinding`.
- [ ] Определить schema-version и migration rules для identity/access records.
- [ ] Описать миграцию из текущих `Settings.owner_id`, `local-owner`,
  `UserProfileService`, `profile.settings` projections, `access_links` и
  browser scoped storage.
- [ ] Определить первый capability vocabulary и role presets: `owner`,
  `co_owner`, `admin`, `member`, `child`, `guest`.
- [ ] Определить grant constraints: `expires_at`, `requires_approval_for`,
  `child_mode`, allowed scopes, allowed skill/tool classes и delegation.
- [ ] Определить actor semantics: `actor`, `current_user`, `subject_user`,
  service identity и skill `on_behalf_of`.
- [ ] Определить threat model для public QR abuse, targeted invite leakage,
  stolen devices, owner/co-owner key loss, skill privilege abuse, revoked live
  sessions и cross-subnet identity correlation.
- [ ] Определить audit event schemas, redaction rules, retention defaults и
  query dimensions.
- [ ] Определить обязательную security regression matrix для следующих фаз.

Exit gate:

- [ ] Architecture и roadmap docs описывают все контракты выше с позицией по
  versioning и migration.
- [ ] Runtime implementation не блокируется неопределенными терминами subject,
  session, membership, grant, capability или audit.

Local verification:

- [ ] `git diff --check`
- [ ] Targeted docs build или link check завершается локально, либо failure
  записан с точной командой и причиной.

## Phase 1 - Subject, Session, Grant Store и Policy Kernel

Priority: `must`.

Цель: посадить backend decision layer до user-facing access flows.

Checklist:

- [ ] Добавить durable storage/service contracts для users, profiles, user keys,
  device keys, sessions, memberships, grants, invites и revocations.
- [ ] Добавить minimal policy evaluator:
  `is_allowed(actor, action, subject, scope, resource, context)`.
- [ ] Реализовать owner implicit subnet-admin semantics.
- [ ] Реализовать initial `co_owner`, `admin`, `member`, `child`, `guest` role
  presets как grants/capability bundles.
- [ ] Возвращать structured allow/deny decisions с reason codes.
- [ ] Добавить append-only audit records для grants, denials,
  profile/preference changes, join/invite actions, device actions и recovery
  actions.
- [ ] Добавить audit query helpers по actor, subject, scope, device/session,
  source, decision и time range.
- [ ] Реализовать revocation propagation rules для grants, sessions и devices.
- [ ] Добавить replay/stale-write protections для invite и recovery material.

Exit gate:

- [ ] Локальный тест может создать user, выдать scoped membership, разрешить
  authorized action, запретить unauthorized action, отозвать grant и увидеть
  audit trail.
- [ ] Policy decisions не зависят от UI state или skill-local state.

Local verification:

- [ ] Unit tests для policy allow/deny/revoke/audit paths.
- [ ] Migration test из существующего owner/local profile baseline.

## Phase 2 - Profile и Preferences Vertical Slice

Priority: `must`.

Цель: превратить текущие profile/settings механизмы в первый user-visible
personalization slice, не смешивая role с profile data.

Checklist:

- [ ] Перевести `UserProfileService` на versioned profile/preference contract,
  сохранив существующую SDK compatibility.
- [ ] Не хранить `role` и membership в profile settings.
- [ ] Добавить SDK surface `ctx.current_user`, `ctx.profile` и
  `ctx.preferences`, backed by policy-checked services.
- [ ] Оставить существующие `profile_get_settings` и `profile_update_settings`
  как compatibility wrappers.
- [ ] Проецировать current-user profile/preferences через KV/Yjs на базе
  существующего projection mechanism.
- [ ] Добавить settings в шапке клиента для display name, locale/language,
  theme, memory/privacy preferences, current subnet/workspace, role status и
  device trust status.
- [ ] Мигрировать browser-scoped UI preferences к user preferences плюс device
  overrides, сохранив localStorage fallback.
- [ ] Эмитить redacted audit records для profile и preference updates.

Exit gate:

- [ ] Current user может менять self-service profile/preferences из клиента и
  через SDK helpers.
- [ ] Role/access preset виден как status, но не редактируется как profile
  field.
- [ ] Profile/preference updates переживают restart и проецируются в Yjs.

Local verification:

- [ ] Profile SDK tests.
- [ ] KV/Yjs projection tests.
- [ ] Browser settings smoke test where practical.

## Phase 3 - Guest Join и Targeted Invite Flows

Priority: `must`.

Цель: реализовать безопасный QR/link entry до device pairing и recovery.

Checklist:

- [ ] Реализовать `guest_join_link` как public, temporary, scope-limited и не
  profile-bound.
- [ ] Реализовать `targeted_invite_link` как personal, expiring, one-time и
  auditable.
- [ ] Добавить `profile_hint` support, не считая hint доказательством identity.
- [ ] Требовать от joining devices показывать target subnet/workspace, role
  preset, lifetime и consent/acceptance action.
- [ ] Добавить owner/co-owner flow для binding unknown session к новому или
  существующему local profile.
- [ ] Добавить invite/link rate limits, max session constraints и bulk guest
  revocation.
- [ ] Отклонять expired, reused, revoked, stale или wrong-scope invite material.
- [ ] Отключать live browser/Yjs access при revocation backing guest или invite
  grant.

Exit gate:

- [ ] Owner может создать public guest join и отозвать все sessions, созданные
  из него.
- [ ] Owner может создать targeted invite, user может принять его один раз,
  повторное использование rejected, а audit records показывают issuer, subject,
  scope, role preset и constraints.

Local verification:

- [ ] Guest join policy tests.
- [ ] Targeted invite expiry/reuse/revoke tests.
- [ ] Live session cutoff test для revoked guest/invite grant.

## Phase 4 - Device Pairing и Admin Recovery

Priority: `must`.

Цель: позволить trusted users добавлять устройства и позволить owner/co-owner
восстанавливать пользователей без небезопасных account-takeover shortcuts.

Checklist:

- [ ] Реализовать `device_pairing_link` для добавления нового device к
  существующему trusted user.
- [ ] Поддержать member self-service device pairing, когда policy разрешает
  `devices.add.self`.
- [ ] По умолчанию требовать owner/co-owner approval для child device pairing.
- [ ] Реализовать `admin_recovery_link` для owner/co-owner assisted recovery.
- [ ] Добавить lost-device flow: bind replacement device, revoke old device и
  invalidate active sessions.
- [ ] Добавить device key lifecycle: generation, storage, rotation hooks,
  revocation, last-used metadata и session invalidation.
- [ ] Описать owner key backup, co-owner recovery и ownership transfer как
  explicit later work, если они не реализованы в этой фазе.

Exit gate:

- [ ] User может добавить второе устройство через уже trusted device, когда
  policy это разрешает.
- [ ] Owner/co-owner может привязать replacement device к существующему profile
  и отозвать lost device в том же flow.
- [ ] Lost/revoked devices не сохраняют active browser/Yjs/API access.

Local verification:

- [ ] Device pairing tests.
- [ ] Child approval tests.
- [ ] Lost-device revoke/session invalidation tests.

## Phase 5 - Owner and Admin User Management Surface

Priority: `must`.

Цель: открыть foundation через usable owner/admin control surface, не превращая
skill или UI в source of truth.

Checklist:

- [ ] Добавить shared runtime service API для users, profiles, devices,
  memberships, grants, invites и recovery actions.
- [ ] Добавить owner/co-owner/admin-facing user management skill или
  control-plane UI.
- [ ] Использовать access presets для common flows: family member, child, guest,
  admin и custom.
- [ ] Показывать active grants, expired grants, revoked grants, pending invites,
  devices, sessions и audit history.
- [ ] Разрешать role preset changes через policy-checked shared services.
- [ ] Разрешать device/session revoke через shared services.
- [ ] Показывать admin-visible privacy metadata без показа private content.
- [ ] Использовать Pending Actions для sensitive conversational requests:
  binding device, granting membership, changing active user или dangerous tool
  invocation.

Exit gate:

- [ ] Owner/co-owner может управлять users, memberships, devices, invites и
  revocation без прямых database edits.
- [ ] Все actions проходят через shared policy/audit services.

Local verification:

- [ ] API/service tests.
- [ ] Skill/UI action tests.
- [ ] Audit query smoke tests.

## Phase 6 - Skill, Tool и SDK Enforcement

Priority: `must`.

Цель: сделать personalization и access checks частью skill design и tool
execution, а не только user-management flows.

Checklist:

- [ ] Расширить skill manifest vocabulary для declared personalization usage,
  required permissions, optional permissions, role variants, user variants и
  device variants.
- [ ] Добавить SDK helpers для `ctx.actor`, `ctx.current_user`,
  `ctx.subject_user`, `ctx.profile`, `ctx.preferences`, `ctx.require` и
  `ctx.policy.explain`.
- [ ] Смоделировать service identities и skill `on_behalf_of` behavior.
- [ ] Enforce both skill permission and actor capability перед sensitive tool
  invocation.
- [ ] Добавить policy gates для memory reads/writes, profile writes, device
  actions, browser automation, skill installation и workspace writes.
- [ ] Возвращать policy explanations в user-visible surfaces, где это уместно.
- [ ] Добавить generated-skill examples, использующие Pending Actions для
  long-term personalization writes.

Exit gate:

- [ ] Skill не может читать или писать private data другого пользователя без
  grant.
- [ ] Tool invocation может быть denied по actor role/capability, даже если
  skill существует и установлен.
- [ ] Manifest-declared permissions валидируются до activation или use.

Local verification:

- [ ] Manifest validation tests.
- [ ] SDK policy tests.
- [ ] Sensitive tool denial tests.
- [ ] Memory/profile cross-user denial tests.

## Phase 7 - Privacy Zone Enforcement и User Data Management

Priority: `should`.

Цель: enforce privacy ниже UI conventions и дать пользователям контроль над
собственными данными.

Checklist:

- [ ] Добавить service-level data classification для shared workspace data,
  user-private data, admin-visible metadata и encrypted/private future data.
- [ ] Enforce user-private read/write policy в memory, conversation, profile и
  preference services.
- [ ] Добавить user-owned memory/profile search, edit, export и redaction flows.
- [ ] Добавить admin-visible metadata views, показывающие existence, usage,
  policy events и retention без private content.
- [ ] Добавить retention defaults и redaction audit trails для user-private
  data.
- [ ] Добавить compatibility checks, чтобы существующие owner-superuser paths не
  стали ordinary product read APIs для private user data.

Exit gate:

- [ ] Product UI соблюдает privacy zones.
- [ ] Service/API paths enforce те же privacy zones.
- [ ] User может inspect и manage собственные private profile/memory data.

Local verification:

- [ ] User-private access tests.
- [ ] Admin metadata/no-content tests.
- [ ] Export/redaction tests.

## Phase 8 - Optional Global Identity и Root-Server Trust

Priority: `could`.

Цель: добавить remote trust и cross-device convenience, не делая root-server
accounts обязательными для local subnets.

Checklist:

- [ ] Добавить `ExternalIdentityBinding` service и API.
- [ ] Добавить pairwise public key support для снижения cross-subnet
  correlation.
- [ ] Добавить optional root-server verification для targeted remote invites.
- [ ] Добавить passkey-backed global identity как optional recovery provider.
- [ ] Дать login выбор: known local subnet identity, global identity, guest join
  или access request.
- [ ] Enforce rule: root verifies identity, subnet grants access.
- [ ] Добавить profile portability tooling между subnets с destination owner
  acceptance.

Exit gate:

- [ ] Global identity может быть bound к local profile.
- [ ] Verified global identity все равно не имеет subnet access без local
  membership/grant.
- [ ] Pairwise identity behavior протестирован или явно deferred.

Local verification:

- [ ] External identity binding tests.
- [ ] Local-grant-required tests.
- [ ] Invite verification tests where implemented.

## Phase 9 - Enterprise и Advanced Governance

Priority: `could`.

Цель: поддержать organization-grade identity и governance, не искажая
local-first household model.

Checklist:

- [ ] Добавить `TrustProvider` SDK/service interface для OIDC, SAML, LDAP и
  local directory providers.
- [ ] Добавить первый SSO/IdP pilot, который maps external claims to proposed
  local memberships and capabilities.
- [ ] Добавить admin scopes: workspace admin, device admin и guest moderator.
- [ ] Добавить policy simulation UI перед committing grants.
- [ ] Добавить time-window constraints для classrooms, museums, events и
  guests.
- [ ] Добавить более богатую localization для invite, consent, recovery и
  denial flows.
- [ ] Добавить compliance export/reporting только после стабилизации core audit
  model.

Exit gate:

- [ ] External IdP может propose, но не silently grant local subnet access.
- [ ] Admin scopes enforced через тот же policy engine, что и household roles.

Local verification:

- [ ] Trust-provider contract tests.
- [ ] Claim-to-proposed-grant tests.
- [ ] Admin-scope policy tests.

## Deferred

Эти пункты должны оставаться видимыми, но не должны блокировать phases 0-9:

- [ ] Полная криптографическая изоляция user-private data от subnet owner.
- [ ] Secret-store redesign для per-user и per-device encryption keys.
- [ ] Mandatory root-server account system.
- [ ] Fully autonomous recovery без owner, co-owner, trusted device, recovery
  code или external identity provider.
- [ ] Cross-subnet federation, где remote subnet может выдать доступ без local
  owner/admin decision.
- [ ] Quorum-based administration для high-security deployments.
- [ ] Hardware-backed key management requirements для всех trusted devices.
- [ ] Mature SSO group-to-capability synchronization и deprovisioning.

## MoSCoW coverage

### Must

- [ ] Phase 0 foundation contracts.
- [ ] Phase 1 subject/session/grant store and policy/audit kernel.
- [ ] Phase 2 profile/preferences vertical slice.
- [ ] Phase 3 guest join and targeted invite flows.
- [ ] Phase 4 device pairing and admin recovery.
- [ ] Phase 5 owner/admin user management surface.
- [ ] Phase 6 skill, tool, and SDK enforcement.

### Should

- [ ] Phase 7 privacy-zone enforcement and user data management.
- [ ] Recovery codes, generated while user still has trusted device.
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

Эти тесты нужно добавлять по фазам по мере появления соответствующих surfaces:

- [ ] Expired invite is rejected.
- [ ] Reused targeted invite is rejected.
- [ ] Public guest join cannot bind a personal profile.
- [ ] Revoked guest grant loses live browser/Yjs access.
- [ ] Revoked device loses live browser/Yjs/API access.
- [ ] Child cannot add a device without approval when policy requires it.
- [ ] Member can add own device only with `devices.add.self`.
- [ ] Member cannot invite users without `users.invite`.
- [ ] Skill cannot read another user's private memory without a grant.
- [ ] Skill cannot write long-term memory without the required policy path.
- [ ] Owner/admin UI can see user-private metadata but not private content.
- [ ] Global identity verification does not grant subnet access by itself.
- [ ] Denied tool invocation records a policy decision and reason code.

## Definition of done

Дорожная карта закрыта, когда:

- пользователя можно добавить как member/child/guest через документированные
  QR/link flows;
- owner и co-owner управляют users, devices, memberships и revocation через
  shared runtime services;
- skills декларируют personalization и permission needs в manifests;
- SDK calls явно различают actor/current-user/subject/service semantics;
- policy enforcement защищает profile, memory, device, skill, tool и workspace
  operations;
- privacy zones enforced ниже product UI;
- все access changes и denied decisions дают queryable audit events;
- optional root/global identity может verify identity без обхода local subnet
  grants.
