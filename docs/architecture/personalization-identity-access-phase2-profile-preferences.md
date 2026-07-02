# Personalization Phase 2 Profile and Preferences

Status: implemented service/SDK slice for profile/preferences; browser settings
UI is not implemented in this phase.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/user/profile.py`
- `src/adaos/sdk/data/profile.py`
- `src/adaos/sdk/data/ctx.py`
- `src/adaos/services/scenario/projection_service.py`
- `tests/test_personalization_profile_phase2.py`

## Purpose

Phase 2 keeps the existing profile settings compatibility API while moving the
runtime contract toward explicit profile and preference records. Role and
membership stay out of profile data.

This phase is intentionally backend-facing. The visible web header and settings
panel are tracked in the roadmap as the current-user settings API/browser UI
phase.

## Implemented Surface

`UserProfileService` still reads and writes the compatibility key:

```text
users/<user_id>/settings
```

It now also writes:

- `users/<user_id>/profile.v0`
- `users/<user_id>/preferences.v0`

The versioned profile record uses the Phase 0 `UserProfile` contract and rejects
access-policy keys such as `role`, `roles`, `membership`, `memberships`,
`grant`, and `grants`.

Preferences use the Phase 0 `Preference` contract and can be updated separately
from profile settings.

## SDK Compatibility

Existing helpers remain:

- `profile_get_settings`
- `profile_update_settings`
- `ctx.current_user.get_profile_settings()`

New helpers expose the Phase 2 surface:

- `profile_get_profile`
- `profile_get_preferences`
- `profile_update_preferences`
- `profile_get_header_settings`
- `ctx.current_user.profile()`
- `ctx.current_user.preferences()`
- `ctx.current_user.update_preferences(...)`
- `ctx.current_user.header_settings()`

## Header Settings

The client-facing header settings shape includes:

- display name and preferred name;
- locale, language, and timezone;
- theme;
- memory/privacy preference;
- current subnet/workspace hints;
- read-only role status;
- device trust status.

`role_status.editable` is always `false` in this slice. Role is status derived
from membership/policy, not a profile field.

## Projection

`ProjectionService` keeps the existing `current_user/profile.settings` KV path
and now also routes `current_user/profile.preferences` to the preference store.
Yjs projection remains manifest-driven through the existing projection
mechanism; paths can use `{user_id}` templating as before.

## Audit

Profile and preference updates call the Phase 1 access kernel and emit redacted
audit records:

- `profile.updated`
- `preference.updated`

The audit records include keys and metadata, not private profile/preference
values.

## Current Boundaries

Implemented now:

- compatibility settings API preserved;
- versioned profile records;
- separate preference records;
- role/membership rejection in profile settings;
- SDK current-user profile/preference helpers;
- header settings model;
- KV projection for profile preferences;
- redacted profile/preference audit.

Not implemented in Phase 2:

- browser UI wiring for the header settings panel;
- current-user settings API routes for the web client;
- global API middleware enforcement;
- full user-private data-zone enforcement outside profile/preferences;
- encrypted private preferences.

## Required Local Verification

Phase 2 is locally verified by:

- `python -m pytest tests/test_personalization_profile_phase2.py`
- `python -m pytest tests/test_personalization_access_contracts.py tests/test_personalization_access_kernel.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
