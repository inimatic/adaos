param(
    [string]$AdbPath = "adb",
    [int]$NodeForwardPort = 18777,
    [int]$RootPort = 18778,
    [int]$HubPort = 18779,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$packageName = "dev.adaos.androidnode"
$activityName = "$packageName/.MainActivity"
$joinCode = "ANDROID-POC-JOIN"
$fixtureProcess = $null
$fixtureOut = Join-Path ([System.IO.Path]::GetTempPath()) "adaos-member-fixture-$PID.out"
$fixtureErr = Join-Path ([System.IO.Path]::GetTempPath()) "adaos-member-fixture-$PID.err"
$baseUrl = "http://127.0.0.1:$NodeForwardPort"

function Wait-Until([scriptblock]$Condition, [string]$Description) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $value = & $Condition
            if ($value) {
                return $value
            }
        } catch {
        }
        Start-Sleep -Milliseconds 250
    } until ([DateTimeOffset]::UtcNow -ge $deadline)
    throw "Timed out waiting for $Description."
}

function Start-Fixture {
    $script = Join-Path $PSScriptRoot "verify_member_link.py"
    $script:fixtureProcess = Start-Process -FilePath "py" -ArgumentList @(
        "-3.11",
        $script,
        "--root-port", "$RootPort",
        "--hub-port", "$HubPort",
        "--code", $joinCode
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $fixtureOut -RedirectStandardError $fixtureErr
    Wait-Until {
        Invoke-RestMethod -Uri "http://127.0.0.1:$RootPort/evidence" -TimeoutSec 2
    } "member fixture startup" | Out-Null
}

function Stop-Fixture {
    if ($script:fixtureProcess -and -not $script:fixtureProcess.HasExited) {
        Stop-Process -Id $script:fixtureProcess.Id -Force
        $script:fixtureProcess.WaitForExit(5000) | Out-Null
    }
    $script:fixtureProcess = $null
}

function Get-MemberStatus {
    Invoke-RestMethod -Uri "$baseUrl/api/node/member/status" -TimeoutSec 2
}

function Wait-MemberConnected([int]$MinimumReconnects = 0) {
    Wait-Until {
        $member = Get-MemberStatus
        if ($member.connected -and [int]$member.reconnect_total -ge $MinimumReconnects) {
            return $member
        }
        return $null
    } "Android member link connection"
}

function Forget-Membership {
    try {
        Invoke-RestMethod -Uri "$baseUrl/api/node/member/disconnect" -Method Post `
            -ContentType "application/json" -Body '{"forget":true}' -TimeoutSec 3 | Out-Null
    } catch {
    }
}

$devices = & $AdbPath devices
if ($LASTEXITCODE -ne 0 -or -not ($devices -match "\sdevice\s*$")) {
    throw "No authorized Android device is visible to adb."
}

& $AdbPath forward "tcp:$NodeForwardPort" "tcp:8777" | Out-Null
& $AdbPath reverse "tcp:$RootPort" "tcp:$RootPort" | Out-Null
& $AdbPath reverse "tcp:$HubPort" "tcp:$HubPort" | Out-Null

$node = Wait-Until {
    Invoke-RestMethod -Uri "$baseUrl/api/node/status" -TimeoutSec 2
} "Android node readiness"
if (-not $node.ready) {
    throw "Android node is not ready."
}

try {
    Forget-Membership
    Start-Fixture
    $join = Invoke-RestMethod -Uri "$baseUrl/api/node/member/join" -Method Post `
        -ContentType "application/json" `
        -Body (@{root_url = "http://127.0.0.1:$RootPort"; code = $joinCode} | ConvertTo-Json) `
        -TimeoutSec 12
    if (-not $join.ok) {
        throw "Member join failed: $($join | ConvertTo-Json -Compress -Depth 8)"
    }

    $connected = Wait-MemberConnected 1
    $tool = @{
        tool = "subnet_env:set_node_label"
        arguments = @{node_label = "Linked Android Phone"}
    } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Uri "$baseUrl/api/tools/call" -Method Post `
        -ContentType "application/json" -Body $tool -TimeoutSec 5 | Out-Null

    $evidence = Wait-Until {
        $current = Invoke-RestMethod -Uri "http://127.0.0.1:$RootPort/evidence" -TimeoutSec 2
        if ($current.sessions -ge 2 -and $current.yjs_update_total -ge 1 -and $current.inbound_probe_sent) {
            return $current
        }
        return $null
    } "bidirectional Yjs evidence"
    Wait-Until {
        $materialization = Invoke-RestMethod -Uri `
            "$baseUrl/api/node/yjs/webspaces/desktop/materialization/snapshot" -TimeoutSec 2
        if ($materialization.snapshot.runtime.member_hub_probe -eq "received-from-protocol-hub") {
            return $materialization
        }
        return $null
    } "Hub-to-phone Yjs application" | Out-Null

    & $AdbPath shell am force-stop $packageName
    & $AdbPath shell am start -n $activityName --ez start_node true | Out-Null
    Wait-Until {
        $current = Get-MemberStatus
        if ($current.connected -and $current.configured) {
            return $current
        }
        return $null
    } "membership recovery after process restart" | Out-Null

    Stop-Fixture
    $offline = Wait-Until {
        $current = Get-MemberStatus
        if (-not $current.connected -and $current.configured) {
            return $current
        }
        return $null
    } "member-link offline state"
    $local = Invoke-RestMethod -Uri `
        "$baseUrl/api/node/yjs/webspaces/desktop/materialization/snapshot" -TimeoutSec 3
    if (-not $local.materialization.ready) {
        throw "LO materialization became unavailable with the Hub offline."
    }

    Start-Fixture
    $recovered = Wait-MemberConnected 1
    $finalEvidence = Wait-Until {
        $current = Invoke-RestMethod -Uri "http://127.0.0.1:$RootPort/evidence" -TimeoutSec 2
        if ($current.sessions -ge 2) {
            return $current
        }
        return $null
    } "post-outage Hub session"

    [ordered]@{
        ok = $true
        node_id = $node.node_id
        joined_subnet_id = $connected.subnet_id
        initial_reconnect_total = $connected.reconnect_total
        membership_survived_process_restart = $true
        local_ready_while_hub_offline = $local.materialization.ready
        recovered_after_hub_outage = $recovered.connected
        phone_to_hub_yjs = $evidence.yjs_update_total
        hub_to_phone_yjs = $evidence.inbound_probe_sent
        final_hub_sessions = $finalEvidence.sessions
        token_exposed = $false
    } | ConvertTo-Json -Depth 5
} finally {
    Forget-Membership
    Stop-Fixture
    Remove-Item -LiteralPath $fixtureOut, $fixtureErr -Force -ErrorAction SilentlyContinue
}
