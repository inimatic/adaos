# Deployment

This page summarizes the supported development, production, and notebook-style
deployment paths. For a first local setup, start with [Quickstart](quickstart.md).

## Development runtime

Use direct API mode when you are developing skills, SDK code, client integration,
or local runtime behavior:

```bash
git clone -b rev2026 https://github.com/inimatic/adaos.git
cd adaos
bash tools/bootstrap.sh --zone ru --dev --node-name "Local Dev Node"
source .venv/bin/activate
adaos api serve --host 127.0.0.1 --port 8777
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File tools/bootstrap.ps1 -ZoneId ru -Dev
.\.venv\Scripts\Activate.ps1
adaos api serve --host 127.0.0.1 --port 8777
```

Development mode writes `ENV_TYPE=dev` into `.env` and keeps runtime state under
the repository-local `.adaos` directory. Direct `adaos api serve` does not run
the supervisor, slot cutover, or production update lifecycle.

## Production runtime

Use init scripts or bootstrap scripts with service/autostart enabled for a
hosted hub or always-on member node:

```bash
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --role hub --install-service auto --zone ru
adaos autostart status
adaos autostart inspect
```

Production mode uses the supervisor-managed slot runtime when autostart is
enabled. The operational surface is:

```bash
adaos autostart status
adaos autostart update-status
adaos autostart update-start
adaos autostart update-cancel
adaos autostart update-rollback
adaos node reliability
```

For Linux root deployments, `adaos autostart enable` also maintains a
non-login-shell CLI shim at `/usr/local/bin/adaos` so SSH commands such as
`ssh host 'adaos autostart update-status'` work even when shell startup files do
not activate the virtual environment.

## Google Colab runtime

Colab is useful for temporary development members, demos, and student labs. It
is not a production host because sessions are ephemeral and networking can be
interrupted by the notebook environment.

In a Colab notebook:

```bash
!git clone -b rev2026 https://github.com/inimatic/adaos.git
%cd adaos
!bash tools/bootstrap.sh --zone ru --dev --node-name "Colab Dev Node" --no-core-update
```

Start a direct runtime:

```bash
!source .venv/bin/activate && adaos api serve --host 127.0.0.1 --port 8777
```

To join the Colab runtime as a member of an existing hub, create a join-code on
the hub and pass it to bootstrap:

```bash
!bash tools/bootstrap.sh --join-code CODE --zone ru --dev --node-name "Colab Member" --no-core-update
```

Because Colab sessions are temporary, prefer member mode over hub mode unless
the notebook itself is the object of the experiment.

## Hosted backend and client

Backend health:

```bash
curl -sS https://api.inimatic.com/healthz
curl -sS https://ru.api.inimatic.com/healthz
```

Client version:

```bash
curl -sS https://inimatic.com/version.json
```

Client hosting builds are documented in
`src/adaos/integrations/adaos-client/README.md`.

