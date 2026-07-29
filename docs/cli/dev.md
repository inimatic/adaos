# adaos dev

Рабочие команды для dev-пространства (.adaos/dev/<subnet_id>) и локальной проверки навыков.

---

## Подготовка dev-окружения

- [x] обязательные шаги

```bash
adaos dev root init
adaos dev root login
```

`init` выполняет первичную инициализацию dev-пространства, `login` подтверждает доступ через устройство или QR.

## Просмотр артефактов

```bash
adaos dev skill list [--json]
adaos dev scenario list [--json]
```

Команды перечисляют содержимое директорий `base/dev/{subnet_id}/skills` и `.../scenarios`.

## Создание артефактов

```bash
adaos dev skill create <NAME> [--template template_name_from_workspace]
adaos dev scenario create <NAME> [--template template_name_from_workspace]
```

## Удаление артефактов

```bash
adaos dev skill delete <NAME> [--yes]
adaos dev scenario delete <NAME> [--yes]
```

Флаг `--yes` отключает подтверждение.

## Публикация в Root

```bash
adaos dev skill publish <NAME> [--bump patch|minor|major] [--force] [--dry-run]
adaos dev scenario publish <NAME> [--bump patch|minor|major] [--force] [--dry-run]
```

## Работа с DEV-runtime навыка

```bash
adaos dev skill activate <NAME> [--slot A|B] [--version VERSION]
adaos dev skill status <NAME> [--json]
adaos dev skill rollback <NAME>
```

`status` и `rollback` работают с runtime по адресу `.adaos/dev/<subnet>/skills/.runtime/<name>`.

## Подготовка и валидация навыка

```bash
adaos dev skill lint <NAME|PATH>
adaos dev skill prep <NAME>
```

`lint` выполняет мягкую проверку каталога навыка, `prep` запускает `prep/prepare.py` для активного DEV-runtime.

## Тестирование навыка

```bash
adaos dev skill test <NAME>
adaos dev skill test <NAME> --runtime
```

Без `--runtime` тесты запускаются из исходников `skills/<name>/tests`. С `--runtime` используется `.runtime/<version>/slots/current/src/skills/<name>/tests`; при отсутствии подкаталогов `smoke|contract|e2e-dryrun` выполняется общий `pytest` по адресу, полученному от PathProvider.

## Настройка и запуск инструментов

```bash
adaos dev skill setup <NAME>
adaos dev skill run <NAME> [TOOL] [--json PAYLOAD | --json-file PATH]
```

Для payload с кириллицей или другим не-ASCII текстом используйте
`--json-file`. Файл читается строго как UTF-8 (допускается UTF-8 BOM), поэтому
PowerShell не может повредить строку при передаче аргументов нативному процессу:

```powershell
adaos dev skill run builder_skill chat --json-file .adaos\state\builder\requests\chat.json
```

`--json` и `--json-file` взаимоисключающие. Inline `--json` оставлен для
коротких ASCII payload; не используйте его для пользовательского Unicode-текста.

