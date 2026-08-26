# CONFIG

## ENV/CLI

- `ADAOS_BASE_DIR` — база данных/артефактов (`~/.adaos` по умолчанию)
- `ADAOS_PROFILE` — профиль (`default`)
- `ADAOS_GIT_NAME` / `ADAOS_GIT_EMAIL` — автор коммитов

## Константы монореп

`config/const.py`:

- `SKILLS_MONOREPO_URL`, `SKILLS_MONOREPO_BRANCH`
- `SCENARIOS_MONOREPO_URL`, `SCENARIOS_MONOREPO_BRANCH`
- `ALLOW_ENV_MONOREPO_OVERRIDE` (False)

### Production workspace source

Production workspace synchronization reads `registry.json` from the configured
remote branch, not from whichever local branch was previously checked out. It
fetches that branch and checks it out as an AdaOS-managed local branch named
`adaos/runtime-<remote>-<branch>`. Existing operator and development branches
remain available and are not rewritten.

- `ADAOS_WORKSPACE_SYNC_REMOTE` selects the Git remote (`origin` by default).
- `ADAOS_WORKSPACE_REGISTRY_BRANCH` overrides the configured registry branch.
- Without the override, skills and scenarios must use the same configured
  monorepo branch because they share one workspace checkout.
- `ENV_TYPE=dev` preserves the currently checked-out development branch.

## Prepare

`prepare_environment()`:

- создаёт каталоги (`skills/`, `scenarios/`, `state/`, `cache/`, `logs/`)
- инициализирует БД
- **клонирует** монорепо только если `.git` отсутствует (без pull/checkout)
