param(
    [Parameter(Mandatory=$true)][string]$RunId,
    [ValidateSet('minimal','low','medium','high')][string]$Effort = 'low',
    [ValidateRange(1,20)][int]$Repetitions = 2,
    [string[]]$Cases = @()
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $root
try {
    $env:ADAOS_CONTROL_URL = 'http://127.0.0.1:8778'
    $env:ADAOS_BUILDER_LLM_MODEL = 'gpt-5'
    $env:ADAOS_BUILDER_LLM_REASONING_EFFORT = $Effort
    $env:ADAOS_BUILDER_LLM_MAX_TOKENS = '128000'
    $env:ADAOS_BUILDER_LLM_JOB_TIMEOUT_S = '600'
    $env:ADAOS_BUILDER_LLM_REPAIR_JOB_TIMEOUT_S = '600'
    $env:PYTHONIOENCODING = 'utf-8'
    $arguments = @('-c', 'from adaos.apps.cli.app import app; app()', 'builder', 'e2e',
        'e2e/builder/development/archetypes/suite.yaml', '--run-id', $RunId,
        '--repetitions', $Repetitions, '--browser', 'off')
    foreach ($case in $Cases) { $arguments += @('--case', $case) }
    & .venv/Scripts/python.exe @arguments
    exit $LASTEXITCODE
} finally { Pop-Location }
