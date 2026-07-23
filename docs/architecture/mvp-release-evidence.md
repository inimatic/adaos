# MVP Release Evidence

This page defines the minimum evidence-bundle shape for an AdaOS MVP
implementation, release candidate, or rollout check. It specializes the richer
[Post-Deploy E2E Testing](post-deploy-e2e-testing.md) bundle without replacing
that document.

## Result States

Every check and the overall run use exactly one result:

- `passed`: the declared invariant was verified;
- `failed`: the invariant was violated and evidence was captured;
- `inconclusive`: environment, credentials, connectivity, or missing
  diagnostics prevented a valid decision;
- `skipped`: the check was outside the declared run scope.

Local success is not stand acceptance. A bundle must name its environment and
must not promote one maturity level to another.

## Minimum Bundle

```text
artifacts/release-evidence/<timestamp>-<run_id>/
  manifest.json
  config.redacted.json
  commands.ndjson
  checks.json
  logs/
  metrics/
  browser/
  snapshots/
  residual-risks.md
```

`manifest.json` must contain:

- schema and `run_id`;
- start/end timestamps and overall result;
- environment: `local`, `stand`, `canary`, or `production`;
- repository commit, dirty-worktree flag, client commit, and AdaOS version;
- operating system, Python, Node, browser, and relevant dependency versions;
- target identifiers such as subnet, node, webspace, and browser device where
  applicable;
- ordered check ids with result, duration, and evidence paths;
- redaction version and a list of omitted or unavailable evidence;
- known residual risks and the next acceptance level still required.

`commands.ndjson` records command, working directory, start/end time, exit
code, and captured-output path. `checks.json` maps human-readable invariants to
those commands and artifacts. A failure before browser or runtime startup must
still leave a manifest and enough evidence to classify the boundary.

## Automated Stand Runner

The executable observe profile writes this evidence before making deep
assertions:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:ADAOS_E2E_TOKEN = '<secret from the runner store>'
.\.venv\Scripts\python.exe -m adaos.e2e.stand --config .secrets/stand-e2e.json
```

After the permanent E2E browser identity and external Playwright storage state
are provisioned, add `--browser` to require client load, runtime debug export,
connected Yjs, ready materialization, and a clean fatal console/page-error
window. The runner uses `passed`, `failed`, and `inconclusive` as distinct CI
outcomes and stores bundles under `artifacts/e2e-runs/` by default.

The repository-wide Python gate is conclusive only when collection completes
and a JUnit result is retained. The 2026-07-23 process-isolated baseline ran
all `2871` collected tests in six segments: `2855` passed, `8` failed, `8`
skipped, and `0` errored. The remaining failures are one API module-state
leak, one Neural installer staging failure, and six NLU Teacher
validation/apply failures. The segmented reports are evidence of a working
complete gate, not a passing release; `TEST-001` remains open until those
clusters reach zero and the bounded aggregate command terminates reliably.

## Required MVP Sections

Choose `skipped` only with a reason in the manifest.

| Section | Minimum evidence |
| --- | --- |
| Code and contracts | targeted unit/integration commands, full relevant suite, commit ids, clean diff check |
| Browser | focused runtime tests, production build, console/network errors, projection lifecycle and reconnect observations |
| Runtime and operations | readiness, status cards, operation lifecycle, install/update or explicit out-of-scope reason |
| Yjs and projections | demand set, ProjectionRecord lifecycle, materialization/readback, dispatcher diagnostics, load/pressure snapshot |
| Stand | target identity, version convergence, smoke checks, bounded soak where required, before/after snapshots |
| Metrics and logs | latency/error counters, memory/pressure samples, correlated log window |
| Residual risk | untested topology, compatibility mirrors, deferred migrations, missing credentials or diagnostics |

## Redaction Rules

Never store raw authorization headers, AdaOS tokens, cookies, JWTs, refresh
tokens, private keys, or token-like query parameters. Redact device/user data
that is not needed to prove the invariant. Record the redaction version in the
manifest and retain failed/inconclusive bundles longer than successful local
runs.

## Closed Wave 0–1 Local Acceptance Profile

The local Wave 0–1 gate closed on 2026-07-23. Keep this command set as its
regression profile; it does not replace the separate target-stand gate.

For the MVP planning/evidence and projection-runtime slice, the minimum local
commands are:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_projection_service.py tests/test_sdk_data_ctx.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_projection_platform_acceptance.py tests/test_projection_pilot_readiness.py tests/test_platform_notifications.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_projection_subscription.py tests/test_projection_demand.py tests/test_projection_demand_yjs.py tests/test_projection_dispatcher.py tests/test_projection_event_bridge.py tests/test_projection_records.py tests/test_projection_record_yjs.py tests/test_projection_diagnostics_api.py
npx ng test --watch=false --browsers=ChromeHeadless --include='src/app/runtime/page-data.service.spec.ts' --include='src/app/runtime/notification-log.service.spec.ts'
npx ng build --configuration production
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

The local gate proves the contract and its deterministic pipeline. A stand run
must separately prove real browser demand, event delivery, Yjs readback,
runtime version convergence, and operator visibility under deployed topology.

