# Codex Project Recipes

Status: working notes for local AdaOS development.

This page collects short, repeatable recipes that are easy to forget during
Codex-assisted work in this repository. It is intentionally operational: use it
for command syntax, local verification, and project-specific traps. Keep design
decisions in the architecture docs.

## Windows PowerShell and UTF-8

Prefer commands that keep UTF-8 explicit when reading or writing text with
Russian, Chinese, or other non-ASCII content.

```powershell
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
```

When inspecting files, prefer commands that do not re-encode through legacy code
pages:

```powershell
Get-Content path\to\file.json -Encoding UTF8
```

For quick JSON checks, use Python with explicit UTF-8:

```powershell
.venv\Scripts\python.exe -c "import json, pathlib; print(json.loads(pathlib.Path('path/to/file.json').read_text(encoding='utf-8')))"
```

If the UI shows `????`, treat it as a data-path bug until proven otherwise:
check the source file bytes, the API payload, stream/Yjs projection, and the
browser rendering payload separately.

For `adaos dev skill run`, never pass user-authored Unicode as inline JSON on
Windows. Store the payload as UTF-8 and use `--json-file`; reserve `--json` for
short ASCII-only payloads:

```powershell
.venv\Scripts\python.exe -m adaos dev skill run builder_skill chat `
  --json-file .adaos\state\builder\requests\chat.json
```

If a persisted request already contains `????`, the original code points have
been lost. Do not guess and silently rewrite history. Mark that evidence as
transport-corrupted/deferred and add a clean follow-up request.

PowerShell 5.1 re-encodes text sent through a native-process pipeline. Setting
only `PYTHONIOENCODING=utf-8` can therefore make Python decode an ASCII/legacy
pipeline as UTF-8 and turn a valid request into `????`. Set `$OutputEncoding`
before piping, or avoid the text pipeline entirely. When a payload file cannot
be used, construct test text from ASCII-only Unicode escapes before converting
it to Base64:

```powershell
$promptJson = '"\u041f\u0440\u043e\u0432\u0435\u0440\u044c \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f"'
$prompt = ConvertFrom-Json $promptJson
$env:ADAOS_PROMPT_B64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($prompt)
)
.venv\Scripts\python.exe -c "import base64, os; print(base64.b64decode(os.environ['ADAOS_PROMPT_B64']).decode('utf-8'))"
```

For Codex-operated Builder changes, the stronger repository rule is:

- create non-ASCII request payloads as UTF-8 files (normally with
  `apply_patch`) and pass them with `--json-file`;
- never generate a Cyrillic payload with a PowerShell here-string piped into
  Python, and never embed user text in `python -c` source;
- use Python only to *read* the UTF-8 file with an explicit encoding; use
  ASCII `\uXXXX` escapes or Base64 only when a file cannot be used;
- set `PYTHONIOENCODING=utf-8` for native-process output, or render diagnostic
  JSON with `ensure_ascii=True` when the surrounding console encoding is not
  under our control;
- verify the persisted bytes before invoking a state-changing tool. If the
  value already contains a replacement character or a long question-mark run,
  reject it at ingress rather than trying to reconstruct the original text.

This rule applies to request text, addenda, acceptance criteria, commit
messages, and any other user-authored text that becomes durable provenance.

## Python Tests

For targeted core tests from the repository root, set `PYTHONPATH` explicitly:

```powershell
$env:PYTHONPATH = "D:\git\inimatic\adaos\src"
.venv\Scripts\python.exe -m pytest tests/test_router_voice_chat.py -q
```

For workspace skill tests, run the test file directly with the same root
`PYTHONPATH` unless the skill lifecycle command is the thing being tested:

```powershell
$env:PYTHONPATH = "D:\git\inimatic\adaos\src"
.venv\Scripts\python.exe -m pytest .adaos/workspace/skills/builder_skill/tests/test_builder_skill.py -q
```

Avoid broad `pytest` while debugging a narrow issue. This repo contains multiple
integration trees and generated/runtime folders; targeted tests give faster and
clearer signal.

## Angular Client Tests

The AdaOS client uses Angular/Karma, not Jest. From
`src/adaos/integrations/adaos-client`, run targeted specs with `ng test`:

```powershell
npx ng test --watch=false --browsers=ChromeHeadless --include=src/app/runtime/layout-render-plan.spec.ts
```

Multiple includes are supported:

```powershell
npx ng test --watch=false --browsers=ChromeHeadless `
  --include=src/app/runtime/layout-render-plan.spec.ts `
  --include=src/app/renderer/widgets/chat.widget.component.spec.ts
```

Before committing browser-side changes, also run:

```powershell
npm run build
```

## Workspace Skills

First identify the source plane. A project under
`.adaos/dev/<node>/skills/<name>` is a DEV/Forge artifact and must be
checkpointed with:

```powershell
.venv\Scripts\python.exe -m adaos dev skill push <name> -m "message"
```

Do not substitute `adaos skill push`: that command writes the stable
`.adaos/workspace` registry checkout and can create an unintended Publication
commit. The corresponding distinction for scenarios is `adaos dev scenario
push` versus `adaos scenario push`.

Workspace skills are runtime artifacts, not plain source folders. Do not rely on
`git push` alone to make a workspace skill available to the local runtime.

For `builder_skill`, the normal local release loop is:

```powershell
$env:PYTHONPATH = "D:\git\inimatic\adaos\src"
.venv\Scripts\python.exe -m adaos skill push builder_skill --no-bump
.venv\Scripts\python.exe -m adaos skill activate builder_skill
```

Use `adaos skill push` for workspace skills. Use Git commits to version the
source repository, but use the AdaOS skill lifecycle to publish/activate what
the runtime executes.

If a tool still runs old skill behavior after push/activation, do not hide it
with an API restart as the first answer. Check the active slot and handler reload
path, then restart only as a diagnostic or temporary recovery step.

Installed dependencies named by a scenario are not automatically project-owned
DEV companions. Keep an installed-only dependency immutable during Automation.
Publication may synthesize its first package identity from the installed
Workspace copy, but the resulting `WorkspaceLock` must pin its digest and source
as `workspace-migration`; subsequent builds resolve it from stable releases.

## Publication Activation Recovery

Stable Publication is a transaction across the channel pointer, WorkspaceLock,
materialized artifacts, runtime reload, and exact health verification. Do not
retry `publish_project` after an uncertain or failed response. Inspect the
promotion and activation receipts first.

If the activation operation is explicitly `failed`, is confirmed rolled back,
and the promotion is paused without a Workspace receipt, recover it once:

```powershell
.venv\Scripts\python.exe -m adaos dev root recover-promotion-activation `
  <candidate-id> <failed-operation-id>
```

The command validates the exact candidate, release, and failed operation, then
records a new idempotency key. Resume the ordinary publication command only
after this explicit recovery. The existing channel receipt is reused; the
stable pointer is not moved a second time.

## Local Runtime Restart

When a local API restart is needed during debugging:

```powershell
$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq "python.exe" -and $_.CommandLine -match "adaos api serve"
}
foreach ($p in $procs) {
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.ProcessId)" |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  Stop-Process -Id $p.ProcessId -Force
}
Start-Sleep -Seconds 2
Start-Process `
  -FilePath "D:\git\inimatic\adaos\.venv\Scripts\python.exe" `
  -ArgumentList @("-m", "adaos", "api", "serve") `
  -WorkingDirectory "D:\git\inimatic\adaos" `
  -WindowStyle Hidden
```

The local browser client is commonly served on `http://127.0.0.1:8100`, and the
hub/API on `http://127.0.0.1:8777` in current debugging sessions.

After restart, verify the listener owner instead of assuming the newly launched
parent owns the port:

```powershell
Get-NetTCPConnection -LocalPort 8777 -State Listen |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

An older child process can survive termination of its launcher and continue to
serve stale code. Stop only the resolved AdaOS API process tree and re-check the
listener before starting another instance.

## Browser Debugging

For Chrome with remote debugging:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9226 `
  --remote-allow-origins=* `
  --user-data-dir="$env:TEMP\inimatic-debug-chrome" `
  --no-first-run `
  "http://127.0.0.1:8100/?runtime_debug=1"
```

Use browser-observed availability and logs together. A UI symptom such as
`Limited`, missing chat history, or stale `desktop-dev` rendering can be a
stream/Yjs guard symptom rather than a component bug.

## Useful Log Filters

From the repository root:

```powershell
Get-Content .adaos\logs\adaos.log -Tail 200 |
  Select-String -Pattern "builder|voice_chat.messages|quarantined|pending action|YJS|llm_job"
```

For Builder flows, inspect both the chat stream and persisted prototype
artifacts:

```powershell
Get-ChildItem .adaos\dev -Recurse -Filter "*.json" |
  Where-Object { $_.FullName -match "ui_revisions" } |
  Sort-Object FullName
```

## Git Boundaries

This repository contains nested repositories such as:

- `src/adaos/integrations/adaos-client`
- `.adaos/workspace/skills/builder_skill`

Check and commit each repository at its own boundary:

```powershell
git status -sb
git -C src/adaos/integrations/adaos-client status -sb
git -C .adaos/workspace/skills/builder_skill status -sb
```

When a nested repository changes, commit and push it there first, then commit
the parent repository pointer or related root changes.
