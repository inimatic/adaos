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

## Builder Context Experiments

On the local development node, run `./e2e/stand/run-builder-context.ps1 -RunId <unique-id>`.
It fixes GPT-5, low reasoning, the provider's 128000-token ceiling, and 600-second
primary/repair waits. `-Effort`, `-Repetitions` and `-Cases` select matched runs.
The suite's 1250-second step ceiling allows two such waits plus local overhead;
this is an observation window, not a latency target or a retry of failed jobs.
If the API owner executes the job, start that owner with the same
`ADAOS_BUILDER_LLM_JOB_TIMEOUT_S=600` and repair setting as the runner.

Before interpreting a cohort, inspect saved `*.request.json` generation.model,
options.reasoning and the provider telemetry. An intended override in run.json
does not prove the provider received it. The runner now rejects a reasoning
override without an explicit model and passes the model through SDK metadata.
Run `context-archetypes-low-20260912-03` was stopped and invalidated because its
requests had model/reasoning null and a 150-second local wait. Keep its evidence,
but never count it in GPT-5 comparisons.
