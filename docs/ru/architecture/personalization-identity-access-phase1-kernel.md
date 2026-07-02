# Персонализация Phase 1 Access Kernel

Статус: реализованный backend kernel для Phase 1.

Дорожная карта:
[Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md).

Code anchors:

- `src/adaos/domain/personalization_access.py`
- `src/adaos/services/personalization_access.py`
- `tests/test_personalization_access_kernel.py`

## Назначение

Phase 1 превращает контракты Phase 0 в локальный backend decision layer. Он
намеренно находится ниже UI, SDK convenience helpers и join-browser flows:
пользовательские поверхности можно добавлять позже, но они должны обращаться к
общему сервису, а не принимать решения доступа из UI state или skill-local
state.

Реализация сейчас является небольшим JSON-backed service kernel. Storage backend
можно заменить; важна зафиксированная семантика поведения.

## Хранимые факты

`PersonalizationAccessStore` сохраняет contract dictionaries для:

- users;
- profiles;
- user keys;
- device keys;
- sessions;
- memberships;
- grants;
- invites;
- recovery actions;
- revocations;
- append-only audit records.

Records сохраняют Phase 0 `schema_version`, чтобы будущие миграции могли
заменить storage backend без изменения subject/session/grant semantics.

## Policy Kernel

`PersonalizationAccessService.evaluate(...)` реализует форму Phase 1:

```text
is_allowed(actor, action, subject, scope, resource, context)
```

Он возвращает `PolicyDecision` с:

- `decision`: `allow` или `deny`;
- `reason_code`;
- matched `grant_ids`, где применимо;
- явными actor, subject, scope и resource.

Текущие правила намеренно минимальны:

- настроенный owner subject имеет implicit subnet-admin authority;
- role presets разворачиваются в Phase 0 capability bundles;
- explicit grants и memberships учитываются только если они active,
  unexpired и scope-compatible;
- actions могут требовать `approval_id` через grant constraints;
- session actors разворачиваются в attached user только если session active и
  unexpired;
- revoked devices блокируют attached sessions.

Это пока не полноценный API authorization middleware. Phase 2+ должны провести
SDK, API и projection access через kernel.

## Revocation Rules

Phase 1 фиксирует первое propagation behavior:

- revoked grants сразу перестают matching;
- revoked sessions запрещают session-actor decisions;
- revoked devices отзывают active sessions с тем же device id;
- user-key, device, session и grant revocations пишут durable revocation facts
  и audit records.

Позже можно добавить live websocket/Yjs disconnect propagation и encrypted
secret isolation, но durable authority state теперь имеет одно место хранения.

## Invite and Recovery Guards

Сервис отклоняет stale privileged material до появления UI flows:

- accepted, expired или revoked invites нельзя claim повторно;
- expired invites помечаются как `expired`;
- accepted или revoked recovery actions нельзя complete повторно.

Targeted invite, public guest join, device pairing и recovery UX остаются
следующими фазами. Phase 1 только задает replay/stale-write guards, которыми эти
flows должны пользоваться.

## Audit Surface

Каждая service mutation и policy decision пишет append-only `AuditRecord`.
Audit query helpers поддерживают фильтрацию по:

- actor;
- subject;
- scope;
- device;
- session;
- source;
- event type;
- decision;
- time range.

Generic audit records хранят metadata и redacted diffs, а не private content.

## Текущие границы

Реализовано сейчас:

- local JSON-backed store и service;
- owner implicit admin;
- role-preset capability expansion;
- grant и membership evaluation;
- session/device-aware evaluation;
- structured allow/deny decisions;
- audit append и query helpers;
- invite/recovery replay guards;
- local-owner baseline regression.

Не реализовано в Phase 1:

- API middleware integration;
- client settings/profile UI;
- guest join и targeted invite browser flows;
- device pairing UX;
- recovery без owner/co-owner;
- root-server/global identity trust;
- encrypted private data или secret isolation.

## Required Local Verification

Phase 1 локально проверяется командами:

- `python -m pytest tests/test_personalization_access_contracts.py tests/test_personalization_access_kernel.py`
- `python -m pytest tests/test_conversation_contracts.py tests/test_event_envelope.py tests/test_infrascope_event_sources.py`
- `python -m mkdocs build --strict --site-dir .tmp_mkdocs_personalization`
- `git diff --check`
