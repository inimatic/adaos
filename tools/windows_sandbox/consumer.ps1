# Runs only inside the disposable guest under its dedicated standard user.
$ErrorActionPreference = 'Stop'
$work = 'C:\AdaosConsumer'
$fixture = 'C:\AdaosFixture'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Consumer must not have administrator rights' }
if (Get-Command uv.exe -ErrorAction SilentlyContinue) { throw 'Unexpected preinstalled uv' }
$pythonCommands = @(Get-Command python.exe,python3.exe,py.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
if (@($pythonCommands | Where-Object { $_ -notlike '*\WindowsApps\*' }).Count) { throw 'Unexpected preinstalled Python' }
$initial = @{user=$identity.Name; administrator=$false; python_commands=$pythonCommands; os=[Environment]::OSVersion.VersionString; architecture=$env:PROCESSOR_ARCHITECTURE}
[IO.File]::WriteAllText((Join-Path $work 'initial.json'), ($initial | ConvertTo-Json -Depth 8))
$env:ADAOS_SHARED_DOTENV_PATH = Join-Path $work 'empty.env'
[IO.File]::WriteAllText($env:ADAOS_SHARED_DOTENV_PATH, '')
$env:ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST = '1'
$env:ADAOS_SUPERVISOR_ENABLED = '0'
$env:ADAOS_NLU_RASA = '0'
$env:ADAOS_GIT_MODE = 'disabled'
$env:ADAOS_TOKEN = 'isolated-sandbox-qualification-only'
$env:ADAOS_DISABLE_PREFERRED_PYTHON_REEXEC = '1'
$env:ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC = '1'
$env:GIT_PYTHON_REFRESH = 'quiet'
Remove-Item Env:PYTHONHOME,Env:PYTHONPATH -ErrorAction SilentlyContinue
$rejected = Join-Path $work 'rejected-uv.exe'
[IO.File]::WriteAllBytes($rejected, [Text.Encoding]::ASCII.GetBytes('UV is deliberately unavailable during Sandbox qualification.'))
$results = @()
foreach ($scenario in @('uv','pip','uv-failed')) {
    $started = [DateTime]::UtcNow
    $case = Join-Path $work $scenario
    New-Item -ItemType Directory -Path $case | Out-Null
    $destination = Join-Path $case 'node'
    $env:ADAOS_BASE_DIR = Join-Path $case 'state'
    $env:ADAOS_BOOTSTRAP_STATE_DIR = Join-Path $case 'outbox'
    # Immutable snapshots avoid sharing violations with the supervisor reader.
    [IO.File]::WriteAllText((Join-Path $work "progress-$scenario-install.json"), (@{scenario=$scenario;stage='install';time=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json))
    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File', (Join-Path $fixture 'release\bootstrap-distribution.ps1'),
        '-Dest',$destination,'-DistributionRoot','http://127.0.0.1:8799','-ZoneId','ru','-Channel','beta','-RuntimeOnly')
    if ($scenario -eq 'pip') { $arguments += '-WithoutUv' }
    if ($scenario -eq 'uv-failed') { $arguments += @('-UvBinary',$rejected) }
    & "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" @arguments > (Join-Path $case 'install.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Installer failed in $scenario with status $LASTEXITCODE" }
    if ((Get-Item -LiteralPath (Join-Path $destination '.venv')).LinkType -ne 'Junction') { throw 'Managed environment is not a junction' }
    [IO.File]::WriteAllText((Join-Path $work "progress-$scenario-verify.json"), (@{scenario=$scenario;stage='verify';time=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json))
    & (Join-Path $destination '.venv\Scripts\python.exe') -I (Join-Path $fixture 'check_runtime.py') --destination $destination --case $case --release (Join-Path $fixture 'release\release.json') --scenario $scenario > (Join-Path $case 'verification.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Runtime verification failed in $scenario with status $LASTEXITCODE" }
    $results += @{scenario=$scenario;ok=$true;administrator=$false;seconds=[Math]::Round(([DateTime]::UtcNow-$started).TotalSeconds,2)}
    [IO.File]::WriteAllText((Join-Path $work 'results.json'), (ConvertTo-Json -InputObject $results -Depth 10))
}
