# Персонализация Phase 3 Guest Join and Targeted Invites

Статус: реализованный backend join/invite slice.

Дорожная карта:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_access.py`
- `tests/test_personalization_join_phase3.py`

## Назначение

Phase 3 делает QR/link entry безопасным на backend layer до реализации device
pairing и recovery UX. Slice покрывает public guest joins и targeted invites,
при этом identity proof остается отдельно от profile hints.

## Guest Join

`create_guest_join_link(...)` создает invite с:

- `kind = guest_join_link`;
- `role = guest`;
- без profile binding и profile hint;
- explicit scope;
- expiry;
- max session count;
- multi-session claim support.

Guest claims должны использовать session subjects. Claim guest join как user
profile отклоняется.

## Targeted Invite

`create_targeted_invite_link(...)` создает invite с:

- `kind = targeted_invite_link`;
- выбранным role preset;
- explicit scope;
- optional `profile_hint`;
- expiry;
- single-use semantics.

`profile_hint` показывается только как hint. Он не считается proof of identity,
и subnet все равно записывает фактический accepting subject.

## Consent Preview

`preview_invite(...)` возвращает данные, которые joining device должен показать
перед acceptance:

- invite kind;
- target scope;
- role preset;
- expiry;
- profile hint, если он разрешен;
- current status;
- acceptance availability;
- max session count.

## Claim and Binding

`claim_invite(...)` отклоняет expired, reused, revoked, wrong-scope и
over-capacity invite material. При успешном claim он может выдать backing grant,
membership и session facts.

`bind_session_to_profile(...)` покрывает owner/co-owner path для привязки
unknown session к новому или существующему local profile.

## Revocation and Cutoff

`revoke_invite(...)` и `revoke_guest_join_sessions(...)` отзывают:

- invite;
- backing grants;
- sessions, записанные в invite claims.

Сервис также принимает optional `access_link_denier` hook. Runtime wiring может
передать `access_links.deny_link` или equivalent adapter, чтобы browser/Yjs
authorization видел cutoff через существующий access-link path. Phase 3 tests
покрывают hook и session-policy cutoff; прямое websocket disconnection остается
runtime integration concern.

## Audit

Slice пишет audit records для:

- invite creation;
- invite acceptance;
- grant/membership creation из invite acceptance;
- invite revocation;
- session binding;
- session revocation.

Audit metadata включает issuer, subject, scope, role, constraints, invite id,
grant id и affected sessions.

## Текущие границы

Реализовано сейчас:

- public guest join contract;
- targeted one-time invite contract;
- profile hint без identity proof;
- consent preview data;
- owner/admin session-to-profile binding primitive;
- invite rate limit;
- max sessions;
- bulk guest revocation;
- wrong-scope/expired/reused/revoked material rejection;
- session/access-link cutoff hook.

Не реализовано в Phase 3:

- actual Join Browser UI wiring;
- QR/link transport parameterization в AdaOS Connect;
- direct websocket disconnect orchestration;
- device pairing и admin recovery flows.

## Required Local Verification

Phase 3 локально проверяется командами:

- `python -m pytest tests/test_personalization_join_phase3.py`
- `python -m pytest tests/test_personalization_access_contracts.py tests/test_personalization_access_kernel.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
