param(
    [string]$AdbPath = "adb",
    [string]$Serial = "",
    [int]$ForwardPort = 18777,
    [double]$DurationHours = 6.25,
    [int]$IntervalSeconds = 60,
    [string]$ReportPath = "$PSScriptRoot\app\build\reports\android-special-use-soak-evidence.json",
    [switch]$StartNode
)

$ErrorActionPreference = "Stop"
$packageName = "dev.adaos.androidnode"
$activityName = "$packageName/.MainActivity"
$adbTarget = @()
if ($Serial) {
    $adbTarget = @("-s", $Serial)
}

function Invoke-Adb {
    param([string[]]$AdbArguments)
    & $AdbPath @adbTarget @AdbArguments
}

function Get-TimeoutCount {
    $events = Invoke-Adb -AdbArguments @("logcat", "-b", "events", "-d", "-v", "brief") 2>$null
    return @($events | Select-String -Pattern "am_foreground_service_timed_out.*$packageName").Count
}

function Write-Evidence {
    param([System.Collections.IDictionary]$Evidence)
    $directory = Split-Path -Parent $ReportPath
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = "$ReportPath.tmp"
    $Evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $ReportPath -Force
}

$devices = & $AdbPath devices
if ($LASTEXITCODE -ne 0 -or -not ($devices -match "\sdevice\s*$")) {
    throw "No authorized Android device is visible to adb."
}

Invoke-Adb -AdbArguments @("forward", "tcp:$ForwardPort", "tcp:8777") | Out-Null
if ($StartNode) {
    Invoke-Adb -AdbArguments @(
        "shell", "am", "start", "-n", $activityName, "--ez", "start_node", "true"
    ) | Out-Null
}

$startedAt = [DateTimeOffset]::UtcNow
$deadline = $startedAt.AddHours($DurationHours)
$baselineTimeouts = Get-TimeoutCount
$samples = [System.Collections.Generic.List[object]]::new()
$evidence = [ordered]@{
    schema = "adaos.android.special_use_soak.v1"
    package = $packageName
    started_at = $startedAt.ToString("o")
    target_duration_seconds = [int]($DurationHours * 3600)
    interval_seconds = $IntervalSeconds
    baseline_timeout_events = $baselineTimeouts
    completed_at = $null
    elapsed_seconds = 0
    new_timeout_events = 0
    ready_samples = 0
    unavailable_samples = 0
    process_restarts = 0
    success = $false
    samples = $samples
}

$previousPid = ""
do {
    $timestamp = [DateTimeOffset]::UtcNow
    $sample = [ordered]@{
        timestamp = $timestamp.ToString("o")
        adb_connected = $false
        pid = ""
        service_present = $false
        ready = $false
        app_version = ""
        node_id = ""
        subnet_id = ""
        member_state = ""
        error = ""
    }
    try {
        $state = (
            Invoke-Adb -AdbArguments @("get-state") 2>$null | Select-Object -First 1
        ).Trim()
        $sample.adb_connected = $state -eq "device"
        if (-not $sample.adb_connected) {
            throw "adb_not_connected"
        }
        $sample.pid = (
            Invoke-Adb -AdbArguments @("shell", "pidof", $packageName) 2>$null
        ).Trim()
        if ($previousPid -and $sample.pid -and $sample.pid -ne $previousPid) {
            $evidence.process_restarts++
        }
        if ($sample.pid) {
            $previousPid = $sample.pid
        }
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:$ForwardPort/api/node/status" -TimeoutSec 5
        $sample.ready = [bool]$status.ready
        $sample.app_version = [string]$status.runtime.app_version
        $sample.node_id = [string]$status.node_id
        $sample.subnet_id = [string]$status.subnet_id
        $sample.member_state = [string]$status.runtime.member_link.state
        $boundarySample = $samples.Count -eq 0 -or $timestamp.AddSeconds(
            [Math]::Max(1, $IntervalSeconds * 2)
        ) -ge $deadline
        if ($boundarySample) {
            $services = Invoke-Adb -AdbArguments @(
                "shell", "dumpsys", "activity", "services", $packageName
            ) 2>$null
            $sample.service_present = [bool]($services -match "NodeService")
        } else {
            $sample.service_present = $sample.ready
        }
        if ($sample.ready -and $sample.service_present) {
            $evidence.ready_samples++
        } else {
            $evidence.unavailable_samples++
        }
    } catch {
        $sample.error = $_.Exception.Message
        $evidence.unavailable_samples++
    }
    $samples.Add([pscustomobject]$sample)
    $evidence.elapsed_seconds = [int]($timestamp - $startedAt).TotalSeconds
    Write-Evidence $evidence
    if ($timestamp -lt $deadline) {
        Start-Sleep -Seconds $IntervalSeconds
    }
} while ([DateTimeOffset]::UtcNow -lt $deadline)

$evidence.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
$evidence.elapsed_seconds = [int]([DateTimeOffset]::UtcNow - $startedAt).TotalSeconds
$evidence.new_timeout_events = [Math]::Max(0, (Get-TimeoutCount) - $baselineTimeouts)
$lastSample = $samples | Select-Object -Last 1
$evidence.success = (
    $evidence.elapsed_seconds -ge [int]($DurationHours * 3600) -and
    $evidence.new_timeout_events -eq 0 -and
    $null -ne $lastSample -and
    $lastSample.ready -and
    $lastSample.service_present
)
Write-Evidence $evidence
$evidence | ConvertTo-Json -Depth 8
if (-not $evidence.success) {
    exit 1
}
