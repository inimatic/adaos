# AdaOS

AdaOS — local-first платформа для создания и эксплуатации распределённых
ассистентных сред, объединяющих людей, AI-агентов, приложения и устройства.

Этот репозиторий содержит общий developer-facing runtime-фундамент. В него
входят CLI, локальный control API, SDK, сервисы узлов, навыки, сценарии,
webspaces, контракты интеграции устройств и браузеров, а также управляемый
жизненный цикл артефактов, на которых строятся решения AdaOS.

Авторитетный источник: [AdaOS documentation home](https://inimatic.github.io/adaos/).

## Одна платформа, несколько направлений решений

AdaOS использует единый runtime и package lifecycle в разных средах
развёртывания и областях применения:

```text
AdaOS Core
  -> профили развёртывания: Home, Campus, Enterprise
  -> каркасы и агенты решений: Research Fabric, aResearcher
  -> пакеты решений: skills, scenarios, workflows, policies, UI
  -> endpoints: Browser, reDevice, hub и member nodes
```

Эти имена не являются отдельными форками AdaOS. Home, Campus и Enterprise
описывают профили развёртывания и управления. Research и aResearcher описывают
предметный каркас и пользовательское решение. reDevice является сквозным
семейством endpoints.

Нормативные различия определены в [Продуктовой модели AdaOS](product/index.md),
а текущие портфельные гипотезы и метки зрелости — в
[Направлениях решений](product/solution-directions.md).

## Что AdaOS умеет сейчас

Текущая реализация сосредоточена на локальных и частных развёртываниях:

- запускать узел как `hub` или `member` и подключать member-узлы по join codes;
- устанавливать, проверять, активировать, запускать, обновлять и исследовать
  навыки и сценарии;
- предоставлять runtime-управление через FastAPI-сервис с локальной
  token-based аутентификацией;
- управлять service-type skills и жизненным циклом runtime через supervisor;
- синхронизировать webspaces и видимое браузеру состояние приложений через Yjs;
- подключать браузеры и reDevice-style endpoints через явные контракты доступа
  и назначения;
- управлять профилями, членством, разрешениями, приглашениями, областями
  приватности и аудитом через развивающийся фундамент personalization и access;
- создавать, проверять, упаковывать, публиковать, активировать, наблюдать и
  исправлять управляемые артефакты через developer- и Builder-workflows.

Реализованный фундамент не означает завершённости каждого именованного
решения. Страницы со статусом target architecture, roadmap, strategic direction
или long-term direction следует читать в соответствии с указанной зрелостью.

## Основная модель runtime

- **Assistant** — пользовательская среда, внутренне поддерживаемая подсетью.
- **Hub** владеет и координирует подсеть.
- **Member** — другой runtime-узел, подключённый к подсети.
- **Webspace** — контекст доступа и проекций внутри Assistant.
- **Application** — продуктовое представление сценария.
- **Skill** предоставляет сфокусированную исполняемую capability.
- **Scenario** координирует многошаговое поведение навыков, сервисов, людей и
  узлов.
- **Device** — физический или виртуальный host; **Agent** или endpoint —
  программный участник, работающий на нём или через него.

Подробное соответствие продуктовой и технической терминологии находится в
английском документе
[AdaOS Product Terminology](https://inimatic.github.io/adaos/architecture/product-terminology/).

## Выберите путь

| Цель | С чего начать |
| --- | --- |
| Понять AdaOS и области применения | [Продуктовая модель](product/index.md) и [Направления решений](product/solution-directions.md) |
| Установить и запустить локальную среду разработки | [Quickstart](https://inimatic.github.io/adaos/quickstart/) |
| Развернуть или эксплуатировать узел | [Deployment](https://inimatic.github.io/adaos/deployment/) и [Runtime and Operations](https://inimatic.github.io/adaos/cli/runtime/) |
| Создать skill или scenario | [Skills](https://inimatic.github.io/adaos/skills/), [Scenarios](https://inimatic.github.io/adaos/scenarios/) и [SDK](https://inimatic.github.io/adaos/sdk/) |
| Понять реализованный runtime | [Architecture Overview](https://inimatic.github.io/adaos/architecture/) |
| Понять долгосрочную модель управляемого развития | [Governed Evolution](https://inimatic.github.io/adaos/architecture/governed-evolution/) |
| Найти текущий источник планирования | [Roadmap Inventory](https://inimatic.github.io/adaos/architecture/roadmap-inventory/) и [Issue Tracker](https://inimatic.github.io/adaos/issue-tracker/) |
| Проверить Builder end to end | [Builder Verification Guide](https://inimatic.github.io/adaos/guides/builder-verification/) |

## Статус и язык документации

Текущее поведение, целевая архитектура, пункты дорожных карт и записи
свидетельств намеренно разделены. Прежде чем считать проект или чеклист
реализованным поведением, проверьте статус страницы и
[Roadmap Inventory](https://inimatic.github.io/adaos/architecture/roadmap-inventory/).

Авторитетна английская документация. Поддерживаемые переводы охватывают
небольшой стабильный публичный слой. Русская навигация ведёт прямо в
англоязычные технические разделы, не публикуя fallback-копии под русскими URL.
См.
[Политику языков и перевода документации](documentation-language-policy.md).
