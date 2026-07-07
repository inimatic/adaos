# Персонализация, identity и доступ: дорожная карта

Статус: дорожная карта реализации и трекер прогресса.

Целевая архитектура:
[Персонализация, identity и доступ](personalization-identity-access.md).

Эта дорожная карта намеренно построена как набор phase gates. Первая цель - не
сразу сделать большой UI управления пользователями, а посадить минимальные
durable identity, policy, audit и privacy foundations, чтобы последующие QR/link
и personalization flows было безопасно реализовывать.

Критическая правка от 2026-07-02: первая реализация Phase 2 и Phase 3 закрыла
backend/service/SDK slices, но не browser-visible product flows. Теперь roadmap
явно разделяет готовность backend и пользовательскую приемку. Проверенный
backend slice не означает, что функция появилась в настройках клиента, AdaOS
Connect или Join Browser, пока не закрыта соответствующая API/UI-фаза.

## Прогресс

- [x] 2026-07-02: опубликованы целевая архитектура и первичная дорожная карта.
- [x] 2026-07-02: дорожная карта разложена на явные implementation phases,
  gates и local verification requirements.
- [x] 2026-07-02: открыт Phase 0 gate; следующая implementation work -
  versioned schemas, threat model, scope lattice и migration notes.
- [x] 2026-07-02: Phase 0 foundation contracts implemented в
  `src/adaos/domain/personalization_access.py`,
  [Персонализация Phase 0 Contracts](personalization-identity-access-phase0-contracts.md)
  и `tests/test_personalization_access_contracts.py`.
- [x] 2026-07-02: Phase 0 local verification passed через targeted pytest,
  adjacent domain tests, `git diff --check` и MkDocs strict build.
- [x] Phase 0 foundation contracts implemented.
- [x] 2026-07-02: Phase 1 access kernel implemented в
  `src/adaos/services/personalization_access.py`,
  [Персонализация Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md)
  и `tests/test_personalization_access_kernel.py`.
- [x] Phase 1 subject/session/grant store и policy kernel implemented.
- [x] 2026-07-02: Phase 2 profile/preferences slice implemented в
  `src/adaos/services/user/profile.py`,
  [Персонализация Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md)
  и `tests/test_personalization_profile_phase2.py`.
- [x] 2026-07-02: Phase 3 guest join and targeted invite slice implemented в
  `src/adaos/services/personalization_access.py`,
  [Персонализация Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md)
  и `tests/test_personalization_join_phase3.py`.
- [x] 2026-07-02: roadmap критически пересмотрена после проверки видимого UI;
  Phase 2 и Phase 3 явно считаются backend slices, а новые must-have фазы
  покрывают Web API/client settings и AdaOS Connect join UX до того, как можно
  заявлять о видимой персонализации.
- [x] 2026-07-07: закрыты пользовательские хвосты Phase 4-5: browser header
  refresh срабатывает при изменении auth-session, logout очищает
  personalized chrome state, current-user chip показывает initials, AdaOS
  Connect рендерит invite QR, admin panel показывает audit drill-down preview,
  а `adaos switch lang <en|ru>` пишет `ADAOS_LANG` из набора core locales.

## Правила выполнения

- Для каждой фазы перед закрытием должны быть рассмотрены последствия для
  schema, service, SDK/API, UI/projection, audit и tests.
- Фаза может быть отмечена как backend slice только если это явно сказано в ее
  exit gate. Формулировки вроде "user can", "owner can", "display" или
  "visible" требуют API/client evidence или явной boundary note.
- Дизайн следующих фаз можно вести в документации заранее, но runtime
  implementation не должна перескакивать через gate без явной записи в этой
  roadmap.
- User-facing flow не считается завершенным, пока под UI или skill surface нет
  policy enforcement и audit records.
- `owner` остается subnet-level technical superuser, но product surfaces все
  равно должны сохранять границы user-private data.
- Root-server или external identity могут подтверждать, кто пользователь;
  subnet grants решают, что эта identity может делать внутри подсети.
- Где это возможно, нужно опираться на стандартные identity patterns:
  WebAuthn/passkeys для browser public-key authenticators, OAuth device
  authorization semantics для QR/device flows, OIDC/OAuth для external
  authentication и SCIM-style provisioning для enterprise users/groups.

## Практические ориентиры

AdaOS остается local-first, но не должен изобретать identity machinery там, где
уже есть устойчивые стандарты:

- [W3C WebAuthn](https://www.w3.org/TR/webauthn-3/) задает форму
  browser-origin-scoped public-key credentials и будущих passkey-backed user или
  device authenticators.
- [OAuth 2.0 Device Authorization Grant, RFC 8628](https://datatracker.ietf.org/doc/html/rfc8628)
  задает interaction model для QR/code flows, где одно устройство начинает
  flow, а доверенная поверхность одобряет или завершает его.
- [OAuth 2.0, RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749) и
  [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html)
  задают модель external authentication и claims. В AdaOS такие claims могут
  подтверждать identity или предлагать ее, но local grants все равно решают
  subnet access.
- [SCIM, RFC 7644](https://datatracker.ietf.org/doc/html/rfc7644) задает
  практику enterprise user/group provisioning. В AdaOS нужен adapter, а не
  отдельный самодельный enterprise directory protocol.
- [NIST SP 800-63B](https://csrc.nist.gov/pubs/sp/800/63/b/upd2/final)
  помогает держать enrollment, revocation, device loss, recovery и
  reauthentication как один authenticator lifecycle.

## Phase 0 - Foundation Contracts

Priority: `must`.

Цель: сделать модель реализуемой, не заморозив неверные абстракции. Эта фаза
порождает versioned draft contracts, а не необратимые final schemas.
Contract note:
[Персонализация Phase 0 Contracts](personalization-identity-access-phase0-contracts.md).

Checklist:

- [x] Определить scope lattice: `subnet`, `workspace`, `webspace`, `scenario`,
  `skill`, `device/session`, `user_private` и shared workspace data.
- [x] Определить versioned draft schemas для `UserProfile`, `UserKey`,
  `DeviceKey`, `SessionKey`, `Membership`, `Grant`, `Capability`,
  `Preference`, `Invite`, `RecoveryAction` и `ExternalIdentityBinding`.
- [x] Определить schema-version и migration rules для identity/access records.
- [x] Описать миграцию из текущих `Settings.owner_id`, `local-owner`,
  `UserProfileService`, `profile.settings` projections, `access_links` и
  browser scoped storage.
- [x] Определить первый capability vocabulary и role presets: `owner`,
  `co_owner`, `admin`, `member`, `child`, `guest`.
- [x] Определить grant constraints: `expires_at`, `requires_approval_for`,
  `child_mode`, allowed scopes, allowed skill/tool classes и delegation.
- [x] Определить actor semantics: `actor`, `current_user`, `subject_user`,
  service identity и skill `on_behalf_of`.
- [x] Определить threat model для public QR abuse, targeted invite leakage,
  stolen devices, owner/co-owner key loss, skill privilege abuse, revoked live
  sessions и cross-subnet identity correlation.
- [x] Определить audit event schemas, redaction rules, retention defaults и
  query dimensions.
- [x] Определить обязательную security regression matrix для следующих фаз.

Exit gate:

- [x] Architecture и roadmap docs описывают все контракты выше с позицией по
  versioning и migration.
- [x] Runtime implementation не блокируется неопределенными терминами subject,
  session, membership, grant, capability или audit.

Local verification:

- [x] `git diff --check`
- [x] Targeted docs build или link check завершается локально, либо failure
  записан с точной командой и причиной.

## Phase 1 - Subject, Session, Grant Store и Policy Kernel

Priority: `must`.

Цель: посадить backend decision layer до user-facing access flows.
Kernel note:
[Персонализация Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md).

Checklist:

- [x] Добавить durable storage/service contracts для users, profiles, user keys,
  device keys, sessions, memberships, grants, invites и revocations.
- [x] Добавить minimal policy evaluator:
  `is_allowed(actor, action, subject, scope, resource, context)`.
- [x] Реализовать owner implicit subnet-admin semantics.
- [x] Реализовать initial `co_owner`, `admin`, `member`, `child`, `guest` role
  presets как grants/capability bundles.
- [x] Возвращать structured allow/deny decisions с reason codes.
- [x] Добавить append-only audit records для grants, denials,
  profile/preference changes, join/invite actions, device actions и recovery
  actions.
- [x] Добавить audit query helpers по actor, subject, scope, device/session,
  source, decision и time range.
- [x] Реализовать revocation propagation rules для grants, sessions и devices.
- [x] Добавить replay/stale-write protections для invite и recovery material.

Exit gate:

- [x] Локальный тест может создать user, выдать scoped membership, разрешить
  authorized action, запретить unauthorized action, отозвать grant и увидеть
  audit trail.
- [x] Policy decisions не зависят от UI state или skill-local state.

Local verification:

- [x] Unit tests для policy allow/deny/revoke/audit paths.
- [x] Migration test из существующего owner/local profile baseline.

## Phase 2 - Profile и Preferences Backend Slice

Priority: `must`.

Цель: превратить текущие profile/settings механизмы в первый policy-checked
service и SDK slice, не смешивая role с profile data. Эта фаза сама по себе не
делает функцию видимой в browser settings panel.
Slice note:
[Персонализация Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md).

Checklist:

- [x] Перевести `UserProfileService` на versioned profile/preference contract,
  сохранив существующую SDK compatibility.
- [x] Не хранить `role` и membership в profile settings.
- [x] Добавить SDK surface `ctx.current_user`, `ctx.profile` и
  `ctx.preferences`, backed by policy-checked services.
- [x] Оставить существующие `profile_get_settings` и `profile_update_settings`
  как compatibility wrappers.
- [x] Проецировать current-user profile/preferences через KV/Yjs на базе
  существующего projection mechanism.
- [x] Добавить client-facing header settings service model для display name,
  locale/language, theme, memory/privacy preferences, current subnet/workspace,
  role status и device trust status.
- [x] Добавить service/SDK target для browser-scoped UI preferences как user
  preferences плюс device overrides; client localStorage fallback остается UI
  integration concern.
- [x] Эмитить redacted audit records для profile и preference updates.

Exit gate:

- [x] Current user может менять self-service profile/preferences через service
  и SDK helpers; browser UI wiring остается вне этой фазы.
- [x] Role/access preset виден как status, но не редактируется как profile
  field.
- [x] Profile/preference updates переживают restart и проецируются в Yjs.

Local verification:

- [x] Profile SDK tests.
- [x] KV/Yjs projection tests.
- [x] Service/SDK header settings smoke test; browser UI wiring остается вне
  этой фазы и отслеживается в Phase 4.

## Phase 3 - Guest Join и Targeted Invite Backend Slice

Priority: `must`.

Цель: реализовать безопасный QR/link entry на backend layer до device pairing,
recovery и AdaOS Connect UI wiring.
Slice note:
[Персонализация Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md).

Checklist:

- [x] Реализовать `guest_join_link` как public, temporary, scope-limited и не
  profile-bound.
- [x] Реализовать `targeted_invite_link` как personal, expiring, one-time и
  auditable.
- [x] Добавить `profile_hint` support, не считая hint доказательством identity.
- [x] Предоставить consent-preview data, которые joining devices смогут
  показать: target subnet/workspace, role preset, lifetime и acceptance status.
- [x] Добавить owner/co-owner flow для binding unknown session к новому или
  существующему local profile.
- [x] Добавить invite/link rate limits, max session constraints и bulk guest
  revocation.
- [x] Отклонять expired, reused, revoked, stale или wrong-scope invite material.
- [x] Отключать live browser/Yjs access при revocation backing guest или invite
  grant через session revocation и access-link denial hook.

Exit gate:

- [x] Backend service может создать public guest join и отозвать все sessions,
  созданные из него.
- [x] Backend service может создать targeted invite, subject может принять его
  один раз, reuse отклоняется, а audit records показывают issuer, subject,
  scope, role preset и constraints.

Local verification:

- [x] Guest join policy tests.
- [x] Targeted invite expiry/reuse/revoke tests.
- [x] Live session cutoff hook test для revoked guest/invite grant. Browser UI
  и direct websocket disconnect integration отслеживаются в Phase 5.

## Phase 4 - Current-User Settings API и Browser UI

Priority: `must`.

Цель: сделать Phase 2 profile/preference slice видимым и usable из web client,
не превращая UI в source of truth.

Checklist:

- [x] Добавить shared runtime/API routes для current-user profile,
  preferences, header settings и policy explanations.
- [x] Загружать header settings в browser shell и показывать display name,
  role/access status, active subnet/workspace и device trust status.
- [x] Помечать current-user identity resolution как owner fallback до появления
  session identity binding для invited/guest users.
- [x] Добавить avatar/initial rendering polish для current-user header chip.
- [x] Добавить current-user settings panel для display name, preferred name,
  language/locale/timezone, theme, UI density, memory/privacy preferences и
  accessibility preferences.
- [x] Подкрепить language, locale, timezone, device и invite-scope controls
  runtime/API options вместо free-form text там, где есть конкретные варианты.
- [x] Добавить CLI language switching через `adaos switch lang <code>`,
  валидируя код по core `src/adaos/locales/*.json` и сохраняя его как
  `ADAOS_LANG`.
- [x] Применять saved theme preferences в browser shell сразу, а не только
  сохранять их server-side.
- [x] Держать role и membership read-only в current-user settings; любые access
  changes отправлять в owner/admin flows.
- [x] Считать browser localStorage только migration/fallback слоем. Service
  store остается authoritative для user preferences.
- [x] Показывать policy denial messages из structured decisions вместо generic
  UI failures.
- [ ] Добавить identity switcher только для sessions с несколькими valid local
  или external identities; смена identity должна быть explicit authenticated
  action и должна аудироваться.
- [x] Добавить API tests для profile/preference load, edit, refresh,
  denied edit, policy explanation и audit-covered service writes.
- [x] Добавить browser build verification для header/settings panel.

Exit gate:

- [x] Signed-in user может менять разрешенные profile/preferences в web UI и
  видит обновленную шапку после reload/restart.
- [x] Role/access status виден, но не редактируется через profile settings.
- [x] API и UI paths проходят через Phase 1-2 policy/audit services.

Local verification:

- [x] API tests для current-user profile/preference/header routes.
- [x] Browser build smoke test для settings panel и header refresh.
- [x] Audit smoke test для profile/preference updates и denied edits через
  Phase 2 service tests.

## Phase 5 - AdaOS Connect Join UX и Link Management

Priority: `must`.

Цель: сделать Phase 3 join/invite semantics usable через AdaOS Connect и Join
Browser, следуя standard device-flow interaction patterns.

Checklist:

- [x] Добавить API routes для guest join creation, targeted invite creation,
  invite preview, invite claim, invite revoke и guest-session bulk revoke.
- [x] Параметризовать link generation по flow kind, scope, role preset,
  expiry, max sessions и optional profile hint.
- [x] Генерировать invite URLs от public app base с target subnet и root/hub
  endpoint parameters вместо local loopback API origins.
- [x] Добавить actual QR rendering для созданных invite links.
- [x] Дать owner/co-owner возможность создать public guest link для
  classrooms, museums, events, demos и temporary visitors.
- [x] Дать owner/co-owner возможность создать targeted invite: ввести local
  profile/user id hint, выбрать scope, access preset и expiry.
- [ ] Добавить profile picker/create UX вместо free-form profile id entry.
- [x] На joining device показывать target scope, role preset, expiry,
  profile hint при наличии и consent/acceptance action.
- [x] Держать public guest joins session-bound и profile-unbound.
- [x] Сделать targeted invites one-time по умолчанию и явно показывать, что их
  нельзя безопасно выводить публично.
- [x] Показывать owner/co-owner pending, accepted, expired и revoked links с
  revoke actions.
- [x] Добавить audit-history drill-down в link management panel.
- [x] Связать invite/session revocation с access-link denial и browser/Yjs
  admission, чтобы revoked sessions отклонялись без ручных database edits.
- [ ] Добавить direct websocket disconnect orchestration для already-connected
  browser sessions.
- [ ] Моделировать UX по OAuth device authorization: short-lived material,
  pending/accepted/expired states, user-visible scope и explicit consent.

Exit gate:

- [x] Owner может показать public guest link и потом отозвать все sessions,
  созданные из него, через AdaOS Connect.
- [x] Owner может пригласить named user вроде Маши с local profile id,
  scope, role preset и expiry; joining browser может preview и accept invite
  один раз.
- [x] Revoked guest/invite sessions теряют browser/Yjs admission без ручных
  database edits.

Local verification:

- [x] API tests для create/preview/claim/revoke flows.
- [x] Browser build smoke test для guest link и targeted invite panel.
- [x] Revoked-session admission/cutoff test через access-link runtime path, а
  не только service hook.
- [x] Audit query smoke tests для issuer, subject, scope, role preset,
  constraints и revoked sessions через Phase 3 service coverage.

## Phase 6 - Device Pairing и Authenticator Lifecycle

Priority: `must`.

Цель: позволить trusted users добавлять устройства и позволить owner/co-owner
восстанавливать пользователей без небезопасных account-takeover shortcuts.

Checklist:

- [x] Реализовать `device_pairing_link` для добавления нового device к
  существующему trusted user.
- [x] Использовать OAuth device-flow interaction shape для pairing: новое
  устройство открывает short-lived link/code, trusted device подтверждает,
  backend записывает pending/accepted/expired. QR image rendering остается
  deferred.
- [x] Поддержать member self-service device pairing, когда policy разрешает
  `devices.add.self`.
- [x] По умолчанию требовать owner/co-owner approval для child device pairing.
- [ ] Добавить optional WebAuthn/passkey-backed device authenticators, где это
  поддерживает browser platform; local device keys остаются AdaOS authority.
- [x] Реализовать `admin_recovery_link` для owner/co-owner assisted recovery.
- [x] Добавить lost-device flow: bind replacement device, revoke old device и
  invalidate active sessions.
- [x] Добавить device key lifecycle storage, revocation, last-used metadata и
  session invalidation.
- [ ] Добавить device key generation и rotation hooks beyond local key refs.
- [ ] Добавить recovery-code design для пользователей, у которых еще есть
  trusted session или device; recovery без previous trust factor остается
  deferred.
- [x] Описать owner key backup, co-owner recovery и ownership transfer как
  explicit later work, если они не реализованы в этой фазе.

Exit gate:

- [x] User может добавить второе устройство через уже trusted device, когда
  policy это разрешает.
- [x] Owner/co-owner может привязать replacement device к существующему profile
  и отозвать lost device в том же flow.
- [x] Lost/revoked devices не сохраняют active browser/Yjs/API access.

Local verification:

- [x] Device pairing tests.
- [ ] Child approval tests beyond default policy denial.
- [x] Lost-device revoke/session invalidation tests.
- [ ] Recovery-code lifecycle tests, если recovery codes включены.

## Phase 7 - Owner and Admin User Management Surface

Priority: `must`.

Цель: открыть foundation через usable owner/admin control surface, не превращая
skill или UI в source of truth.

Checklist:

- [x] Добавить shared runtime service API для users, profiles, devices,
  memberships, grants, invites и recovery actions.
- [x] Добавить owner/co-owner/admin-facing user management skill или
  control-plane UI.
- [x] Использовать access presets для common flows: family member, child, guest
  и admin. Custom capability editing остается future work.
- [x] Показывать active grants, expired grants, revoked grants, pending invites,
  devices, sessions и audit history.
- [x] Разрешать role preset changes через policy-checked shared services.
- [x] Разрешать device/session revoke через shared services.
- [x] Показывать admin-visible privacy metadata без показа private content.
- [ ] Использовать Pending Actions для sensitive conversational requests:
  binding device, granting membership, changing active user или dangerous tool
  invocation.
- [x] Явно поддержать several administrators: owner, co_owner, scoped admin,
  workspace admin, device admin и guest moderator являются grants, а не profile
  fields.

Exit gate:

- [x] Owner/co-owner может управлять users, memberships, devices, invites и
  revocation без прямых database edits.
- [x] Все actions проходят через shared policy/audit services.
- [x] Admin surfaces делают common presets простыми, но оставляют путь к
  expanded capability view.

Local verification:

- [x] API/service tests.
- [x] Browser UI build smoke test.
- [ ] Dedicated skill action tests.
- [ ] Multi-admin grant и denial tests beyond owner/co-owner preset creation.
- [x] Audit query smoke tests.

## Phase 8 - Privacy Zone Enforcement и User Data Management

Priority: `must`.

Цель: enforce privacy ниже UI conventions и дать пользователям контроль над
собственными данными до того, как multi-user personalization будет считаться
product-complete.

Checklist:

- [ ] Добавить service-level data classification для shared workspace data,
  user-private data, admin-visible metadata и encrypted/private future data.
- [ ] Enforce user-private read/write policy в memory, conversation, profile,
  preference и projection services.
- [ ] Добавить user-owned memory/profile search, edit, export и redaction flows.
- [ ] Добавить admin-visible metadata views, показывающие existence, usage,
  policy events и retention без private content.
- [ ] Добавить retention defaults и redaction audit trails для user-private
  data.
- [ ] Добавить compatibility checks, чтобы существующие owner-superuser paths не
  стали ordinary product read APIs для private user data.
- [ ] Добавить UI copy и policy explanations для private-data denials, понятные
  owner и non-owner users.

Exit gate:

- [ ] Product UI соблюдает privacy zones.
- [ ] Service/API/projection paths enforce те же privacy zones.
- [ ] User может inspect и manage собственные private profile/memory data.
- [ ] Owner/admin UI может управлять access и retention metadata без раскрытия
  private content через ordinary product APIs.

Local verification:

- [ ] User-private access tests.
- [ ] Admin metadata/no-content tests.
- [ ] Export/redaction tests.
- [ ] Projection leakage tests.

## Phase 9 - Skill, Tool и SDK Enforcement

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

## Phase 10 - Optional Global Identity и Root-Server Trust

Priority: `could`.

Цель: добавить remote trust и cross-device convenience, не делая root-server
accounts обязательными для local subnets.

Checklist:

- [ ] Добавить `ExternalIdentityBinding` service и API.
- [ ] Добавить pairwise public key support для снижения cross-subnet
  correlation.
- [ ] Добавить optional root-server verification для targeted remote invites.
- [ ] Добавить passkey/WebAuthn-backed global identity как optional
  authentication и recovery provider.
- [ ] Дать login выбор: known local subnet identity, global identity, guest join
  или access request.
- [ ] Enforce rule: root verifies identity, subnet grants access.
- [ ] Считать OIDC/OAuth providers external identity verifiers, а не local
  authorization sources.
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
- [ ] Identity-choice login tests.

## Phase 11 - Enterprise и Advanced Governance

Priority: `could`.

Цель: поддержать organization-grade identity и governance, не искажая
local-first household model.

Checklist:

- [ ] Добавить `TrustProvider` SDK/service interface для OIDC, SAML, LDAP, SCIM
  и local directory providers.
- [ ] Добавить первый SSO/IdP pilot, который maps external claims to proposed
  local memberships and capabilities.
- [ ] Добавить SCIM-style user/group provisioning adapter для
  enterprise-managed subjects, не делая SCIM обязательным для household
  subnets.
- [ ] Добавить deprovisioning behavior для external users: disable memberships,
  revoke sessions/devices where policy requires it и emit audit records.
- [ ] Добавить admin scopes: workspace admin, device admin и guest moderator.
- [ ] Добавить policy simulation UI перед committing grants.
- [ ] Добавить time-window constraints для classrooms, museums, events и
  guests.
- [ ] Добавить более богатую localization для invite, consent, recovery и
  denial flows.
- [ ] Добавить compliance export/reporting только после стабилизации core audit
  model.

Exit gate:

- [ ] External IdP или SCIM provider может propose, но не silently grant local
  subnet access, если local policy явно не разрешает automatic provisioning для
  этого provider и scope.
- [ ] Admin scopes enforced через тот же policy engine, что и household roles.

Local verification:

- [ ] Trust-provider contract tests.
- [ ] Claim-to-proposed-grant tests.
- [ ] SCIM provision/deprovision adapter tests.
- [ ] Admin-scope policy tests.

## Deferred

Эти пункты должны оставаться видимыми, но не должны блокировать phases 0-11:

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
- [ ] Custom identity federation protocols там, где OIDC/OAuth, WebAuthn,
  OAuth device authorization или SCIM уже подходят для use case.

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

- [ ] Recovery codes, generated while user still has trusted device.
- [ ] Stronger admin-visible privacy metadata and user-private export/redaction.
- [ ] More complete policy explanations and user-facing denial messages.
- [ ] More complete policy simulation перед committing grants.
- [ ] Richer invite, recovery и denial localization.

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

Эти тесты нужно добавлять по фазам по мере появления соответствующих surfaces:

- [x] Expired invite is rejected.
- [x] Reused targeted invite is rejected.
- [x] Public guest join cannot bind a personal profile.
- [x] Revoked guest grant loses live browser/Yjs access.
- [ ] Browser settings UI cannot write role/membership as profile data.
- [ ] Browser settings UI survives refresh/restart and remains backed by the
  shared profile/preference store.
- [ ] Public guest QR is scope-limited and visibly temporary in the joining UI.
- [ ] Targeted invite preview shows subnet/workspace, issuer, role preset,
  expiry и consent before claim.
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

## Definition of done

Дорожная карта закрыта, когда:

- пользователя можно добавить как member/child/guest через документированные
  QR/link flows;
- current-user profile/preferences видимы и редактируются в browser settings
  surface через shared policy/audit services;
- AdaOS Connect может create, preview, claim, list и revoke guest и targeted
  links без direct database edits;
- owner и co-owner управляют users, devices, memberships и revocation через
  shared runtime services;
- user может добавить trusted device или восстановиться через документированный
  prior-trust factor;
- skills декларируют personalization и permission needs в manifests;
- SDK calls явно различают actor/current-user/subject/service semantics;
- policy enforcement защищает profile, memory, device, skill, tool и workspace
  operations;
- privacy zones enforced ниже product UI;
- все access changes и denied decisions дают queryable audit events;
- optional root/global identity может verify identity без обхода local subnet
  grants.
