param(
    [string]$AdbPath = "adb",
    [string]$ApkPath = "$PSScriptRoot\app\build\outputs\apk\debug\app-debug.apk",
    [int]$ForwardPort = 18777,
    [int]$DurationSeconds = 1800,
    [int]$SampleIntervalSeconds = 10,
    [int]$OutageSeconds = 30,
    [int]$TimeoutSeconds = 60,
    [switch]$SkipInstall,
    [string]$EvidencePath = "$PSScriptRoot\app\build\reports\android-lifecycle-evidence.json"
)

$ErrorActionPreference = "Stop"
$packageName = "dev.adaos.androidnode"
$activityName = "$packageName/.MainActivity"
$statusUrl = "http://127.0.0.1:$ForwardPort/api/node/status"
$steadyPssLimitKiB = 200 * 1024
$startupPssLimitKiB = 320 * 1024
$maxLifecycleLogLines = 512
$maxLifecycleLogChars = 128 * 1024
$samples = [Collections.Generic.List[object]]::new()
$checks = [ordered]@{}
$startupPeakPssKiB = 0
$wifiInitiallyEnabled = $null
$dataInitiallyEnabled = $null
$nodeShouldBeRunning = $false

function Invoke-AdbChecked {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AdbArguments)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $AdbPath @AdbArguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "adb $($AdbArguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Get-NodeStatus {
    return Invoke-RestMethod -Uri $statusUrl -TimeoutSec 3
}

function Get-PackageMemory {
    $raw = (& $AdbPath shell dumpsys meminfo $packageName 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $raw -notmatch "TOTAL PSS:\s*([\d,]+).*TOTAL RSS:\s*([\d,]+)") {
        return $null
    }
    return [ordered]@{
        pss_kib = [int64](($Matches[1] -replace ",", ""))
        rss_kib = [int64](($Matches[2] -replace ",", ""))
    }
}

function Add-MemorySample {
    param([string]$Phase, $Status = $null)
    $memory = Get-PackageMemory
    if (-not $Status) {
        try { $Status = Get-NodeStatus } catch { $Status = $null }
    }
    $procPss = 0
    $procPeak = 0
    $queueRejected = 0
    $requestRejected = 0
    $memberDropped = 0
    if ($Status) {
        $procPss = [int64]$Status.runtime.resources.process.pss_kib
        $procPeak = [int64]$Status.runtime.resources.process.peak_pss_kib
        $queueRejected = [int64]$Status.runtime.resource_bounds.ystore.task_queue_rejected
        $requestRejected = [int64]$Status.runtime.resource_bounds.loopback.rejected_requests
        $memberDropped = [int64]$Status.runtime.resource_bounds.member_link.dropped_messages
    }
    $pss = if ($memory) { [int64]$memory.pss_kib } else { $procPss }
    $script:startupPeakPssKiB = [Math]::Max(
        $script:startupPeakPssKiB,
        [Math]::Max($pss, $procPeak)
    )
    $samples.Add([ordered]@{
        sampled_at = [DateTimeOffset]::UtcNow.ToString("o")
        phase = $Phase
        pss_kib = $pss
        rss_kib = if ($memory) { [int64]$memory.rss_kib } else { 0 }
        procfs_pss_kib = $procPss
        procfs_sampled_peak_pss_kib = $procPeak
        ystore_queue_rejected = $queueRejected
        loopback_requests_rejected = $requestRejected
        member_messages_dropped = $memberDropped
    })
}

function Wait-NodeReady {
    param([string]$Phase)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $status = $null
        try { $status = Get-NodeStatus } catch { }
        Add-MemorySample -Phase $Phase -Status $status
        if ($status -and $status.ready -and $status.node_state -eq "ready") {
            return $status
        }
        Start-Sleep -Milliseconds 500
    } until ([DateTimeOffset]::UtcNow -ge $deadline)
    throw "AdaOS did not become ready within $TimeoutSeconds seconds during $Phase."
}

function Assert-ReadyAndStable {
    param($Expected, [string]$Phase)
    $status = Get-NodeStatus
    if (-not $status.ready -or $status.node_state -ne "ready") {
        throw "Node is not ready during ${Phase}: $($status | ConvertTo-Json -Compress -Depth 8)"
    }
    if ($status.node_id -ne $Expected.node_id) {
        throw "Node identity changed during $Phase."
    }
    if ([double]$status.runtime.started_at -ne [double]$Expected.runtime.started_at) {
        throw "Python runtime restarted unexpectedly during $Phase."
    }
    if ([int]$status.runtime.resource_bounds.loopback.request_thread_limit -ne 32) {
        throw "Loopback request-thread bound is missing."
    }
    if ([int]$status.runtime.resource_bounds.ystore.task_queue_limit -ne 64) {
        throw "YStore queue bound is missing."
    }
    if ($status.runtime.resources.policy.large_heap_requested) {
        throw "Runtime reports largeHeap usage."
    }
    Add-MemorySample -Phase $Phase -Status $status
    return $status
}

function Wait-NodeUnavailable {
    param([int]$Seconds)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Seconds)
    do {
        try {
            $status = Get-NodeStatus
            if (-not $status.ready -or $status.node_state -ne "ready") {
                return
            }
        } catch {
            return
        }
        Start-Sleep -Milliseconds 500
    } until ([DateTimeOffset]::UtcNow -ge $deadline)
    throw "Loopback endpoint remained available after an explicit stop."
}

function Start-NodeActivity {
    Invoke-AdbChecked -AdbArguments @(
        "shell", "am", "start", "-n", $activityName,
        "-a", "$packageName.action.DEBUG_START_NODE", "-f", "0x20000000"
    ) | Out-Null
}

function Set-WifiState {
    param([bool]$Enabled)
    $mode = if ($Enabled) { "enable" } else { "disable" }
    Invoke-AdbChecked shell svc wifi $mode | Out-Null
}

function Set-DataState {
    param([bool]$Enabled)
    $mode = if ($Enabled) { "enable" } else { "disable" }
    Invoke-AdbChecked shell svc data $mode | Out-Null
}

function Wait-NetworkValidated {
    param([int]$Seconds = 45)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Seconds)
    do {
        $connectivity = (Invoke-AdbChecked shell dumpsys connectivity) -join "`n"
        if ($connectivity -match "IS_VALIDATED") {
            return
        }
        Start-Sleep -Seconds 1
    } until ([DateTimeOffset]::UtcNow -ge $deadline)
    throw "Android network did not return to a validated state within $Seconds seconds."
}

function Write-Phase {
    param([string]$Name)
    Write-Host "lifecycle phase=$Name at=$([DateTimeOffset]::UtcNow.ToString('o'))"
}

try {
    Write-Phase "device_facts"
    $devices = & $AdbPath devices
    if ($LASTEXITCODE -ne 0 -or -not ($devices -match "\sdevice\s*$")) {
        throw "No authorized Android device is visible to adb."
    }
    $abi = ((Invoke-AdbChecked shell getprop ro.product.cpu.abi) -join "").Trim()
    $api = [int](((Invoke-AdbChecked shell getprop ro.build.version.sdk) -join "").Trim())
    $model = ((Invoke-AdbChecked shell getprop ro.product.model) -join "").Trim()
    $build = ((Invoke-AdbChecked shell getprop ro.build.fingerprint) -join "").Trim()
    $chromePackage = (Invoke-AdbChecked shell dumpsys package com.android.chrome) -join "`n"
    $chromeVersionName = "unknown"
    $chromeVersionCode = "unknown"
    if ($chromePackage -match "versionName=([^\s]+)") { $chromeVersionName = $Matches[1] }
    if ($chromePackage -match "versionCode=([^\s]+)") { $chromeVersionCode = $Matches[1] }
    $pageSize = [int](((Invoke-AdbChecked shell getconf PAGESIZE) -join "").Trim())
    $memoryTotalKiB = 0
    $meminfo = (Invoke-AdbChecked shell cat /proc/meminfo) -join "`n"
    if ($meminfo -match "MemTotal:\s*(\d+)\s*kB") {
        $memoryTotalKiB = [int64]$Matches[1]
    }
    if ($abi -ne "arm64-v8a" -or $api -lt 26) {
        throw "Unsupported test device: ABI=$abi API=$api."
    }

    $wifiInitiallyEnabled = (((Invoke-AdbChecked shell settings get global wifi_on) -join "").Trim() -eq "1")
    $dataInitiallyEnabled = (((Invoke-AdbChecked shell settings get global mobile_data) -join "").Trim() -eq "1")

    if (-not $SkipInstall) {
        Write-Phase "install"
        $resolvedApk = (Resolve-Path -LiteralPath $ApkPath).Path
        Invoke-AdbChecked install -r $resolvedApk | Out-Null
        # Package replacement can retain a task/notification PendingIntent on
        # some Samsung builds. Establish a deterministic lifecycle baseline.
        Invoke-AdbChecked shell am force-stop $packageName | Out-Null
        Start-Sleep -Seconds 1
    }
    Invoke-AdbChecked forward "tcp:$ForwardPort" "tcp:8777" | Out-Null
    Invoke-AdbChecked logcat -c | Out-Null
    Write-Phase "startup"
    Start-NodeActivity
    $nodeShouldBeRunning = $true
    $initial = Wait-NodeReady -Phase "startup"
    $testStarted = [DateTimeOffset]::UtcNow
    $initialDescriptor = [string]$initial.runtime.install_descriptor_sha256
    $initialMemberConfigured = [bool]$initial.runtime.member_link.configured
    $initialMemberRoot = [string]$initial.runtime.member_link.root_url
    $initialMemberSubnet = [string]$initial.runtime.member_link.subnet_id

    if ($initial.environment.local_auth_required) {
        throw "Loopback authentication must be disabled for the LO zone."
    }
    if ([int]$initial.runtime.resource_bounds.skills.note_count_limit -ne 256) {
        throw "Notebook bounds are absent from status."
    }
    $checks.runtime_bounds = $true

    Write-Phase "activity_recreate"
    Invoke-AdbChecked shell am start -n $activityName -f 0x10008000 | Out-Null
    Start-Sleep -Seconds 2
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "activity_recreate"
    $activityLog = (
        Invoke-AdbChecked -AdbArguments @(
            "logcat", "-d", "-s", "AdaOSNodeActivity:I", "*:S"
        )
    ) -join "`n"
    if ($activityLog -notmatch "onDestroy" -or $activityLog -notmatch "onCreate") {
        throw "Activity recreation was not visible in logcat."
    }
    $checks.activity_recreated_without_runtime_restart = $true

    Write-Phase "screen_off_on"
    Invoke-AdbChecked shell input keyevent 26 | Out-Null
    Start-Sleep -Seconds 2
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "screen_off"
    Invoke-AdbChecked shell input keyevent 224 | Out-Null
    Start-Sleep -Seconds 2
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "screen_on"
    $checks.screen_off_on = $true

    Write-Phase "browser_foreground_background"
    $chrome = (Invoke-AdbChecked shell pm list packages com.android.chrome) -join ""
    if ($chrome -notmatch "package:com.android.chrome") {
        throw "Chrome is not installed; browser foreground/background gate cannot run."
    }
    Invoke-AdbChecked shell monkey -p com.android.chrome -c android.intent.category.LAUNCHER 1 | Out-Null
    Start-Sleep -Seconds 2
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "browser_foreground"
    Invoke-AdbChecked shell am start -n $activityName | Out-Null
    Start-Sleep -Seconds 2
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "browser_background"
    $checks.browser_foreground_background = $true

    Write-Phase "wifi_loss"
    Set-WifiState -Enabled $true
    Start-Sleep -Seconds 3
    Set-WifiState -Enabled $false
    Start-Sleep -Seconds $OutageSeconds
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "wifi_loss"
    Set-WifiState -Enabled $wifiInitiallyEnabled
    $checks.wifi_loss = $true

    Write-Phase "wan_loss"
    Set-WifiState -Enabled $false
    Set-DataState -Enabled $false
    Start-Sleep -Seconds $OutageSeconds
    $initial = Assert-ReadyAndStable -Expected $initial -Phase "wan_loss"
    Set-WifiState -Enabled $wifiInitiallyEnabled
    Set-DataState -Enabled $dataInitiallyEnabled
    if ($wifiInitiallyEnabled -or $dataInitiallyEnabled) {
        Wait-NetworkValidated
        Start-Sleep -Seconds 8
    }
    $checks.wan_loss = $true

    Write-Phase "soak"
    $soakDeadline = $testStarted.AddSeconds([Math]::Max(1, $DurationSeconds))
    $sampleIndex = 0
    do {
        $sampleIndex += 1
        $initial = Assert-ReadyAndStable -Expected $initial -Phase "soak_$sampleIndex"
        $latest = $samples[$samples.Count - 1]
        Write-Host "soak sample=$sampleIndex pss_kib=$($latest.pss_kib) elapsed_s=$([int]([DateTimeOffset]::UtcNow - $testStarted).TotalSeconds)"
        if ([DateTimeOffset]::UtcNow -lt $soakDeadline) {
            Start-Sleep -Seconds ([Math]::Max(1, $SampleIntervalSeconds))
        }
    } while ([DateTimeOffset]::UtcNow -lt $soakDeadline)
    $checks.soak_duration_seconds = [int]([DateTimeOffset]::UtcNow - $testStarted).TotalSeconds

    Write-Phase "persistence_markers"
    $yjsMarker = "lifecycle-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
    & py -3.11 "$PSScriptRoot\verify_yjs_restart.py" write --uri "ws://127.0.0.1:$ForwardPort/yws/desktop" --value $yjsMarker
    if ($LASTEXITCODE -ne 0) { throw "Writing the lifecycle Yjs marker failed." }
    $skillMarker = "Lifecycle Notebook $([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
    $skillScenarioReady = $false
    for ($attempt = 1; $attempt -le 3 -and -not $skillScenarioReady; $attempt += 1) {
        & py -3.11 "$PSScriptRoot\verify_android_skills.py" run --base-url "http://127.0.0.1:$ForwardPort" --marker $skillMarker
        $skillScenarioReady = ($LASTEXITCODE -eq 0)
        if (-not $skillScenarioReady -and $attempt -lt 3) {
            Write-Host "skill scenario retry=$($attempt + 1) after network recovery"
            Start-Sleep -Seconds 10
            Wait-NetworkValidated
        }
    }
    if (-not $skillScenarioReady) { throw "The lifecycle skill scenario failed." }

    Write-Phase "force_stop_restart"
    Invoke-AdbChecked shell am force-stop $packageName | Out-Null
    Wait-NodeUnavailable -Seconds 10
    Start-NodeActivity
    $restarted = Wait-NodeReady -Phase "forced_restart"
    & py -3.11 "$PSScriptRoot\verify_yjs_restart.py" verify --uri "ws://127.0.0.1:$ForwardPort/yws/desktop" --value $yjsMarker
    if ($LASTEXITCODE -ne 0) { throw "Yjs state did not survive force-stop/restart." }
    & py -3.11 "$PSScriptRoot\verify_android_skills.py" verify --base-url "http://127.0.0.1:$ForwardPort" --marker $skillMarker
    if ($LASTEXITCODE -ne 0) { throw "Notebook state did not survive force-stop/restart." }
    if ([string]$restarted.runtime.install_descriptor_sha256 -ne $initialDescriptor) {
        throw "Install descriptor changed across force-stop/restart."
    }
    if (
        [bool]$restarted.runtime.member_link.configured -ne $initialMemberConfigured -or
        [string]$restarted.runtime.member_link.root_url -ne $initialMemberRoot -or
        [string]$restarted.runtime.member_link.subnet_id -ne $initialMemberSubnet
    ) {
        throw "Membership state changed across force-stop/restart."
    }
    $checks.force_stop_persistence = $true

    Write-Phase "user_stop"
    Invoke-AdbChecked -AdbArguments @(
        "shell", "am", "start", "-n", $activityName,
        "-a", "$packageName.action.DEBUG_STOP_NODE", "-f", "0x20000000"
    ) | Out-Null
    Wait-NodeUnavailable -Seconds $TimeoutSeconds
    $nodeShouldBeRunning = $false
    Start-Sleep -Seconds 10
    $resurrected = $false
    try {
        $stoppedStatus = Get-NodeStatus
        $resurrected = (
            $stoppedStatus.ready -and $stoppedStatus.node_state -eq "ready"
        )
    } catch { }
    if ($resurrected) {
        throw "Node silently resurrected after the user stop action."
    }
    $serviceDump = (Invoke-AdbChecked shell dumpsys activity services $packageName) -join "`n"
    if ($serviceDump -match "NodeService") {
        throw "NodeService remains registered after the user stop action."
    }
    $checks.user_stop_no_resurrection = $true

    Write-Phase "final_restart"
    Start-NodeActivity
    $nodeShouldBeRunning = $true
    $final = Wait-NodeReady -Phase "final_restart"

    $steadySamples = @($samples | Where-Object { $_.phase -like "soak_*" -and $_.pss_kib -gt 0 })
    $steadyPeakPssKiB = 0
    foreach ($sample in $steadySamples) {
        $steadyPeakPssKiB = [Math]::Max(
            $steadyPeakPssKiB,
            [int64]$sample.pss_kib
        )
    }
    $runtimeStartupPeak = [int64]$final.runtime.resources.process.peak_pss_kib
    $startupPeakPssKiB = [Math]::Max($startupPeakPssKiB, $runtimeStartupPeak)
    if ($steadyPeakPssKiB -le 0 -or $steadyPeakPssKiB -gt $steadyPssLimitKiB) {
        throw "Steady PSS gate failed: $steadyPeakPssKiB KiB (limit $steadyPssLimitKiB KiB)."
    }
    if ($startupPeakPssKiB -le 0 -or $startupPeakPssKiB -gt $startupPssLimitKiB) {
        throw "Startup PSS gate failed: $startupPeakPssKiB KiB (limit $startupPssLimitKiB KiB)."
    }
    if (@($samples | Where-Object {
        $_.ystore_queue_rejected -gt 0 -or
        $_.loopback_requests_rejected -gt 0 -or
        $_.member_messages_dropped -gt 0
    }).Count -gt 0) {
        throw "A bounded runtime queue rejected or dropped work during the lifecycle gate."
    }
    $checks.memory_budgets = $true
    $checks.bounded_queues_no_rejections = $true

    Write-Phase "evidence"
    $serviceLogLines = @(
        Invoke-AdbChecked -AdbArguments @(
            "logcat", "-d", "-s", "AdaOSNodeService:I",
            "AdaOSNodeActivity:I", "Python:I", "*:S"
        )
    )
    if ($serviceLogLines.Count -gt $maxLifecycleLogLines) {
        $headCount = [int]($maxLifecycleLogLines / 2)
        $tailCount = $maxLifecycleLogLines - $headCount - 1
        $serviceLogLines = @(
            $serviceLogLines | Select-Object -First $headCount
            "... lifecycle log middle omitted by bounded evidence capture ..."
            $serviceLogLines | Select-Object -Last $tailCount
        )
    }
    $serviceLog = $serviceLogLines -join "`n"
    if ($serviceLog.Length -gt $maxLifecycleLogChars) {
        $half = [int](($maxLifecycleLogChars - 80) / 2)
        $serviceLog = $serviceLog.Substring(0, $half) +
            "`n... lifecycle log middle omitted by character bound ...`n" +
            $serviceLog.Substring($serviceLog.Length - $half)
    }
    $result = [ordered]@{
        schema = "adaos.android.lifecycle.evidence.v1"
        ok = $true
        captured_at = [DateTimeOffset]::UtcNow.ToString("o")
        artifact = [ordered]@{
            package = $packageName
            app_version = [string]$final.runtime.app_version
            repository_commit = (git rev-parse HEAD).Trim()
            apk_sha256 = if (Test-Path -LiteralPath $ApkPath) {
                (Get-FileHash -LiteralPath $ApkPath -Algorithm SHA256).Hash.ToLower()
            } else { "not-recorded" }
            install_descriptor_sha256 = [string]$final.runtime.install_descriptor_sha256
        }
        device = [ordered]@{
            model = $model
            api = $api
            abi = $abi
            build_fingerprint = $build
            memory_total_kib = $memoryTotalKiB
            page_size_bytes = $pageSize
        }
        browser = [ordered]@{
            package = "com.android.chrome"
            version_name = $chromeVersionName
            version_code = $chromeVersionCode
        }
        node_id = [string]$final.node_id
        budgets = [ordered]@{
            steady_pss_limit_kib = $steadyPssLimitKiB
            steady_peak_pss_kib = $steadyPeakPssKiB
            startup_pss_limit_kib = $startupPssLimitKiB
            startup_sampled_peak_pss_kib = $startupPeakPssKiB
        }
        checks = $checks
        membership_configured = $initialMemberConfigured
        samples = $samples
        lifecycle_log = $serviceLog
    }
    $parent = Split-Path -Parent $EvidencePath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $EvidencePath -Encoding utf8
    $result | ConvertTo-Json -Depth 6
} finally {
    if ($null -ne $wifiInitiallyEnabled) {
        try { Set-WifiState -Enabled $wifiInitiallyEnabled } catch { Write-Warning $_ }
    }
    if ($null -ne $dataInitiallyEnabled) {
        try { Set-DataState -Enabled $dataInitiallyEnabled } catch { Write-Warning $_ }
    }
    try { Invoke-AdbChecked shell input keyevent 224 | Out-Null } catch { }
    if ($nodeShouldBeRunning) {
        try { Start-NodeActivity } catch { }
    }
}
