# Персонализация Phase 0 Contracts

Статус: реализованный contract anchor для Phase 0.

Дорожная карта:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchor:
`src/adaos/domain/personalization_access.py`.

Contract version:
`adaos.personalization_access.contract.v0`.

## Назначение

Phase 0 еще не включает authorization enforcement. Она фиксирует versioned
vocabulary, который дальше должны использовать services, SDK helpers, UI
surfaces и tests.

Контракт намеренно находится в `adaos.domain`, чтобы Phase 1 могла построить
storage и policy service поверх него без импортов UI, API или skill runtime.

## Scope Lattice

Первый scope lattice:

- `subnet`
- `workspace`
- `webspace`
- `scenario`
- `skill`
- `device_session`
- `user_private`
- `shared_workspace`

Правила:

- `owner` является implicit subnet administrator.
- Non-owner subjects получают scoped grants.
- `user_private` отделен от shared workspace data.
- Browser/device sessions являются policy subjects и revocation targets, а не
  только transport details.

## Versioned Schemas

Phase 0 contract определяет draft schemas:

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

Все records несут `schema_version = adaos.personalization_access.contract.v0`.
Phase 1 storage должен хранить достаточно version information, чтобы мигрировать
эти records без переписывания несвязанных profile, access-link или
conversation-memory state.

## Migration Stance

Текущие runtime data мапятся в этот контракт так:

- `Settings.owner_id` и `local-owner` становятся bootstrap owner subject.
- Текущие `UserProfileService` settings становятся `UserProfile.settings`, но
  access policy fields вроде `role`, `roles`, `membership` и `grants`
  отклоняются.
- Текущие `profile.settings` projections остаются compatibility views поверх
  будущего profile/preference service.
- Текущие `access_links` становятся device/session facts и revocation inputs.
- Browser scoped storage становится user preferences плюс device overrides, с
  local fallback на время миграции.

## Role Presets and Capabilities

Roles - это presets, а не enforcement primitives. Contract определяет стартовые
presets:

- `owner`
- `co_owner`
- `admin`
- `member`
- `child`
- `guest`

Capabilities - низкоуровневые строки, например:

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

Phase 1 может добавлять capabilities, но не должна менять separation между
role/profile: role и membership никогда не принадлежат profile settings.

## Actor Semantics

Contract разделяет:

- `actor`: authenticated subject, выполняющий action;
- `current_user`: user, attached к текущей session;
- `subject_user`: user, про которого выполняется operation;
- `service`: service identity для background work;
- `on_behalf_of`: user или service, чья authority используется;
- `session` и `device`: конкретные technical entry points.

Это нужно до sensitive conversational flows, которые могут предлагать
membership, device, recovery или tool actions.

## Join Flow Contracts

Contract называет четыре flow:

- `guest_join_link`
- `targeted_invite_link`
- `device_pairing_link`
- `admin_recovery_link`

`guest_join_link` публичный и не должен нести profile binding. Targeted invite,
device pairing и admin recovery не публичные и должны быть auditable.

## Threat Model

Phase 0 threat model покрывает:

- public QR abuse;
- targeted invite leakage;
- stolen или lost trusted devices;
- owner или co-owner key loss;
- skill privilege abuse;
- revoked live browser/Yjs/API sessions;
- cross-subnet identity correlation;
- unsafe recovery без previous trust factor.

Ответом пока не является full cryptographic isolation. Ближайший ответ - clear
scopes, grants, revocation, policy decisions и audit records.

## Audit Contract

Audit records - append-only domain facts с:

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

Generic audit logs не должны хранить raw private content. Private data systems
могут иметь отдельные retention policies, но это вне generic access audit
contract.

## Required Local Verification

Phase 0 локально проверяется командами:

- `python -m pytest tests/test_personalization_access_contracts.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
