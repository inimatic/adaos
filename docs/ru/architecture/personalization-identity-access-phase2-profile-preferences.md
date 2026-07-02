# Персонализация Phase 2 Profile and Preferences

Статус: реализованный vertical slice для profile/preferences.

Дорожная карта:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/user/profile.py`
- `src/adaos/sdk/data/profile.py`
- `src/adaos/sdk/data/ctx.py`
- `src/adaos/services/scenario/projection_service.py`
- `tests/test_personalization_profile_phase2.py`

## Назначение

Phase 2 сохраняет существующий compatibility API для profile settings, но
переводит runtime contract к явным profile и preference records. Role и
membership остаются вне profile data.

## Реализованная поверхность

`UserProfileService` по-прежнему читает и пишет compatibility key:

```text
users/<user_id>/settings
```

Он также пишет:

- `users/<user_id>/profile.v0`
- `users/<user_id>/preferences.v0`

Versioned profile record использует Phase 0 `UserProfile` contract и отклоняет
access-policy keys: `role`, `roles`, `membership`, `memberships`, `grant`,
`grants`.

Preferences используют Phase 0 `Preference` contract и обновляются отдельно от
profile settings.

## SDK Compatibility

Существующие helpers сохранены:

- `profile_get_settings`
- `profile_update_settings`
- `ctx.current_user.get_profile_settings()`

Новые helpers открывают Phase 2 surface:

- `profile_get_profile`
- `profile_get_preferences`
- `profile_update_preferences`
- `profile_get_header_settings`
- `ctx.current_user.profile()`
- `ctx.current_user.preferences()`
- `ctx.current_user.update_preferences(...)`
- `ctx.current_user.header_settings()`

## Header Settings

Client-facing header settings включают:

- display name и preferred name;
- locale, language и timezone;
- theme;
- memory/privacy preference;
- current subnet/workspace hints;
- read-only role status;
- device trust status.

`role_status.editable` всегда `false` в этом slice. Role является status из
membership/policy, а не profile field.

## Projection

`ProjectionService` сохраняет существующий `current_user/profile.settings` KV
path и теперь также маршрутизирует `current_user/profile.preferences` в
preference store. Yjs projection остается manifest-driven через существующий
projection mechanism; paths могут использовать `{user_id}` templating.

## Audit

Profile и preference updates вызывают Phase 1 access kernel и пишут redacted
audit records:

- `profile.updated`
- `preference.updated`

Audit records содержат keys и metadata, но не private profile/preference values.

## Текущие границы

Реализовано сейчас:

- compatibility settings API preserved;
- versioned profile records;
- separate preference records;
- role/membership rejection в profile settings;
- SDK current-user profile/preference helpers;
- header settings model;
- KV projection для profile preferences;
- redacted profile/preference audit.

Не реализовано в Phase 2:

- browser UI wiring для header settings panel;
- global API middleware enforcement;
- full user-private data-zone enforcement вне profile/preferences;
- encrypted private preferences.

## Required Local Verification

Phase 2 локально проверяется командами:

- `python -m pytest tests/test_personalization_profile_phase2.py`
- `python -m pytest tests/test_personalization_access_contracts.py tests/test_personalization_access_kernel.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
