# AdaOS

AdaOS - это Python-платформа для построения распределенных ассистентных систем из навыков, сценариев, сервисов узла и локальных API управления.

Текущий репозиторий содержит ядро разработки и runtime:

- локальный CLI `adaos`
- управляющий API на FastAPI
- runtime-сервисы для узла, навыков, сценариев и webspace
- SDK-модули для навыков, данных, событий и control plane
- bootstrap-скрипты, тесты и документацию MkDocs

## Что AdaOS умеет сейчас

Текущая реализация сосредоточена на локальных и приватных развертываниях:

- запуск узла в роли `hub` или `member`
- управление навыками и сценариями из CLI
- локальное HTTP-управление через токенизированный API
- управление service-type skills через supervisor и `/api/services/*`
- работа с Yjs-based webspace через `adaos node yjs ...`
- onboarding member-узлов через join code и управление обновлениями
- developer workflow для Root и Forge-подобной публикации

## Основные разделы

- [Быстрый старт](quickstart.md)
- [Архитектура](architecture/index.md)
- [CLI](cli/index.md)
- [SDK](sdk/index.md)
- [Навыки](skills.md)
- [Сценарии](scenarios.md)
- [DevPortal](devportal.md)
- [Целевая архитектура артефактов](../architecture/artifact-source-package-activation.md)
- [Проверка pipeline от 2026-07-24](../architecture/artifact-pipeline-local-evidence-2026-07-24.md)

## Pipeline разработки артефактов

Однопользовательский путь `DEV → immutable checkpoint → package → trial →
publication → Workspace` проверен локально на реальных сценарии и навыке.
Builder назначает отдельный `change_id` каждой итерации Automation и не
повторяет автоматически команды с неизвестным результатом. Backend-маршрут
проверки дерева исходников развернут в production, но до включения package-only
режима по умолчанию остается проверка на чистом stand/второй машине.

## Текущий фокус

В репозитории уже реализованы реальные операционные функции: роли узлов, autostart, orchestration обновлений ядра, service supervision, monitoring и управление Yjs webspace. Поэтому страницы вне разделов `Roadmap` и `Concepts` здесь приведены к текущей реализации в `src/adaos`.
