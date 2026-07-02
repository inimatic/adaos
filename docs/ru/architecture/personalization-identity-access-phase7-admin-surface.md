# Персонализация Phase 7 Owner and Admin Surface

Status: реализован vertical slice для owner/admin shared-service API и AdaOS
Connect control surface.

Roadmap:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_access.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `tests/test_personalization_phase6_7.py`

## Реализованный slice

Phase 7 открывает access foundation через shared runtime APIs:

- `GET /api/personalization/admin/summary`
- `POST /api/personalization/admin/grants`
- `POST /api/personalization/admin/devices/{device_id}/revoke`
- `POST /api/personalization/admin/sessions/{session_id}/revoke`

Summary возвращает users, profile metadata, devices, sessions, memberships,
grants, invites, recovery actions и последние audit records. Profile content в
admin view остается metadata-only.

AdaOS Connect теперь содержит owner/admin controls для:

- common role preset grants;
- device pairing links;
- admin recovery links;
- device revoke;
- session revoke;
- compact summary по devices, sessions, memberships и recovery actions.

## Access boundary

UI не является source of truth. Все admin actions идут через
`PersonalizationAccessService`, policy checks и audit records. Roles остаются
membership/grant facts, а не profile fields.

Несколько администраторов выражаются grants вроде `co_owner`, `admin` или
будущих scoped admin roles. Phase 7 добавляет API path для таких grants, но еще
не дает полный capability matrix editor.

## Текущие границы

Не реализовано в этом slice:

- dedicated user-management skill;
- Pending Actions integration для conversational admin approvals;
- full audit-history drill-down;
- custom capability editor beyond preset grants;
- privacy-zone enforcement beyond metadata-only admin profile summary.

## Required Local Verification

Phase 7 локально проверяется командами:

- `PYTHONPATH=src python -m pytest tests/test_personalization_phase6_7.py`
- `npm run build` в `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
