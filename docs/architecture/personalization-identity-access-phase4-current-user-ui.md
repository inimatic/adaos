# Personalization Phase 4 Current-User Settings API and Browser UI

Status: implemented vertical slice for current-user settings in the browser
shell.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_runtime.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `src/adaos/integrations/adaos-client/src/app/app.component.scss`
- `tests/test_personalization_api_phase4_5.py`
- `tests/test_personalization_profile_phase2.py`

## Implemented Slice

Phase 4 exposes the Phase 2 profile/preference service through runtime API
routes and a browser header panel:

- `GET /api/personalization/current-user/header-settings`
- `GET /api/personalization/current-user/profile`
- `PATCH /api/personalization/current-user/profile`
- `GET /api/personalization/current-user/preferences`
- `PATCH /api/personalization/current-user/preferences`
- `GET /api/personalization/options`
- `GET /api/personalization/policy/explain`

The browser header now has a current-user control. The panel loads the
authoritative header settings from the API, lets the signed-in user edit
allowed profile/preference fields, and refreshes the header after saving.
Language, locale, timezone, device, and scope controls are backed by API
options instead of free-form text where the runtime can provide a concrete
enumeration.

## Access Boundary

`role`, `membership`, `grant`, and related access-policy fields stay outside
profile settings. The API rejects attempts to write them through profile
patches with a structured denial. The UI only displays role/access status as
read-only metadata.

The browser does not become a source of truth for preferences. It calls the
runtime API, which routes writes through `UserProfileService` and the
personalization access/audit service.

Current identity resolution is explicitly marked as `owner_settings_fallback`.
It is valid for the local owner session only. Invited/guest users still require
the Phase 6+ session-to-profile binding before `current_user` stops being an
owner fallback.

## Current Limits

Not implemented in this phase:

- avatar/initial rendering polish in the header chip;
- identity switcher for sessions with multiple valid identities;
- full owner/admin user-management surface;
- cross-surface E2E tests beyond the strict browser build smoke test.

## Required Local Verification

Phase 4 is locally verified by:

- `PYTHONPATH=src python -m pytest tests/test_personalization_api_phase4_5.py`
- `PYTHONPATH=src python -m pytest tests/test_personalization_profile_phase2.py`
- `npm run build` in `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
