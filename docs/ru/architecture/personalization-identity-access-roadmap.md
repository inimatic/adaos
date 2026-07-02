# Персонализация, identity и доступ: дорожная карта

Статус: дорожная карта реализации.

Целевая архитектура:
[Персонализация, identity и доступ](personalization-identity-access.md).

Дорожная карта использует MoSCoW categories:

- `must`: нужно для цельной и безопасной первой продуктовой реализации;
- `should`: важные follow-up работы для usability, observability или safety;
- `could`: полезные расширения, которые не должны блокировать первый проход;
- `deferred`: сознательно отложенные работы, под которые нужно оставить место в
  архитектуре.

## Must

- [ ] Зафиксировать canonical schemas для `UserProfile`, `UserKey`,
  `DeviceKey`, `SessionKey`, `Membership`, `Grant`, `Capability`,
  `Preference` и `ExternalIdentityBinding`.
- [ ] Не хранить `role` в profile settings; моделировать role как scoped
  membership или grant attribute.
- [ ] Ввести capability vocabulary для profile, preferences, users, devices,
  skills, tools, memory и workspace access.
- [ ] Зафиксировать стартовые role presets: `owner`, `co_owner`, `admin`,
  `member`, `child`, `guest`.
- [ ] Считать `owner` subnet-level technical superuser с implicit admin access
  по scope подсети.
- [ ] Добавить first-class `co_owner` или эквивалентную recovery/admin роль.
- [ ] Разделить `actor`, `current_user` и `subject_user` в policy decisions,
  audit events и SDK terminology.
- [ ] Развести join flows на отдельные типы: `guest_join_link`,
  `targeted_invite_link`, `device_pairing_link`, `admin_recovery_link`.
- [ ] Сделать targeted invite links expiring, one-time и auditable.
- [ ] Гарантировать, что public guest links не подразумевают personal profile
  binding.
- [ ] Добавить owner/co-owner flow для binding unknown session к новому или
  существующему local profile.
- [ ] Добавить device revocation semantics для потерянных или замененных
  устройств.
- [ ] Зафиксировать privacy zones: shared workspace data, user-private data,
  admin-visible metadata и future encrypted private data.
- [ ] Не показывать user-private memory и conversation content в обычном
  admin/owner UI по умолчанию.
- [ ] Перевести settings в шапке клиента к current-user profile/preferences
  surface.
- [ ] Показывать current role/access preset как status в user settings, а не
  как редактируемое поле profile.
- [ ] Создать user-management skill или control-plane surface для owners/admins.
- [ ] Проводить user-management actions через shared runtime services, а не
  через skill-local state.
- [ ] Зафиксировать audit events для profile changes, preference changes,
  invites, memberships, roles, capability decisions, device pairing, revocation
  и recovery.
- [ ] Хранить redacted diffs и policy decisions в audit records; не писать raw
  private content в generic audit logs.
- [ ] Расширить skill manifest vocabulary для declared personalization usage и
  required/optional permissions.
- [ ] Расширить SDK vocabulary: `ctx.actor`, `ctx.current_user`,
  `ctx.profile`, `ctx.preferences`, `ctx.require` и явная semantics для
  selected/subject user.
- [ ] Добавить тесты на profile updates через SDK, KV/Yjs projection и audit
  event emission.
- [ ] Добавить тесты на role/capability denial хотя бы для одного sensitive tool
  path.

## Should

- [ ] Добавить preset-based owner UX для grants: family member, child, guest,
  admin и custom.
- [ ] Добавить constraints в grants: `expires_at`, `requires_approval_for`,
  `child_mode`, allowed workspace ids и allowed skill/tool classes.
- [ ] Поддержать member self-service device pairing, когда policy разрешает
  `devices.add.self`.
- [ ] Требовать owner approval для child self-service device pairing.
- [ ] Добавить lost-device flow в один шаг: bind replacement device и предложить
  revoke old devices/sessions.
- [ ] Добавить read-only user/device/membership inventory projection для
  owner/admin UI.
- [ ] Связать access-link/device inventory с user profile и membership records.
- [ ] Нормализовать browser-scoped UI preferences в user preferences плюс device
  overrides, сохранив localStorage fallback.
- [ ] Добавить policy explanations для denied tool/skill calls.
- [ ] Добавить pending actions для sensitive conversational requests: смена
  active user, binding device, grant membership или dangerous tool invocation.
- [ ] Добавить child-mode defaults для memory writes, browser automation,
  external communication и device pairing.
- [ ] Добавить temporary guest access с automatic expiry и visible session
  status.
- [ ] Добавить user-private memory search/edit UI для владельца данных.
- [ ] Добавить admin-visible privacy metadata без показа private content.
- [ ] Добавить import/export и redaction flows для user profile и memory data.
- [ ] Добавить optional recovery codes, генерируемые пока у пользователя есть
  trusted device.
- [ ] Добавить policy decision tests для public guest joins, targeted invites,
  device pairing и admin recovery.

## Could

- [ ] Добавить pairwise global identity bindings, чтобы один global account мог
  использовать разные subnet-specific public keys.
- [ ] Добавить optional root-server backed invite verification для remote
  targeted invites.
- [ ] Добавить passkey-backed global identity как recovery provider.
- [ ] Добавить enterprise `TrustProvider` implementations для OIDC, SAML, LDAP
  или domain-specific directories.
- [ ] Добавить несколько admin scopes: workspace admin, device admin и guest
  moderator.
- [ ] Добавить delegation policies вроде "Маша может добавлять свои устройства,
  но не может приглашать пользователей".
- [ ] Добавить time-window constraints для гостей, детей, аудиторий, музеев и
  events.
- [ ] Добавить policy simulation UI: показать, что user/role/device сможет
  делать до применения grant.
- [ ] Добавить более богатую localization для invite, consent, recovery и denial
  flows.
- [ ] Добавить profile portability tooling между подсетями с явным owner
  acceptance в destination subnet.

## Deferred

- [ ] Полная криптографическая изоляция user-private data от subnet owner.
- [ ] Secret-store redesign для per-user и per-device encryption keys.
- [ ] Обязательная root-server account system.
- [ ] Fully autonomous recovery без owner, co-owner, trusted device, recovery
  code или external identity provider.
- [ ] Cross-subnet federation, где удаленная подсеть может выдать доступ без
  local owner/admin decision.
- [ ] Fine-grained enterprise compliance reporting сверх core audit event model.
- [ ] Quorum-based administration для high-security deployments.
- [ ] Hardware-backed key management requirements для всех trusted devices.
- [ ] Mature SSO group-to-capability synchronization и deprovisioning.

## Рекомендуемый порядок

- [ ] Phase 0: зафиксировать vocabulary и schemas для profile, identity,
  device, membership, grants, capabilities, preferences и audit.
- [ ] Phase 1: реализовать current-user profile/preferences settings в клиенте и
  SDK на базе существующих profile settings storage и projections.
- [ ] Phase 2: реализовать owner/co-owner user-management surface с local
  profiles, targeted invites, public guest joins и device revocation.
- [ ] Phase 3: ввести policy evaluation для role presets, capabilities и
  constraints на sensitive skill/tool/data paths.
- [ ] Phase 4: добавить device pairing и admin recovery flows, включая
  lost-device revocation.
- [ ] Phase 5: добавить privacy-zone enforcement в UI и memory surfaces, чтобы
  user-private data не показывались в обычном admin browsing.
- [ ] Phase 6: добавить optional root-server/global identity bindings и pairwise
  key support.
- [ ] Phase 7: добавить enterprise trust-provider SDK и первый SSO/IdP pilot.

## Definition of done

Дорожная карта закрыта, когда:

- пользователя можно добавить как member/child/guest через документированные
  QR/link flows;
- owner и co-owner управляют users, devices, memberships и revocation через
  shared runtime services;
- skills декларируют personalization и permission needs в manifests;
- SDK calls явно различают actor/current-user/subject semantics;
- policy enforcement защищает как минимум profile, memory, device, skill и tool
  operations;
- privacy zones соблюдаются product UI;
- все access changes и denied decisions дают auditable events.
