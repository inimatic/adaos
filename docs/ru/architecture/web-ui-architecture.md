# Web UI Architecture

Основной source of truth по целевой архитектуре находится в английской версии:
[Web UI Architecture](../../architecture/web-ui-architecture.md).

Эта страница - краткая русская точка входа и резюме для текущего направления.

## Что фиксирует архитектура

Целевая AdaOS Web UI строится как универсальный браузерный клиент:

- стабильный shell без бизнес-логики навыков;
- сохраненный runtime-слой `webui.v1`;
- semantic view layer для описания UI как данных;
- typed action model;
- разделение Yjs-состояния, stream-состояния и browser-local view state;
- Taiga UI как богатый desktop/workspace renderer;
- Ionic как shell/mobile/navigation слой.

Навыки и сценарии не должны поставлять произвольный Angular/Taiga-код в клиент.
Они поставляют манифесты, semantic views, bindings и typed actions.

## Формы

Формы должны стать semantic surface, а не набором `ion-*` или `tui-*`
компонентов в ABI.

Текущее состояние:

- в `webui.v1` уже есть виджет `ui.form`;
- клиентский `ui.form` сейчас поддерживает только `text`, `textarea`,
  `number`, `date`, `toggle`, `select`;
- схема валидирует сам `widgetType`, но почти не валидирует
  `ui.form.inputs.fields`;
- `webui.v1.types.d.ts` пока оставляет `type: string` и generic `inputs`.

Целевое направление:

- `form` - semantic view kind для survey/editor/settings/review/quiz форм;
- `form_matrix` - отдельный semantic kind для grid/matrix вопросов;
- `ui.form` остается compatibility-контейнером;
- field ABI добавляется в `webui.v1` как typed contract;
- канонический контракт говорит `singleChoice`, `multiChoice`, `rating`,
  `linearScale`, `dateRange`, а не `ion-radio` или `tui-rating`;
- renderer-specific параметры допускаются только как hints.

Минимальный набор типов полей:

- short text, long text, number, integer;
- single choice, multiple choice, dropdown, combobox, chips/tags;
- boolean/toggle, slider/range, linear scale, rating;
- date, time, date-time, date range;
- file upload через artifact refs;
- section/page break/static content/media help;
- single-choice grid, checkbox grid, rating grid;
- quiz metadata: correct answer, points, feedback.

## Состояние и submit

Форма должна явно разделять:

- `draft` - локальный или selectively synchronized view state во время
  редактирования;
- `domain` - состояние навыка/сценария после успешной валидации и submit;
- `response` - immutable запись ответа, если форма не объявила edit semantics;
- агрегаты и summary - отдельные projections/streams, а не draft branch.

Базовые действия формы:

- `validate`;
- `submit`;
- `save_draft`;
- `reset`;
- `next_section`;
- `previous_section`.

Branching и conditional visibility должны быть декларативными: поле или option
ссылается на section id или terminal state, а не на callback renderer-а.

## Дорожная карта

- [x] Зафиксировать формы как semantic browser surface, отдельный от Taiga/Ionic.
- [ ] Добавить typed `formField` definitions в `webui.v1.schema.json`.
- [ ] Добавить TypeScript helpers в `webui.v1.types.d.ts`.
- [ ] Сохранить backward compatibility для текущего `ui.form`.
- [ ] Валидировать `ui.form.inputs.fields` при наличии typed fields.
- [ ] Добавить contract fixtures: survey, settings/editor, multi-section,
  quiz-like form.
- [ ] Расширить `skill validate` и Builder diagnostics для форм.
- [ ] Реализовать renderer adapter для текущих полей.
- [ ] Добавить choice, scale, rating, date/time, file и matrix renderers.
- [ ] Добавить draft/dirty/submit/pending feedback lifecycle.
- [ ] Добавить declarative validation и localized errors.
- [ ] Добавить conditional visibility и branching.
- [ ] Вывести response summaries через projections/streams и отдельные chart
  semantic views.
