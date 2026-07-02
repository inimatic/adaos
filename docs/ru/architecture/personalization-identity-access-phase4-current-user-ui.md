# Персонализация Phase 4 Current-User Settings API and Browser UI

Status: реализован пользовательский vertical slice для current-user settings в
browser shell.

Roadmap:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_runtime.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `src/adaos/integrations/adaos-client/src/app/app.component.scss`
- `tests/test_personalization_api_phase4_5.py`
- `tests/test_personalization_profile_phase2.py`

## Реализованный slice

Phase 4 выводит Phase 2 profile/preference service в runtime API и browser
header panel:

- `GET /api/personalization/current-user/header-settings`
- `GET /api/personalization/current-user/profile`
- `PATCH /api/personalization/current-user/profile`
- `GET /api/personalization/current-user/preferences`
- `PATCH /api/personalization/current-user/preferences`
- `GET /api/personalization/policy/explain`

В шапке браузера появился current-user control. Panel загружает authoritative
header settings из API, позволяет signed-in user редактировать разрешенные
profile/preference fields и обновляет шапку после save.

## Access boundary

`role`, `membership`, `grant` и связанные access-policy fields не являются
profile settings. API отклоняет попытку записать их через profile patch
structured denial. UI показывает role/access status только read-only.

Browser не становится source of truth для preferences. Он вызывает runtime API,
а API проводит write через `UserProfileService` и personalization access/audit
service.

## Текущие границы

Не реализовано в этой фазе:

- avatar/initial rendering polish в header chip;
- identity switcher для sessions с несколькими valid identities;
- полноценный owner/admin user-management surface;
- cross-surface E2E tests кроме strict browser build smoke test.

## Required Local Verification

Phase 4 локально проверяется командами:

- `PYTHONPATH=src python -m pytest tests/test_personalization_api_phase4_5.py`
- `PYTHONPATH=src python -m pytest tests/test_personalization_profile_phase2.py`
- `npm run build` в `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
