# Сценарии

Аналогично Skills:

- `{BASE_DIR}/scenarios` — моно-репо.
- Таблицы `scenarios` и `scenario_versions`.

CLI:

```bash
# исполнение сценария и печать результатов
adaos scenario run greet_on_boot

# валидация структуры (проверка зарегистрированных действий)
adaos scenario validate greet_on_boot

# запуск тестов сценария (pytest из .adaos/scenarios/<id>/tests)
adaos scenario test greet_on_boot

# запустить тесты для всех сценариев
adaos scenario test
```

## Модель данных (SQLite)

- Таблица `scenarios`:

  - `name` (PK), `active_version`, `repo_url`, `installed`, `last_updated`
- Таблица `scenario_versions` — как у навыков.

## Хранилище кода

- Путь: `{BASE_DIR}/scenarios` — **одно git-репо** (моно-репо сценариев).
- Выборка подпапок через `git sparse-checkout` по БД.

## Сервис и CLI

Сервис: `services/scenario/manager.py` (аналогично `SkillManager`).

CLI подкоманды, связанные с управлением хранилищем и реестром, доступны в SDK
через `adaos.sdk.manage.scenarios.*`. Для локальных сценариев основной поток
работы идёт через команды `run`, `validate` и `test` выше.
