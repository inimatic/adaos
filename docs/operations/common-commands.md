# Common Commands

This page keeps the longer command examples out of the README while preserving
the operational notes that are useful during development, deployment, and field
debugging.

## Local API runtime

```bash
adaos --help
adaos where
adaos api serve --host 127.0.0.1 --port 8777
adaos skill list
adaos skill run weather_skill --topic nlp.intent.weather.get --payload '{"city":"Berlin"}'
curl -i http://127.0.0.1:8777/health/live
curl -i http://127.0.0.1:8777/health/ready
```

Notes:

- `adaos api serve` starts the local hub/runtime HTTP API directly, without the
  slot supervisor.
- In development, an explicit `--port` is persisted into `.adaos/node.yaml` as
  `local_api_url`, and the next `adaos api serve` reuses it.
- `8777` and `8778` are the normal browser-discoverable local hub ports.
- Use a different port such as `8779` when you do not want
  `https://inimatic.web.app/` or `https://inimatic.com/` to auto-attach to the
  local runtime and prefer it to stay on Root.
- Supervisor-managed runtime mode is separate: it owns port `8776`, manages
  slots, and sets `ADAOS_SUPERVISOR_ENABLED=1`.
- Development runtimes with `ENV_TYPE=dev` do not follow hub/root core-update
  signals by default. Set `ADAOS_DEV_ALLOW_CORE_UPDATE=1` only when deliberately
  testing the update machinery.
- Leave `HUB_NATS_WS_PROXY` unset or set to `auto` for normal Windows and Linux
  hub-to-root NATS-over-WS routing. Use `HUB_NATS_WS_PROXY=none` only for
  direct-route diagnostics.

## One-line bootstrap variants

The init scripts prefer a real git checkout when `git` is available. Archive
mode is available via `--archive` / `-Archive`, but it does not include git
metadata or submodules.

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --python /usr/bin/python3.11 --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --join-code CODE --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --node-name "Codespace Member" --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --no-core-update --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --use-git-from https://github.com/<you>/adaos.git --rev my-branch --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --archive --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --workspace-registry-repo https://github.com/<you>/adaos-registry.git --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --codespaces --node-name "Codespace Member" --no-core-update --zone ru
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --dest . --zone ru
```

Windows PowerShell:

```powershell
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content))
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -ZoneId ru
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -JoinCode CODE -ZoneId ru
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -UseGitFrom https://github.com/<you>/adaos.git -Rev my-branch
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -Archive -ZoneId ru
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -WorkspaceRegistryRepo https://github.com/<you>/adaos-registry.git -ZoneId ru
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -ZoneId ru -Dev
```

Windows CMD:

```bat
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $env:TEMP 'adaos-init.ps1'; iwr -UseBasicParsing 'https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1' -OutFile $p; & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $p -JoinCode CODE"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $env:TEMP 'adaos-init.ps1'; iwr -UseBasicParsing 'https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1' -OutFile $p; & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $p -ZoneId ru"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $env:TEMP 'adaos-init.ps1'; iwr -UseBasicParsing 'https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1' -OutFile $p; & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $p -UseGitFrom https://github.com/<you>/adaos.git -Rev my-branch"
```

## Manual bootstrap

Linux / macOS:

```bash
bash tools/bootstrap.sh
source .venv/bin/activate
adaos --help
bash tools/bootstrap.sh --zone ru --dev
bash tools/bootstrap.sh --python /usr/bin/python3.11 --zone ru --dev
bash tools/bootstrap.sh --node-name "Local Dev Node" --zone ru --dev
```

Windows with `uv`:

```powershell
Set-ExecutionPolicy RemoteSigned -Scope Process
powershell -ExecutionPolicy Bypass -File tools/bootstrap_uv.ps1
.\.venv\Scripts\Activate.ps1
adaos --help
powershell -ExecutionPolicy Bypass -File tools/bootstrap_uv.ps1 -ZoneId ru -Dev
```

Windows with `pip`:

```powershell
powershell -ExecutionPolicy Bypass -File tools/bootstrap.ps1 -ZoneId ru -Dev
.\.venv\Scripts\Activate.ps1
adaos --help
```

Manual editable install:

```bash
pip install -e ".[dev]"
adaos --help
```

## Git checkout maintenance

```bash
adaos git remote status --recursive --check-ssh
adaos git remote use-ssh --recursive
adaos git remote use-https --recursive
adaos git repair-core --rev rev2026
```

`repair-core` can adopt an existing AdaOS source tree into a git checkout, set
`origin/rev2026` as upstream, and initialize the required `rasa-port` submodule.

## Slot shell

Use the source-able scripts from `tools/` to switch the current shell into the
runtime of the active core slot without touching the root `.venv`.

```bash
source tools/slot-shell.sh
source tools/slot-shell.sh --cd
```

PowerShell:

```powershell
. .\tools\slot-shell.ps1
. .\tools\slot-shell.ps1 -Cd
```

State-changing production CLI commands try to re-exec into the active slot
automatically. If the current process is still outside the active slot context,
the CLI emits `slot_shell_required`; run `source tools/slot-shell.sh --cd` or
`. .\tools\slot-shell.ps1 -Cd` before retrying.

## Service management

Systemd user service:

```bash
systemctl --user daemon-reload
systemctl --user restart adaos.service
systemctl --user status adaos.service --no-pager
journalctl --user -u adaos.service -n 120 --no-pager
```

Autostart:

```bash
adaos autostart enable
adaos autostart status
adaos autostart inspect
adaos autostart inspect --json
adaos autostart inspect --sample-sec 0.5
adaos autostart disable
```

Local status with token:

```bash
export ADAOS_TOKEN='********'
curl -sS -H "Authorization: Bearer $ADAOS_TOKEN" http://127.0.0.1:8777/api/node/status
```

## Core update and smoke flow

```bash
adaos autostart update-status
adaos autostart update-start
adaos autostart update-cancel
adaos autostart update-rollback
adaos autostart update-complete
adaos autostart smoke-update
```

JSON variants:

```bash
adaos autostart update-status --json
adaos autostart smoke-update --countdown-sec 5 --json
adaos autostart update-cancel --json
adaos autostart update-rollback --json
```

Recommended smoke order:

```bash
adaos autostart update-status --json
adaos autostart smoke-update --countdown-sec 30 --json
adaos autostart update-cancel --json
adaos autostart smoke-update --countdown-sec 5 --json
```

State files are normally under one of these paths:

```bash
cat ~/adaos/.adaos/state/core_update/status.json
cat ~/.adaos/state/core_update/status.json
```

## Runtime diagnostics

```bash
adaos node reliability
adaos node status
adaos node status --probe
adaos runtime status
adaos runtime logs
```

CPU inspection:

```bash
adaos autostart inspect --json
/root/.adaos/state/core_slots/slots/A/venv/bin/python -m pip install py-spy
/root/.adaos/state/core_slots/slots/B/venv/bin/py-spy dump --pid <RUNTIME_PID>
```

Yjs scenario benchmark:

```bash
adaos node yjs benchmark-scenario --webspace default --scenario-id infrascope --baseline-scenario web_desktop --iterations 5 --detail
```

## Web client URL examples

The full list of client URL parameters lives in
`src/adaos/integrations/adaos-client/README.md#client-url-parameters`.

```text
https://inimatic.web.app/?zone=ru&mode=login&auto_login=1
https://inimatic.web.app/?boot_debug=1
https://inimatic.web.app/?runtime_debug=0
https://inimatic.web.app/?yjs_persist=0
https://inimatic.com/?zone=ru&mode=login&auto_login=1
```

## Linux non-login SSH CLI shim

When `adaos autostart enable` runs as root on Linux, AdaOS also maintains
`/usr/local/bin/adaos`. This is for non-login SSH commands such as
`ssh host 'adaos autostart update-status'`, where shell startup files may not
put the venv into `PATH`.

The shim exports the same root-control environment as the autostart wrapper and
executes the stable root Python as `python -m adaos.apps.cli.app "$@"`. After a
core root-promotion, `refresh_wrapper` rewrites the shim together with
`~/.adaos/bin/adaos-autostart.sh`, so it follows the current `/root/adaos/src`
checkout. `adaos autostart status --json` reports the `cli_shim` path and state.

Set `ADAOS_LINUX_CLI_SHIM_PATH` before `adaos autostart enable` if a deployment
needs a different shim path. Existing non-AdaOS files at that path are left
untouched.

