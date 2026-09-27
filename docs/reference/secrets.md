# Секреты

## Backends

- **KeyringVault** — хранит значения в OS keyring; индекс ключей — в SQLiteKV.
- **FileVault** — шифрованный `{BASE_DIR}/state/vault.json` (Fernet). Мастер-ключ из keyring или `ADAOS_VAULT_MASTER_KEY`.

## CLI

```bash
adaos secret set KEY VALUE
adaos secret get KEY [--show]
adaos secret list
adaos secret delete KEY
adaos secret export [--show]
adaos secret import file.json
````

## Политики

- Требуются `"secrets.read"`/`"secrets.write"`.
- Значения не логируются; экспорт по умолчанию маскирует.

- **KeyringVault** (основной)

  - Хранение значений в системном keyring (Windows Credential Locker / macOS Keychain / Secret Service).
  - Индекс ключей (для `list/export`) — в `SQLiteKV`.
  - Совместим с KV, у которого есть `get/set` или `get_json/set_json`.

- **FileVault** (фолбэк)

  - Файл `{BASE_DIR}/state/vault.json`, **зашифрованный Fernet**.
  - Мастер-ключ:

    - хранится в keyring (`service='adaos:master:<profile>'`), или
    - читается из `ADAOS_VAULT_MASTER_KEY` (для CI), или
    - генерируется при первом запуске.

## Сервис

`services/crypto/secrets_service.py` — обёртка с Capabilities:

- `put/get/delete/list/import_items/export_items`
- Требуются права: `"secrets.read"` и/или `"secrets.write"`.

## CLI

```bash
adaos secret set KEY VALUE            # создать/обновить
adaos secret get KEY [--show]         # по умолчанию маскирует
adaos secret list
adaos secret delete KEY
adaos secret export [--show]          # JSON (значения по умолчанию маскированы)
adaos secret import file.json
```

## Headless node vault selection

`ADAOS_CREDENTIAL_VAULT_BACKEND` accepts `auto` (default), `keyring`, or
`file`. `auto` uses the OS keyring only when a real backend is available. A
headless Linux node without a Secret Service session does not probe the Python
fail backend on the boot critical path; it uses `FileVault`.

For `FileVault`, the Fernet master key is provisioned atomically at
`{BASE_DIR}/private/credentials/vault-master-<profile-digest>.key` with
owner-only permissions. `ADAOS_VAULT_MASTER_KEY`, when provided for initial
provisioning, seeds that durable local key. An invalid configured key fails
closed. The private key and encrypted `state/vault.json` are node authority
state: neither is published with an Application/skill package or copied to
another subnet by CBS resolution.

> CLI никогда не логирует значения секретов.
