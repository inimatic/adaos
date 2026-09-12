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

`replay-builder-candidates.py --builder-skill .adaos/dev/<subnet>/skills/builder_skill`
checks archived candidates through semantic compilation, resource materialization,
the actual DEV Builder payload validator and public UI postconditions, using the
exact retained instruction without applying a revision. Use a new output file; replay never replaces
fresh-generation scores. `retain-builder-job.py` preserves a terminal Root response
after a runner interruption, with an explicit not-applied marker.

The controlled `context-archetypes-gpt5-low-20260912-04` was stopped at a
Builder/live-dropdown contract blocker after four completed attempts. Its pending
budget response was retained separately. It is incomplete, not a full-cohort score.
Grader v13 constrains evidence pointers to the actual artifact and includes the
result schema in its request digest. Do not compare it silently with v12 scores.

Run `context-archetypes-gpt5-low-20260912-05` is a complete 4/8 cohort. Its lookup
postcondition failures and duplicate resource-local fields motivated the next
batch; subsequent replays do not change those scores. Browser `--probe readonly`
selects a fixture with a stored-record lock, checks disabled inputs/actions and
the provider's rejection of a direct update. It is separate from the mutable
`interactions` probe; unsupported tasks must remain visibly unexercised.

Run `context-archetypes-gpt5-low-20260912-06` was interrupted during its final
repair (13 passes, two validation failures, one unfinished attempt). Its
`interruption.json` preserves that classification; do not count it as a complete
cohort or relabel compiler replays as generation successes. Resume currently
cannot reconcile an orphaned worker solely from an absent terminal journal.

`ADAOS_E2E_DICTIONARY_PROBE=1` makes browser review wait for and verify an actual
authored value label from `assets/i18n/<locale>.json`. Interaction/command probes
verify returned record identity, not unique visible marker text: numeric values,
hidden input fields and sorted rows cannot serve as identity. Cleanup remains
limited to operations explicitly declared by the prototype.
`--probe interactions --field-type number` targets numeric edits explicitly;
`dropdown` and `singleChoice` also cover assignment and status editors. Probes
record the field type, preserve fixture values after the task, verify read-only
controls, and include the exact probe-script digest in their cohort receipt.

Run `context-archetypes-gpt5-low-20260912-08` retains 11 passes, three validation
failures and two inconclusive grades. Its original grader v13 exhausted its
output budget for library attempt 1; Root credits were exhausted for procurement
attempt 2. Do not retry paid inference until credits are restored. The separate
schema canary failed for the same quota reason, not schema incompatibility.

Grader v14 retains attempt-specific `-input.json`, `-response.json` and immutable
`-artifact.json` files under `evidence/grading`. A retry reuses that artifact and
rejects changed candidate identity; failed/partial Root responses retain job and
request IDs plus usage. Its model settings participate in request identity.
The default GPT-4.1 ceiling is 32768 output tokens; this is not a target response
length. Version 14 scores must not silently replace or compare as v13 scores.

For older cohorts, `retain-builder-evaluation-artifacts.py <run>` captures
revision-bound, pre-interaction snapshots for admitted, owned, unapproved DEV
tests. Run it before browser mutations and use a new output set; existing files
are never overwritten. Run 08 has 13 such artifacts. Its library first-input
overwrite predates per-attempt retention and remains an explicit evidence gap.

`probe-structured-output-contract.py --output <new-directory>` is a deliberate
live GPT-5/low canary for nested unions and bounds through the configured Root.
It creates no application, retains exact request/response and requires
`ENV_TYPE=dev`. A quota failure is inconclusive for schema support. Generation,
artifact replay, regrading, browser review and mutation probes remain distinct
evidence classes; none is a substitute for another.
