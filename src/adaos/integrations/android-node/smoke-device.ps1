param(
    [string]$AdbPath = "adb",
    [string]$ApkPath = "$PSScriptRoot\app\build\outputs\apk\debug\app-debug.apk",
    [int]$ForwardPort = 18777,
    [int]$TimeoutSeconds = 45,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$packageName = "dev.adaos.androidnode"
$activityName = "$packageName/.MainActivity"
$resolvedApk = (Resolve-Path -LiteralPath $ApkPath).Path

$devices = & $AdbPath devices
if ($LASTEXITCODE -ne 0 -or -not ($devices -match "\sdevice\s*$")) {
    throw "No authorized Android device is visible to adb."
}

$abi = (& $AdbPath shell getprop ro.product.cpu.abi).Trim()
$api = [int]((& $AdbPath shell getprop ro.build.version.sdk).Trim())
if ($abi -ne "arm64-v8a") {
    throw "The PoC APK requires arm64-v8a; device reports $abi."
}
if ($api -lt 26) {
    throw "The PoC APK requires API 26+; device reports API $api."
}

& $AdbPath install -r $resolvedApk
if ($LASTEXITCODE -ne 0) {
    throw "adb install failed."
}
& $AdbPath forward "tcp:$ForwardPort" "tcp:8777" | Out-Null
& $AdbPath shell am start -n $activityName --ez start_node true | Out-Null

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
$status = $null
do {
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:$ForwardPort/api/node/status" -TimeoutSec 2
    } catch {
        Start-Sleep -Milliseconds 500
    }
} until ($status -or [DateTimeOffset]::UtcNow -ge $deadline)

if (-not $status) {
    throw "Android node did not expose its status within $TimeoutSeconds seconds."
}
if (-not $status.ready -or $status.node_state -ne "ready") {
    throw "Android node is not ready: $($status | ConvertTo-Json -Compress -Depth 8)"
}
if (-not $status.runtime.yjs_ready) {
    throw "Android node reports yjs_ready=false."
}
if ($status.environment.local_auth_required) {
    throw "Android loopback runtime unexpectedly requires authentication."
}

if ($OpenBrowser) {
    & $AdbPath shell am start -a android.intent.action.VIEW -d "https://inimatic.com/?zone=lo&try_local_hub=1&runtime_debug=1" | Out-Null
}

[ordered]@{
    ok = $true
    device_api = $api
    device_abi = $abi
    node_id = $status.node_id
    subnet_id = $status.subnet_id
    app_version = $status.runtime.app_version
    python_version = $status.runtime.python_version
    yjs_mode = $status.runtime.yjs_mode
    skills_ready = $status.runtime.skills_ready
    status_url = "http://127.0.0.1:$ForwardPort/api/node/status"
} | ConvertTo-Json -Depth 4
