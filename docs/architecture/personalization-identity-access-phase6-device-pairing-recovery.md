# Personalization Phase 6 Device Pairing and Recovery

Status: implemented vertical slice for device pairing links, admin recovery
links, device/session lifecycle, and revocation-backed session invalidation.

Roadmap:
[Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/services/personalization_access.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/integrations/adaos-client/src/app/app.component.ts`
- `src/adaos/integrations/adaos-client/src/app/app.component.html`
- `tests/test_personalization_phase6_7.py`

## Implemented Slice

Phase 6 adds the first trusted-device lifecycle over the existing Phase 0-5
contracts:

- `device_pairing_link` creation for an existing user/profile;
- public claim of a device pairing link from the new browser/device;
- `DeviceKey` and `SessionKey` records for paired devices;
- member self-service pairing when policy grants `devices.add.self`;
- owner/admin-assisted `admin_recovery_link`;
- lost-device replacement that binds the replacement device and revokes old
  device/session records in one flow;
- access-link denial for revoked device/session ids.

AdaOS Connect now exposes pairing and recovery forms. The created links use the
same public invite URL transport as Phase 5, while the claim payload adds
`device_id`, `device_name`, key id, and public key reference fields.

## Access Boundary

Device pairing does not create role or membership by itself. It binds a device
to an already trusted subject. Role/membership remains a separate Phase 7
admin action.

Admin recovery is authorized by the owner/co-owner that issued the recovery
link. The replacement device can claim the link without already having an owner
session, but the old-device revocation is audited against the issuer recorded
in the link.

## Current Limits

Not implemented in this slice:

- WebAuthn/passkey-backed authenticators;
- recovery codes;
- owner key backup, co-owner recovery quorum, and ownership transfer;
- QR image rendering for pairing/recovery links;
- direct websocket disconnect orchestration for already-connected sessions.

## Required Local Verification

Phase 6 is locally verified by:

- `PYTHONPATH=src python -m pytest tests/test_personalization_phase6_7.py`
- `PYTHONPATH=src python -m pytest tests/test_personalization_api_phase4_5.py tests/test_personalization_join_phase3.py`
- `npm run build` in `src/adaos/integrations/adaos-client`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
