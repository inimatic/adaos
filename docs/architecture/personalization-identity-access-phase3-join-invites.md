# Personalization Phase 3 Guest Join and Targeted Invites

Status: implemented backend join/invite slice; AdaOS Connect and Join Browser
UI are not implemented in this phase.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_access.py`
- `tests/test_personalization_join_phase3.py`

## Purpose

Phase 3 makes QR/link entry safe at the backend layer before device-pairing and
recovery UX are built. It covers public guest joins and targeted invites, while
keeping identity proof separate from profile hints.

This phase provides service contracts, policy checks, preview data, and cutoff
hooks. The visible owner/joiner experience is tracked separately as the AdaOS
Connect join UX and link-management phase.

## Guest Join

`create_guest_join_link(...)` creates an invite with:

- `kind = guest_join_link`;
- `role = guest`;
- no profile binding or profile hint;
- explicit scope;
- expiry;
- max session count;
- multi-session claim support.

Guest claims must use session subjects. Claiming a guest join as a user profile
is rejected.

## Targeted Invite

`create_targeted_invite_link(...)` creates an invite with:

- `kind = targeted_invite_link`;
- selected role preset;
- explicit scope;
- optional `profile_hint`;
- expiry;
- single-use semantics.

`profile_hint` is displayed as a hint only. It is not accepted as identity
proof, and the subnet still records the actual accepting subject.

## Consent Preview

`preview_invite(...)` returns the material a joining device needs to show before
acceptance:

- invite kind;
- target scope;
- role preset;
- expiry;
- profile hint, when allowed;
- current status;
- acceptance availability;
- max session count.

## Claim and Binding

`claim_invite(...)` rejects expired, reused, revoked, wrong-scope, or
over-capacity invite material. On successful claim it can issue the backing
grant, membership, and session facts.

`bind_session_to_profile(...)` covers the owner/co-owner path for attaching an
unknown session to a new or existing local profile.

## Revocation and Cutoff

`revoke_invite(...)` and `revoke_guest_join_sessions(...)` revoke:

- the invite;
- backing grants;
- sessions recorded in invite claims.

The service also accepts an optional `access_link_denier` hook. Runtime wiring
can pass `access_links.deny_link` or an equivalent adapter so browser/Yjs
authorization sees the cutoff through the existing access-link path. The Phase 3
tests cover the hook and session-policy cutoff; direct websocket disconnection
is still a runtime integration concern.

## Audit

The slice writes audit records for:

- invite creation;
- invite acceptance;
- grant/membership creation from invite acceptance;
- invite revocation;
- session binding;
- session revocation.

Audit metadata includes issuer, subject, scope, role, constraints, invite id,
grant id, and affected sessions.

## Current Boundaries

Implemented now:

- public guest join contract;
- targeted one-time invite contract;
- profile hint without identity proof;
- consent preview data;
- owner/admin session-to-profile binding primitive;
- invite rate limit;
- max sessions;
- bulk guest revocation;
- wrong-scope/expired/reused/revoked material rejection;
- session/access-link cutoff hook.

Not implemented in Phase 3:

- actual Join Browser UI wiring;
- API routes consumed by AdaOS Connect;
- QR/link transport parameterization in AdaOS Connect;
- direct websocket disconnect orchestration;
- device pairing and admin recovery flows.

## Required Local Verification

Phase 3 is locally verified by:

- `python -m pytest tests/test_personalization_join_phase3.py`
- `python -m pytest tests/test_personalization_access_contracts.py tests/test_personalization_access_kernel.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
