# Персонализация Phase 5 AdaOS Connect Join UX and Link Management

Status: реализован vertical slice для guest links, targeted invites,
preview/claim, listing и revocation через API и browser UI.

Roadmap:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_runtime.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/services/settings.py`
- `src/adaos/services/access_links.py`
- `src/adaos/integrations/adaos-backend/backend/io/bus/hubRouteProxy.ts`
- `src/adaos/integrations/adaos-backend/backend/webauthn.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `src/adaos/integrations/adaos-client/src/app/app.component.scss`
- `tests/test_personalization_api_phase4_5.py`
- `tests/test_personalization_join_phase3.py`

## Реализованный slice

Phase 5 делает Phase 3 join/invite semantics доступными из browser shell:

- owner-authenticated guest link creation;
- owner-authenticated targeted invite creation;
- public invite preview;
- public invite claim;
- owner-authenticated invite listing;
- owner-authenticated invite revoke;
- owner-authenticated guest-session bulk revoke.

В шапке браузера появился AdaOS Connect control. Owner может создать и
скопировать guest link для public/session-bound доступа и targeted invite link
для named user вроде Маши. Joining browser, открывший
`?adaos_invite=<id>`, получает preview со scope, role, expiry, profile hint при
наличии и explicit acceptance action. Созданные ссылки отображаются с QR,
когда API возвращает `claim_url`, поэтому public guest и device-to-device flows
могут использовать один и тот же link material как copyable link или scanable
code.

Invite URLs теперь используют AdaOS Connect code flow, а не переносят target
subnet и root hub endpoint прямо в URL. Видимая браузеру ссылка:

```text
https://inimatic.com/?mode=registration&user_code=DF0B-2729&zone=ru
```

Root хранит invite payload в том же temporary `device_code:*` session space,
который используется для owner registration, с `purpose = personalization_invite`.
В URL остаются только user-facing code и zone. Joining browser делает
`GET /v1/connect/sessions/{user_code}` в этой zone, получает invite id, target
subnet и root hub base, затем делает preview/claim через target hub.
`?adaos_invite=<id>` остается compatibility path, но новые guest/targeted links
должны быть code+zone AdaOS Connect links.

Local hub API не должен молча откатываться к длинным parameterized links. Если
root session не создана, `claim_url` пустой, а `claim_url_error` возвращает
`root_invite_session_unavailable`. Единственный source для token в этом
registration path - `Settings.root_token`, который читается из
`node.yaml root.root_token` или `ROOT_TOKEN`; этот flow не должен подбирать
токен из нескольких env aliases.

## Access boundary

Guest joins остаются session-bound и profile-unbound. API отклоняет claim guest
link как personal user profile.

Targeted invites остаются one-time по умолчанию и показываются как private
material. Profile hint остается hint, а не identity proof. Accepting browser
передает фактический subject id во время claim.

Revocation вызывает personalization service и access-link denial adapter, чтобы
последующие browser/Yjs admission checks отклоняли revoked sessions без ручных
database edits.

Owner/admin panel также показывает audit-history preview: последние audit
events из summary API раскрываются в компактный drill-down с raw payload. Это
diagnostic/manageability surface, а не замена Phase 1 audit store.

## Текущие границы

Не реализовано в этой фазе:

- profile picker/create UX вместо free-form profile id entry;
- direct websocket disconnect orchestration для already-connected browser
  sessions;

## Required Local Verification

Phase 5 локально проверяется командами:

- `PYTHONPATH=src python -m pytest tests/test_personalization_api_phase4_5.py`
- `PYTHONPATH=src python -m pytest tests/test_personalization_join_phase3.py`
- `npm run build` в `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
