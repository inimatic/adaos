# Доступ устройств и браузеры

## Назначение

Этот документ фиксирует целевую архитектуру device-centric управления доступом в AdaOS.
Он объединяет в одну модель:

- панель `Devices` в `web_desktop`
- browser access links, выдаваемые через web pairing
- member links, появляющиеся через subnet join flow
- per-node управление `Apps` и `Marketplace`
- будущие voice- и automation-сценарии управления устройствами

Цель в том, чтобы перестать рассматривать браузерные сессии, member links, каталоги приложений и marketplace как разрозненные UI-функции.
Вместо этого AdaOS должен получить единый device access plane с:

- долговременной идентичностью
- явной политикой времени жизни
- detach и revoke
- наблюдаемостью
- переиспользуемыми SDK- и skill-surface

## Проблема

В runtime уже есть почти все исходные части:

- browser pairing и bootstrap через `session_jwt`
- постоянный browser `device_id`
- member join codes и hub-member links
- сигналы browser и Yjs presence
- per-node каталоги `Apps` и `Marketplace`
- durable state, Yjs projections и skill-hosted modal UI

Не хватало единой архитектурной модели, которая отвечает на вопросы:

1. Какие подключенные сущности считаются долгоживущими устройствами, а какие временными клиентами?
2. Где хранится каноническая политика времени жизни?
3. Как браузеры и member nodes управляются в одной ментальной модели?
4. Какой слой отвечает за rename, detach и lifetime control?
5. Как `web_desktop` остаётся device-centric, но при этом опирается на универсальные платформенные компоненты?

## Базовые термины

### Access link

`access link` — это каноническая управляемая связь между AdaOS и удалённой конечной точкой.

Есть два типа links:

- `browser`: web client, идентифицируемый постоянным `device_id`
- `member`: subnet member node, идентифицируемый `node_id`

Именно access link является основным policy object.
Join codes, pair codes и approval tokens — это только временные bootstrap-механизмы выдачи доступа.

### Device и client

Для браузеров операторская модель делит links на два класса:

- `device`: долгоживущая доверенная конечная точка, обычно с постоянным доступом и редактируемым именем
- `client`: временная конечная точка с фиксированным сроком доступа

Это различие по политике, а не по транспорту.
Браузер на телефоне, телевизоре или ноутбуке может стать `device`.

### Имя устройства

У каждого долгоживущего endpoint может быть human-facing `display_name`.
Это имя должно стать стабильной меткой для:

- `web_desktop`
- browser observability UI
- будущих voice commands
- будущих automation rules

### Webspace affinity

Для browser links нужно помнить текущий или последний webspace.
Это позволяет показывать inventory не как плоский список токенов, а в контексте реального использования.

## Целевая архитектура

## 1. Bootstrap и live access — это разные задачи

Архитектура разделяет:

- `issuance`: pair codes, join codes, approvals, bootstrap session material
- `managed access`: долговременный access link registry и runtime enforcement

Это означает:

- browser pair code не является самой записью inventory
- member join code не является самой policy-записью устройства
- браузер, который получил ключ, но ни разу не подключился, не обязан попадать в долговременный inventory

Долговременная модель начинается в момент первого реального использования:

- live browser session или `device.register`
- member websocket hello и регистрация link

## 2. Access link registry в core runtime

AdaOS должен поддерживать небольшой core registry access links.

Он живёт в runtime layer, а не внутри одного skill, потому что нужен нескольким skills и client surfaces.
Первичный механизм хранения — local durable state.

Рекомендуемая форма:

```text
namespace: access_links
key: registry
```

Поля записи должны быть transport-agnostic:

- `id`
- `kind`
- `display_name`
- `access_class`
- `lifetime_mode`
- `expires_at`
- `autorotate`
- `revoked`
- `revoked_at`
- `created_at`
- `updated_at`
- `last_seen_at`
- `online`
- `connection_state`
- `last_webspace_id`
- `hostname`
- `node_names`

Правила keying:

- browser links индексируются по постоянному browser `device_id`
- member links индексируются по `node_id`

## 3. Политика времени жизни

Политика по умолчанию:

- доступ бессрочный
- rotation токена или session material выполняется платформой автоматически

Операторские режимы lifetime:

- `permanent`
- фиксированные пресеты, например `1h`, `1d`, `7d`, `30d`

Правила:

- постоянные browser links показываются в группе `Devices`
- browser links с фиксированным сроком показываются в группе `Clients`
- архив просроченных browser clients хранить не требуется
- detached links становятся revoked policy objects и должны отклоняться на новом ingress

Для member links lifetime поддерживается в той же модели, даже если типичный operational режим остаётся бессрочным.

## 4. Runtime enforcement

Access link registry должен быть не только описательным, но и policy source на ingress.

### Browser path

Browser access должен проверяться на входе live runtime channels:

- browser HTTP calls несут `X-AdaOS-Device-Id`
- browser Yjs connections несут `dev=<device_id>`
- browser control и event flows уже публикуют `device.register` и session change events

Runtime должен:

1. получить `device_id`
2. найти browser access link
3. отклонить доступ, если link revoked или expired
4. обновить `last_seen_at`, `connection_state` и `last_webspace_id` для принятого трафика

Так lifetime control становится частью runtime, а не только UI state.

### Member path

Member access должен проверяться на handshake member link:

- member hello несёт `node_id`
- hub-side link manager владеет registration и unregistration

Runtime должен:

1. получить `node_id`
2. найти member access link
3. отклонить registration, если link revoked или expired
4. обновить member metadata в registry после успешного подключения

## 5. SDK surface

Registry должен переиспользоваться skills.
Канонический доступ к нему — через SDK helper layer, например:

- `sdk.data.access_links.list_browser_links()`
- `sdk.data.access_links.list_member_links()`
- `sdk.data.access_links.rename_*()`
- `sdk.data.access_links.set_*_lifetime()`
- `sdk.data.access_links.detach_*()`

Это сохраняет стабильный skill API и позволяет менять внутреннее хранение или enforcement без размножения логики по skills.

## 6. Skill layer

Первый skill-потребитель этой модели — `browsers_skill`.

Его задачи:

- публиковать operator-facing browser projections в Yjs
- предоставлять generic actions для rename, lifetime и detach
- представлять browser inventory группами `Devices` и `Clients`
- сохранять webspace context для навигации оператора

Важно, что `browsers_skill` не владеет самой access model.
Он только первый потребитель core registry и SDK.

Это делает архитектуру пригодной для:

- будущих skills управления устройствами
- voice assistant skills
- policy automation skills
- admin и fleet-management surfaces

## 7. `web_desktop` как device-centric shell

Поверхность `desktop-icons` должна быть переосмыслена из `Applications` в `Devices`.

Это означает:

- верхний вход в панель описывает управляемые endpoints, а не только набор app icons
- node sections представляют device contexts
- per-node operational actions убираются за settings affordance
- node actions остаются реализованными через generic modals и skill-hosted actions

Settings modal секции устройства становится главным operator shell для ноды.
Он должен содержать:

- `Apps`
- `Marketplace`
- `Hide` или `Show`
- rename
- lifetime policy
- `Detach`

Так панель остаётся компактной, но не теряет control surface.

## 8. Модель UI для браузеров

Панель `Devices` должна также давать вход в `Browsers`.

Целевая UX-модель:

- отдельные группы `Devices` и `Clients`
- группировка или фильтрация по последнему или текущему webspace
- игнорирование pair approvals, которые так и не превратились в реальное browser usage
- отсутствие архива просроченных browser clients

Browser settings должны опираться на ту же access model:

- редактируемое имя
- постоянный или фиксированный lifetime
- detach

## 9. Marketplace и app management остаются node-scoped

Device-centric shell не отменяет node-scoped управление возможностями.

Он только проясняет зоны ответственности:

- `Apps` — каталог установленных приложений конкретной ноды
- `Marketplace` — список skills и scenarios, которые ещё не установлены на этой ноде
- `Hide` — состояние представления на desktop
- rename, lifetime и detach — часть device access management

Поэтому `Marketplace` остаётся node-scoped operational action, но открывается из device settings context, а не живёт рядом с каждой section button group.

## 10. Offline semantics

Offline state не должен дёргаться из-за кратковременной потери транспорта.

Device-centric desktop продолжает использовать grace timeout перед переводом иконок в disabled.
Этот timeout относится к presentation semantics.
Сам access link registry остаётся долговременной policy model и может хранить:

- online или offline
- last seen
- connection state

Это разделяет:

- валидность доступа
- фактическую текущую связанность
- UI confidence window

## 11. Связь с другими архитектурными слоями

Этот документ дополняет:

- [Member-Hub Connectivity](member-hub-connectivity.md): ownership жизненного цикла hub-member транспорта и restart-aware member semantics
- [Registry Marketplace And Operations](registry-marketplace-operations-roadmap.md): node-scoped marketplace publication и install flows
- [Operational Event Model](operational-event-model.md): browser-facing projections и materialization для оператора
- [Semantic State Plane](semantic-state-plane.md): отделение access policy от краткоживущего transport status

## Дорожная карта перехода

Рекомендуемый порядок реализации описан в
[Device Access Roadmap](device-access-roadmap.md).

