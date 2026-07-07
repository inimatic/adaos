# Personalization Phase 5 AdaOS Connect Join UX and Link Management

Status: implemented vertical slice for guest links, targeted invites,
preview/claim, listing, and revocation through API and browser UI.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_runtime.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/services/access_links.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `src/adaos/integrations/adaos-client/src/app/app.component.scss`
- `tests/test_personalization_api_phase4_5.py`
- `tests/test_personalization_join_phase3.py`

## Implemented Slice

Phase 5 makes the Phase 3 join/invite semantics usable from the browser shell:

- owner-authenticated guest link creation;
- owner-authenticated targeted invite creation;
- public invite preview;
- public invite claim;
- owner-authenticated invite listing;
- owner-authenticated invite revoke;
- owner-authenticated guest-session bulk revoke.

The browser header exposes an AdaOS Connect control. Owners can create and copy
guest links for public/session-bound access and targeted invite links for named
users such as Masha. A joining browser that opens `?adaos_invite=<id>` gets a
preview with scope, role, expiry, profile hint when present, and an explicit
acceptance action. Created links are listed with QR rendering when `claim_url`
is present, so classroom, museum, and device-to-device flows can use either
copyable links or scanable codes from the same source material.

Invite URLs are generated from the configured public app base, normally
`https://inimatic.com`, and carry the target subnet plus root hub endpoint
parameters. A joining browser uses those parameters to route preview/claim
requests to the target hub instead of assuming the local `127.0.0.1` API.

## Access Boundary

Guest joins are session-bound and profile-unbound. The API rejects attempts to
claim a guest link as a personal user profile.

Targeted invites remain one-time by default and are shown as private material.
The profile hint is still a hint, not identity proof. The accepting browser
submits the actual subject id during claim.

Revocation calls the personalization service and the access-link denial adapter
so subsequent browser/Yjs admission checks reject revoked sessions without
manual database edits.

The owner/admin panel also exposes an audit-history preview. It shows the
latest audit events returned by the summary API with a compact drill-down to
the raw event payload. This is a diagnostic and manageability surface, not a
replacement for the Phase 1 audit store.

## Current Limits

Not implemented in this phase:

- profile picker/create UX instead of free-form profile id entry;
- direct websocket disconnect orchestration for already-connected browser
  sessions.

## Required Local Verification

Phase 5 is locally verified by:

- `PYTHONPATH=src python -m pytest tests/test_personalization_api_phase4_5.py`
- `PYTHONPATH=src python -m pytest tests/test_personalization_join_phase3.py`
- `npm run build` in `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
