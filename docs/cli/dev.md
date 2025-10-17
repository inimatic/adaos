# adaos dev

Команды для работы в dev-пространстве текущего сабнета.

---

## Список dev-артефактов

- [x] реализация

```bash
adaos dev skill list [--json]
adaos dev scenario list [--json]
```

Выводит навыки и сценарии из `base/dev/{subnet_id}/skills` и `base/dev/{subnet_id}/scenarios`.

## Удаление артефактов

- [x] реализация

```bash
adaos dev skill delete <NAME> [--yes]
adaos dev scenario delete <NAME> [--yes]
```

Удаляет каталоги артефактов. Если не найден — завершает с кодом 3.
Флаг `--yes` пропускает подтверждение.

---

## Публикация артефактов

- [x] реализация

```bash
adaos dev skill publish <NAME> [--bump patch|minor|major] [--force] [--dry-run]
adaos dev scenario publish <NAME> [--bump patch|minor|major] [--force] [--dry-run]
```

Переносит артефакт в workspace и публикует его в root-реестре.

- `--bump` — часть семантической версии для инкремента (по умолчанию `patch`);
- `--force` — игнорировать различия манифестов;
- `--dry-run` — предварительный просмотр без внесения изменений.

После публикации отправляется событие
`registry.skill.published` или `registry.scenario.published`.

---

## Регистрация и логин сабнета

- [x] реализация

```bash
adaos dev root init
adaos dev root login
```

- `init` — инициализация и получение сертификата подсети от root-сервера.
- `login` — авторизация через устройство/QR-код.

## Валидация

- [x] реализация

```bash
adaos dev skill publish <NAME>
```

## Тестирование

- [ ] реализация

```bash
adaos dev skill test <NAME>
```

## Запуск

- [ ] реализация

```bash
adaos dev skill run <NAME>
```
