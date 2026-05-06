# Дорожная карта device access

## Цель

Перевести AdaOS от набора разрозненных browser-, member-, marketplace- и desktop-behavior в единый reusable device access plane.

Целевое состояние описано в
[Доступ устройств и браузеры](device-access-and-browsers.md).

## Принципы

- authoritative model живёт в core runtime
- сначала появляется стабильный SDK helper layer, потом множится skill logic
- bootstrap issuance отделяется от долгоживущей access policy
- browser links и member links управляются в одной operator mental model
- `web_desktop` остаётся компактным за счёт переноса section operations в settings modal

## Этап 0. Зафиксировать словарь и UI intent

Результаты:

- переименовать desktop surface из `Applications` в `Devices`
- зафиксировать термины `device`, `client`, `access link`, `detach`, `display_name`
- зафиксировать разделение между:
  - node capability management: `Apps`, `Marketplace`, `Hide`
  - access management: rename, lifetime, detach

Критерий выхода:

- команда использует единый словарь в коде, документации и UX review

## Этап 1. Ввести core access link registry

Результаты:

- создать runtime-owned access link registry на durable state
- поддержать browser links по `device_id`
- поддержать member links по `node_id`
- хранить display name, lifetime mode, expiry, revocation, last seen, connectivity и webspace affinity
- опубликовать SDK helpers в `sdk.data.access_links`

Критерий выхода:

- skills и runtime services могут читать и менять access links без дублирования логики хранения

## Этап 2. Подключить runtime enforcement на live ingress

Результаты:

- проверять browser policy на Yjs или control ingress по `device_id`
- гарантировать передачу `X-AdaOS-Device-Id` в browser HTTP requests
- проверять member policy на hub-side member hello или registration
- отклонять revoked и expired links до превращения их в активные runtime sessions

Критерий выхода:

- `detach` и expiry становятся runtime truth, а не только UI annotation

## Этап 3. Выпустить `browsers_skill` как первого потребителя

Результаты:

- публиковать browser inventory в Yjs projections
- дать skill actions для rename, lifetime и detach
- представить `Devices` и `Clients` как отдельные browser groups
- хранить только браузеры, которые реально были использованы

Критерий выхода:

- browser observability больше не зависит от ad hoc inspection сырых session internals

## Этап 4. Перестроить `web_desktop` вокруг устройств

Результаты:

- переименовать top-level desktop surface в `Devices`
- добавить вход в `Browsers`
- заменить per-section action rows одной settings affordance
- перенести `Apps`, `Marketplace`, `Hide`, rename, lifetime и `Detach` в settings modal
- сохранить короткие mobile labels и icon-first UX там, где это нужно

Критерий выхода:

- desktop остаётся компактным на mobile, но сохраняет полный control surface

## Этап 5. Сохранить node-scoped operations внутри device context

Результаты:

- оставить `Apps` привязанным к текущей ноде
- оставить `Marketplace` привязанным к текущей ноде и отфильтрованным по уже неустановленным items
- сохранить visibility controls вроде `Hide` или `Show` как presentation-only settings

Критерий выхода:

- device management и capability management разведены по смыслу, но доступны из одной settings shell

## Этап 6. Свести browser и member lifecycle semantics

Результаты:

- использовать одну policy model для браузеров и member nodes
- поддержать rename member devices через runtime-controlled node naming flows
- поддержать detach для connected members через link manager unregistration
- определить offline behavior для member nodes, detached в отключённом состоянии

Критерий выхода:

- оператору не нужно держать две разные ментальные модели для браузеров и member nodes

## Этап 7. Добавить настоящий issuer-side autorotation

Результаты:

- превратить `autorotate` из policy flag в полноценную token lifecycle capability
- ротировать permanent browser access без ручного re-pair
- обеспечить revocation fan-out в активное server-side session state

Критерий выхода:

- постоянный device access долговечен, но не превращается в навсегда статичный токен

## Этап 8. Добавить voice и automation integration

Результаты:

- использовать `display_name` как канонический voice-facing label устройства
- открыть device policies для automation и assistant skills
- поддержать intent-паттерны вида:
  - "отключи телевизор в гостиной"
  - "открой приложения на кухонном планшете"
  - "дай этому браузеру доступ на один день"

Критерий выхода:

- управление device access становится частью assistant platform, а не только operator UI

## Рекомендуемый порядок реализации

1. Этап 0 и Этап 1
2. Этап 2
3. Этап 3 и Этап 4
4. Этап 5 и Этап 6
5. Этап 7
6. Этап 8

Такой порядок сначала стабилизирует runtime contract, а уже затем строит observability и operator UX поверх него.

