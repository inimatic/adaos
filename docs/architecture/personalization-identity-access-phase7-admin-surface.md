# Personalization Phase 7 Owner and Admin Surface

Status: implemented vertical slice for owner/admin shared-service API and
AdaOS Connect control surface.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_access.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `tests/test_personalization_phase6_7.py`

## Implemented Slice

Phase 7 exposes the access foundation through shared runtime APIs:

- `GET /api/personalization/admin/summary`
- `POST /api/personalization/admin/grants`
- `POST /api/personalization/admin/devices/{device_id}/revoke`
- `POST /api/personalization/admin/sessions/{session_id}/revoke`

The summary returns users, profile metadata, devices, sessions, memberships,
grants, invites, recovery actions, and recent audit records. Profile content is
kept metadata-only for the admin view.

AdaOS Connect now includes owner/admin controls for:

- common role preset grants;
- device pairing links;
- admin recovery links;
- device revoke;
- session revoke;
- a compact summary of devices, sessions, memberships, and recovery actions.

## Access Boundary

The UI is not a source of truth. All admin actions route through
`PersonalizationAccessService`, policy checks, and audit records. Roles remain
membership/grant facts, not profile fields.

Multiple administrators are represented by grants such as `co_owner`, `admin`,
or future scoped admin roles. Phase 7 adds the API path for those grants, but
does not yet expose a full capability matrix editor.

## Current Limits

Not implemented in this slice:

- a dedicated user-management skill;
- Pending Actions integration for conversational admin approvals;
- full audit-history drill-down;
- custom capability editor beyond preset grants;
- privacy-zone enforcement beyond metadata-only admin profile summary.

## Required Local Verification

Phase 7 is locally verified by:

- `PYTHONPATH=src python -m pytest tests/test_personalization_phase6_7.py`
- `npm run build` in `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
