# Quickstart

Status: public current-state setup guide.

## Clone And Bootstrap

Linux and macOS:

```bash
git clone -b rev2026 https://github.com/inimatic/adaos.git
cd adaos
bash tools/bootstrap.sh --zone ru --dev
source .venv/bin/activate
adaos --help
```

Windows PowerShell:

```powershell
git clone -b rev2026 https://github.com/inimatic/adaos.git
cd adaos
powershell -ExecutionPolicy Bypass -File tools/bootstrap.ps1 -ZoneId ru -Dev
.\.venv\Scripts\Activate.ps1
adaos --help
```

## Run A Development Runtime

```bash
adaos dev serve --host 127.0.0.1 --port 8777
curl -i http://127.0.0.1:8777/health/live
curl -i http://127.0.0.1:8777/health/ready
```

Use `adaos api serve` only for lower-level foreground API debugging.
Production-style runtimes use `adaos autostart ...`.

## Port Guidance

Use port `8777` or `8778` when the browser client should auto-discover a local
runtime. Use another port, such as `8779`, when the hosted client should stay
routed through Root.

