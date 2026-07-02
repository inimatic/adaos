# Персонализация, identity и доступ

Статус: целевая архитектура и словарь.

Документ фиксирует целевую модель персонализации, личности пользователя,
подключения устройств, ролей, grant'ов доступа и наблюдаемости в AdaOS. Он
расширяет существующий концепт персонализации явной local-first моделью доверия
и словарем авторизации.

Дорожная карта реализации:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

## Цели

AdaOS должен поддерживать домашние, учебные, музейные и small-team сценарии без
обязательной глобальной cloud identity. Пользовательский UX может оставаться
QR/link-first, но модель платформы должна строго разделять личность, устройства,
membership, policy и границы персональных данных.

Целевая архитектура должна:

- сохранять работоспособность подсети без root server;
- честно считать владельца подсети локальным техническим superuser;
- позволять не-owner пользователям иметь приватные данные и scoped membership;
- поддерживать публичный guest join, персональный invite, device pairing и
  recovery;
- принимать access-решения через роли, capabilities, constraints и audit;
- оставлять место для optional root-server identity, SSO и изоляции секретов.

## Текущая реализованная база

В AdaOS уже есть несколько механизмов, на которые опирается эта архитектура:

- `UserProfileService` хранит MVP profile settings и эмитит
  `user.profile.changed`.
- Scenario data projections умеют маршрутизировать
  `current_user/profile.settings` в KV и Yjs.
- Conversation memory уже имеет scoped records, consent state, pending write
  review, redaction и audit events.
- Device/access-link слой уже моделирует browser и member endpoint access,
  lifetime, revocation и device observability.
- Named entities и aliases дают локализованные human-facing имена.
- Browser client имеет subnet-scoped UI preferences для части настроек.
- Phase 0 contracts задают общий vocabulary для scope, subject, role,
  capability, invite, recovery, decision и audit.
- Phase 1 реализует reusable backend access kernel для persisted identity
  facts, session/device-aware policy decisions, revocation facts, replay guards
  и audit queries.
- Phase 2 сохраняет profile settings compatibility и добавляет versioned
  profile/preference records, current-user SDK helpers, header settings и
  redacted profile/preference audit.
- Phase 3 реализует backend guest join и targeted invite flows с consent
  preview data, scoped claims, grant issuance, bulk revocation и
  session/access-link cutoff hooks.

Эти механизмы полезны, но пока не собраны в end-to-end profile UI, join flow,
API middleware и SDK enforcement surface.

## Модель доверия

### Subnet root of trust

У каждой подсети есть локальный trust root. Внутри подсети он является
authoritative issuer для:

- owner и co-owner grants;
- user keys;
- device keys;
- browser session keys;
- workspace memberships;
- capability grants;
- revocation records.

Root server может помогать с discovery, routing или проверкой external identity,
но сам по себе не выдает доступ в подсеть. Доступ в подсеть всегда является
решением owner, co-owner, admin policy или уже доверенного устройства,
действующего в рамках grant.

### Owner как subnet superuser

`owner` - это технический superuser уровня подсети, а не обычная роль
workspace. У owner есть фактический административный доступ, потому что он
контролирует hub machine, server files, исходный код и local runtime.

Продуктовый UI все равно должен различать:

- техническое администрирование;
- обычный read-доступ к чужим приватным данным;
- policy-решения вроде приглашения пользователей и выдачи capabilities.

Так AdaOS может начать с честной owner-superuser модели и не закрыть будущий
путь к encrypted private data и более сильной изоляции.

### Optional external trust providers

Root-server identity и enterprise SSO являются optional trust providers. Они
могут подтверждать, что внешний public key или account принадлежит пользователю,
но подсеть сама решает, доверять ли этой identity.

External providers должны приводиться к общему интерфейсу:

```text
TrustProvider
  verify_identity(assertion)
  map_claims_to_subjects(assertion)
  propose_membership_grants(subject)
```

Примеры:

- `inimatic_root`;
- OIDC/SAML/LDAP enterprise IdP;
- будущий local directory service.

## Core objects

### User profile

`UserProfile` - это локальные user-facing данные:

- display name и preferred name;
- locale, language, timezone;
- avatar или visual identity;
- self-editable profile fields;
- персональные preferences, которые не являются access policy.

Профиль не является доказательством личности. Это human-facing запись,
привязанная к user subject.

### User key

`UserKey` - криптографическая identity пользователя внутри подсети. Профиль
может быть создан до появления доверенного ключа пользователя, но access grants
должны привязываться к keys, devices или sessions, а не только к именам.

### Device key

`DeviceKey` идентифицирует доверенное устройство или endpoint пользователя.
Телефон, browser на ноутбуке, ReDevice endpoint или будущий agent endpoint могут
иметь отдельные ключи и lifecycle.

### Session key

`SessionKey` идентифицирует короткоживущую browser или endpoint session. Она
может быть anonymous, guest-scoped или привязанной к user/device после approval.

### Membership

`Membership` связывает subject со scope:

```text
subject: user:masha
scope: workspace:family
role: member
status: active
```

Membership всегда scoped. Пользователь может быть member в одном workspace и
guest в другом. Subnet owner имеет implicit admin access во всех scope подсети.

### Grant

`Grant` - authorization record. Он может быть role-based, capability-based или
ограниченным constraints:

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

Preferences - это настройки, выбранные пользователем или для пользователя. Они
не должны содержать membership или role state. Примеры:

- theme;
- language;
- UI density;
- assistant display name;
- privacy и memory preferences;
- accessibility preferences.

Device-specific overrides могут накладываться поверх user preferences.

## Роли и capabilities

Роли - human-facing presets. Capabilities - enforcement vocabulary.

Рекомендуемые стартовые роли:

- `owner`: subnet superuser и root administrator;
- `co_owner`: доверенный администратор для recovery и user management, не
  обязательно владеющий server machine;
- `admin`: управляет users, workspaces, skills и policies в назначенных scope;
- `member`: доверенный именованный пользователь;
- `child`: именованный пользователь со строгими defaults и owner approval
  constraints;
- `guest`: временный или ограниченный subject, часто session-bound.

`invited` лучше считать статусом приглашения, а не ролью:

- `pending`;
- `accepted`;
- `expired`;
- `revoked`.

Capabilities должны быть явными и достаточно стабильными для SDK, skill
manifests, tool invocation и audit:

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

Role presets разворачиваются в capabilities плюс constraints. Advanced UI может
потом показывать expanded capability set, но обычные owner flows должны
показывать простые presets.

## Join и login flows

AdaOS должен поддерживать четыре разных QR/link flows. Их нельзя схлопывать в
один общий "join", потому что у них разные security semantics.

### Public guest join

Use case: лекционный зал, музей, публичный kiosk, временный demo.

Свойства:

- QR или link можно показывать публично;
- вход как `guest` или `visitor`;
- grants временные и scope-limited;
- personal profile binding не подразумевается;
- joining device все равно должен видеть, к какой подсети/workspace он
  присоединяется.

### Targeted invite

Use case: owner приглашает Машу как family member или workspace member.

Свойства:

- invite персональный, одноразовый и expiring;
- invite может содержать `profile_hint` или заранее выбранный local profile;
- joining device принимает invite;
- итоговый membership пишется с issuer, subject, scope, role preset и
  constraints.

Targeted links нельзя показывать публично. Кто первый предъявит такую ссылку,
может стать intended user, если нет дополнительного proof или confirmation.

### Device pairing

Use case: Маша уже вошла на телефоне и хочет добавить ПК.

Свойства:

- новое устройство показывает QR;
- уже доверенное устройство подписывает или подтверждает новый device key;
- policy решает, требуется ли также owner approval;
- итоговое устройство привязано к тому же user profile и memberships.

Полезные policies:

- members могут добавлять свои устройства;
- children требуют owner approval;
- sensitive workspaces требуют admin approval.

### Admin recovery

Use case: Маша потеряла телефон и ей нужно новое доверенное устройство.

Свойства:

- owner или co-owner сканирует QR нового устройства;
- выбирается существующий профиль;
- новый device key привязывается к профилю;
- старое устройство можно отозвать в том же flow;
- recovery action аудитится как privileged.

Без owner/co-owner, другого trusted device, recovery code или external identity
provider безопасного восстановления нет. "Простой" fallback без прежнего фактора
доверия будет означать возможность захвата профиля.

## Global и subnet identities

AdaOS должен сохранить local subnet identity как default и разрешить global
identity как optional binding.

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

Важное правило:

```text
Root server verifies identity.
Subnet grants access.
```

Если у пользователя есть и local, и global identity, login UI может предложить:

- продолжить как известный local subnet user;
- использовать global identity;
- войти как guest;
- запросить доступ.

Чтобы уменьшить cross-subnet correlation, архитектура должна поддерживать
pairwise keys: один global account может предъявлять разные subnet-specific
public keys, а root server подтверждает связь только при необходимости.

## Зоны персональных данных

Data model должен различать видимость даже до реализации криптографической
изоляции.

### Shared workspace data

Данные, намеренно общие внутри workspace:

- shared scenario state;
- collaborative documents;
- public skill outputs;
- workspace-level settings.

### User-private data

Данные, принадлежащие пользователю:

- personal memory;
- personal conversation history;
- private preferences;
- private notes и profile details.

Owner/admin UI не должен casually показывать user-private content. Metadata,
policy events, storage usage и revocation controls могут быть admin-visible.

### Secret и encrypted data

Будущая более строгая изоляция:

- user-held encryption keys;
- secret store access scoped к user/device;
- encrypted private memory;
- owner не может расшифровать данные через обычные product APIs.

Реализацию можно отложить, но архитектура не должна закреплять product-level
read access ко всем приватным данным пользователей.

## Actor, current user и subject user

Runtime и SDK calls не должны смешивать того, кто действует, с тем, кого
редактируют или обсуждают.

```text
actor
  authenticated subject that performs the action

current_user
  user attached to the current client/session

subject_user
  user that an operation is about
```

Примеры:

- Маша меняет свой язык: `actor == current_user == subject_user`.
- Owner привязывает новое устройство к Маше: `actor == owner`,
  `subject_user == Masha`.
- Skill пишет memory для active agent/user pair: actor и subject должны быть
  явными в write policy.

Conversational commands могут создавать privileged action proposal, но не должны
молча менять actor identity или завершать sensitive grants без confirmation.

## Client settings surface

Settings в шапке клиента должны стать нормальной точкой входа в профиль и
preferences текущего пользователя.

Там стоит показывать:

- display name и avatar;
- language и locale;
- theme и UI density;
- personal memory/privacy preferences;
- current subnet/workspace;
- current role или access preset как read-only status;
- device trust status.

Role не должен быть profile field. Role и membership принадлежат access policy.

## User management skill

AdaOS должен предоставить owner/admin-facing user management skill или control
plane surface. Он должен работать через shared runtime services, а не владеть
authorization model.

Возможности:

- list users, devices, memberships и pending invites;
- create targeted invites;
- approve public guest joins, когда это требуется;
- bind session к существующему profile;
- create local profiles;
- change role presets;
- revoke devices и sessions;
- inspect policy decisions и audit trails;
- manage child-mode constraints и temporary access expiry.

## SDK и manifest surface

Skills нужен стабильный контракт для personalization и access-aware behavior.

Целевая форма SDK:

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

Целевая форма manifest:

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

SDK должен позволить skill декларировать адаптацию по role, user, device или
workspace, но не должен позволять skill обходить policy checks.

## Наблюдаемость и audit

Персонализации нужна явная наблюдаемость, потому что profile changes и grants
влияют на trust.

Рекомендуемые события:

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

Audit records должны включать:

- actor;
- subject;
- scope;
- device/session;
- source skill или client surface;
- request id и trace id;
- policy decision;
- redacted diff для profile/preference changes;
- expiration и constraints для grants.

Audit logs не должны хранить raw private content, если конкретная подсистема не
имеет отдельную retention policy.

## Итоговая модель

Чистая модель состоит из четырех слоев:

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

Default UX должен оставаться простым:

```text
QR/link -> approve or accept -> choose profile/access preset -> record grants
```

Платформенный контракт под ним должен быть достаточно точным, чтобы поддержать
privacy, delegation, child profiles, temporary guests, multiple administrators,
external identity и будущую secret isolation.
