# Дорожная карта device access

Целевое состояние: [Доступ устройств и браузеры](device-access-and-browsers.md)

## Рабочие принципы

- [x] Authoritative access model живёт в core runtime.
- [x] Сначала появляется reusable SDK helper layer, потом множится skill-local logic.
- [x] Bootstrap issuance отделяется от долгоживущей access policy.
- [x] Browser links и member links управляются в одной operator mental model.
- [x] `web_desktop` остаётся компактным за счёт переноса section operations в settings modals.

## Core access model

- [x] Переименовать desktop surface из `Applications` в `Devices`.
- [x] Зафиксировать и использовать термины `device`, `client`, `access link`, `detach`, `display_name`.
- [x] Ввести runtime-owned access link registry на durable state.
- [x] Поддержать browser links по `device_id`.
- [x] Поддержать member links по `node_id`.
- [x] Хранить display name, lifetime mode, expiry, revocation, last seen, connectivity и webspace affinity.
- [x] Опубликовать SDK helpers в `sdk.data.access_links`.

## Enforcement и lifecycle

- [x] Проверять browser policy на live ingress по `device_id`.
- [x] Гарантировать передачу `X-AdaOS-Device-Id` в browser HTTP requests.
- [x] Проверять member policy на hub-side member hello или registration.
- [x] Отклонять revoked и expired links до превращения их в активные runtime sessions.
- [ ] Добавить настоящий issuer-side autorotation для permanent browser access.
- [ ] Разнести revocation на всё активное server-side browser session state.

## Browser observability

- [x] Выпустить `browsers_skill` как первого потребителя access-link registry.
- [x] Публиковать browser inventory в Yjs projections.
- [x] Дать skill actions для rename, lifetime и detach.
- [x] Представить `Devices` и `Clients` как отдельные browser groups.
- [x] Игнорировать bootstrap approvals, которые так и не превратились в реальное browser usage.
- [x] Не хранить архив просроченных browser clients.
- [ ] Явно группировать browser inventory по last/current webspace в operator UI.
- [ ] Довести parity browser settings между transient client modal и skill-hosted modal flow.

## `web_desktop` как device shell

- [x] Добавить вход в `Browsers` внутри панели `Devices`.
- [x] Заменить per-section action rows одной settings affordance.
- [x] Перенести `Apps`, `Marketplace`, `Hide`, rename, lifetime и `Detach` в device settings UX.
- [x] Сохранить короткие mobile labels и icon-first UX там, где это нужно.
- [ ] Провести все device settings actions через один стабильный generic modal contract.
- [ ] Добавить confirmation и richer status messaging для destructive detach flows.

## Node-scoped operations внутри device context

- [x] Оставить `Apps` привязанным к текущей ноде.
- [x] Оставить `Marketplace` привязанным к текущей ноде.
- [x] Фильтровать `Marketplace` по items, ещё не установленным на этой ноде.
- [x] Сохранить `Hide` или `Show` как presentation-only desktop state.
- [ ] Свести node capability management и device access management к одному reusable settings schema.

## Сведение browser и member semantics

- [x] Использовать одну access policy model для браузеров и member nodes.
- [x] Поддержать rename member devices через runtime-controlled node naming flows.
- [x] Поддержать detach для connected members через link manager unregistration.
- [ ] Определить offline behavior для member nodes, detached в отключённом состоянии.
- [ ] Добавить reconciler между durable access policy и transient runtime link state.

## Voice и automation follow-up

- [ ] Использовать `display_name` как канонический voice-facing label устройства.
- [ ] Открыть device policies для automation и assistant skills.
- [ ] Поддержать operator и assistant intents вида:
  - [ ] "отключи телевизор в гостиной"
  - [ ] "открой приложения на кухонном планшете"
  - [ ] "дай этому браузеру доступ на один день"

## Рекомендуемый порядок выполнения

- [x] Этап 0 и Этап 1: словарь и core access model.
- [x] Этап 2: ingress enforcement.
- [x] Этап 3: первый browser observability slice.
- [~] Этап 4: `web_desktop` device shell.
- [ ] Этап 5: unified node-scoped settings contract.
- [ ] Этап 6: cleanup для browser/member convergence.
- [ ] Этап 7: issuer-side autorotation.
- [ ] Этап 8: voice и automation integration.
