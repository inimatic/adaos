# Персонализация Phase 6 Device Pairing and Recovery

Status: реализован vertical slice для device pairing links, admin recovery
links, device/session lifecycle и revocation-backed session invalidation.

Roadmap:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_access.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `tests/test_personalization_phase6_7.py`

## Реализованный slice

Phase 6 добавляет первый trusted-device lifecycle поверх контрактов Phase 0-5:

- создание `device_pairing_link` для существующего user/profile;
- public claim pairing link с нового browser/device;
- запись `DeviceKey` и `SessionKey` для paired devices;
- member self-service pairing, если policy дает `devices.add.self`;
- owner/admin-assisted `admin_recovery_link`;
- lost-device replacement: replacement device привязывается, old device/session
  revoke выполняется в том же flow;
- access-link denial для revoked device/session ids.

AdaOS Connect теперь показывает формы pairing и recovery. Созданные links
используют тот же public invite URL transport, что и Phase 5, а claim payload
добавляет `device_id`, `device_name`, key id и public key reference.

## Access boundary

Device pairing сам по себе не создает role или membership. Он привязывает
устройство к уже доверенному subject. Role/membership остается отдельным
admin-действием Phase 7.

Admin recovery авторизуется owner/co-owner, который выпустил recovery link.
Replacement device может claim link без уже активной owner session, но revoke
старого device аудируется на issuer, записанного в link.

## Текущие границы

Не реализовано в этом slice:

- WebAuthn/passkey-backed authenticators;
- recovery codes;
- owner key backup, co-owner recovery quorum и ownership transfer;
- QR image rendering для pairing/recovery links;
- direct websocket disconnect orchestration для already-connected sessions.

## Required Local Verification

Phase 6 локально проверяется командами:

- `PYTHONPATH=src python -m pytest tests/test_personalization_phase6_7.py`
- `PYTHONPATH=src python -m pytest tests/test_personalization_api_phase4_5.py tests/test_personalization_join_phase3.py`
- `npm run build` в `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
