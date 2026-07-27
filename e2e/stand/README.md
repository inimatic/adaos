# AdaOS Stand E2E

This directory contains the executable post-deploy acceptance vertical slice:

- `adaos.e2e.stand` probes Root and Hub contracts and always writes an evidence
  bundle;
- `browser/` runs one deterministic headless Chromium smoke against the
  deployed client;
- credentials and Playwright storage state stay under ignored `.secrets/` or
  in CI secret storage.

## Prepare

Create a target config from `target.example.json` under `.secrets/` and set the
control token through the config's `tokenEnv` variable. Do not put tokens,
cookies, JWTs, or inline authorization values in target JSON.

Install the pinned browser runner once on the runner machine:

```powershell
Set-Location e2e/stand/browser
npm ci
npm run install:browser
Set-Location ../../..
```

## Observe Profile

Run the no-browser health and diagnostics gate:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:ADAOS_E2E_TOKEN = '<secret from the runner store>'
.\.venv\Scripts\python.exe -m adaos.e2e.stand --config .secrets/stand-e2e.json
```

Add the deployed client smoke:

```powershell
.\.venv\Scripts\python.exe -m adaos.e2e.stand --config .secrets/stand-e2e.json --browser
```

Exit codes are `0` for `passed`, `1` for `failed`, and `2` for
`inconclusive`. Bundles are written under `artifacts/e2e-runs/` unless
`--output-root` overrides the location.

Playwright tracing is intentionally disabled in v1 because traces can retain
authorization headers, cookies, and storage state. The v1 browser evidence is
the redacted console/network/WebSocket lifecycle, screenshot, and the public
AdaOS debug-state snapshot.
