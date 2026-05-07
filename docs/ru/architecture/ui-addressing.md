# UI Addressing

Основной source of truth для этой темы находится в английской версии:
[UI Addressing](../../architecture/ui-addressing.md).

Этот документ нужен как краткая точка входа в русской ветке.

## Что фиксирует архитектура

Целевая модель адресации AdaOS для Web UI разделяет несколько слоёв:

- logical authoring layer: `ctx.*`
- projection-routing layer: `data_projections` и пары `(scope, slot)`
- runtime UI binding layer: typed refs для `y`, `stream`, `view`, `projection`,
  `action`
- domain identity layer: например `device:*` и `webspace:*`

## Зачем это нужно

- чтобы навыки и сценарии, в том числе написанные LLM, не придумывали
  произвольные ветки и имена
- чтобы Web UI мог безопасно связывать состояние, стримы и действия
- чтобы device, workspace, operations и projection-модели использовали один
  словарь

## Приоритет

Приоритетный срез адресного пространства нужен именно для Web UI:

- `y:` refs
- `stream:` refs
- `view:` refs
- `projection:` refs
- `action:` refs
- базовые domain refs вроде `device:*` и `webspace:*`

Для полной модели, правил scope и дорожной карты используйте английский
документ.

