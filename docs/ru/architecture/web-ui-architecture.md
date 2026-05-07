# Web UI Architecture

Основной source of truth для этой темы находится в английской версии:
[Web UI Architecture](../../architecture/web-ui-architecture.md).

Этот документ нужен как краткая точка входа в русской ветке.

## Что фиксирует архитектура

Целевая архитектура AdaOS Web UI строится как:

- stable shell
- preserved `webui.v1` runtime manifest layer
- new semantic view layer
- typed action model
- Yjs + stream + local view-state split
- Taiga-based rich renderer layer
- Ionic shell and mobile interaction layer

## Основные принципы

- навыки не поставляют произвольный Angular/Taiga код
- Taiga используется как presentation toolkit, а не как язык манифеста
- semantic UI contracts отделяются от renderer-specific деталей
- staged loading и focus-aware hydration становятся частью явного контракта

## Приоритетный MVP-срез

В первую очередь архитектура предлагает:

- semantic view kinds:
  `collection_grid`, `form_matrix`, `event_log`, `chat_panel`
- typed actions:
  `emit`, `open_modal`, `set_view_state`, `call_host`,
  `invoke_skill_action`
- layouts:
  `stack`, `split`, `tabs`
- data mechanisms:
  Yjs binding, stream receiver, local view state

Для полной целевой модели, ограничений и дорожной карты используйте
английский документ.

