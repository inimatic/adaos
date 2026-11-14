# Supervisor runs only in Windows Sandbox. Host mappings contain public fixtures
# and an empty diagnostic output directory, never the checkout or credentials.
$ErrorActionPreference = 'Stop'
$fixture = 'C:\AdaosFixture'
$receiver = 'C:\AdaosReceiver'
$work = 'C:\AdaosConsumer'
$publishedOutput = 'C:\AdaosOutput'
$nodeProcess = $null
$consumerProcess = $null
$outcome = @{ok=$false;environment='windows-sandbox';networking='Disable';time=[DateTime]::UtcNow.ToString('o')}
try {
    foreach ($directory in @($fixture,$receiver,$work)) { New-Item -ItemType Directory -Path $directory | Out-Null }
    Copy-Item -Path 'C:\AdaosInput\*' -Destination $fixture -Recurse
    $releasePath = Join-Path $fixture 'release\release.json'
    $record = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
    $filename = $record.filename
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $fixture ('release\'+$filename))).Hash.ToLower() -ne $record.sha256) { throw 'Guest bundle SHA-256 mismatch' }
    $outcome.bundle_sha256 = $record.sha256
    $outcome.source_revision = $record.source.revision
    $outcome.guest_os = [Environment]::OSVersion.VersionString
    $record.PSObject.Properties.Remove('filename')
    foreach ($entry in @{channel='beta';zone_id='ru';bundle_url=('http://127.0.0.1:8799/bundles/'+$filename)}.GetEnumerator()) {
        $record | Add-Member -NotePropertyName $entry.Key -NotePropertyValue $entry.Value -Force
    }
    New-Item -ItemType Directory -Path (Join-Path $receiver 'root-state') | Out-Null
    [IO.File]::WriteAllText((Join-Path $receiver 'root-state\catalogue.json'), (@{schema='adaos.bootstrap_catalogue.v1';releases=@($record)} | ConvertTo-Json -Depth 20))
    $env:BOOTSTRAP_STAND_DIRECTORY = Join-Path $receiver 'root-state'
    $env:BOOTSTRAP_STAND_ADMIN_TOKEN = [Guid]::NewGuid().ToString('N')
    $env:BOOTSTRAP_STAND_HOST = '127.0.0.1'
    $env:BOOTSTRAP_STAND_PORT = '8799'
    $env:BOOTSTRAP_STAND_BUNDLES = Join-Path $fixture 'release'
    $nodeProcess = Start-Process -FilePath (Join-Path $fixture 'node.exe') -ArgumentList (Join-Path $fixture 'receiver.mjs') -WorkingDirectory $fixture -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $receiver 'stdout.log') -RedirectStandardError (Join-Path $receiver 'stderr.log')
    $ready = $false
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        if ($nodeProcess.HasExited) { throw 'Qualification Root exited before startup' }
        try { $connection = New-Object Net.Sockets.TcpClient('127.0.0.1',8799); $connection.Dispose(); $ready=$true; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Qualification Root startup timed out' }
    Get-ChildItem Env:BOOTSTRAP_STAND_* | Remove-Item
    $password = ConvertTo-SecureString ([Guid]::NewGuid().ToString('N')+'aA1!') -AsPlainText -Force
    $user = New-LocalUser -Name 'AdaosConsumer' -Password $password -PasswordNeverExpires -UserMayNotChangePassword
    $users = Get-LocalGroup -SID 'S-1-5-32-545'
    Add-LocalGroupMember -Group $users -Member $user
    $acl = Get-Acl -LiteralPath $work
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($user.SID,'Modify','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule); Set-Acl -LiteralPath $work -AclObject $acl
    $acl = Get-Acl -LiteralPath $fixture
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($users.SID,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule); Set-Acl -LiteralPath $fixture -AclObject $acl
    $credential = New-Object Management.Automation.PSCredential(('.\'+$user.Name),$password)
    # PowerShell 5.1 can lose ExitCode for Start-Process -Credential when its
    # native handle was never retained. Check a failing child before the long
    # package tests and keep each handle alive until its result is collected.
    $probeProcess = Start-Process -FilePath "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 2; exit 7') -Credential $credential -LoadUserProfile -WindowStyle Hidden -PassThru
    $probeHandle = $probeProcess.Handle
    if (-not $probeProcess.WaitForExit(30000) -or $probeProcess.ExitCode -ne 7) { throw 'Cannot reliably collect a standard-user process exit code' }
    $outcome.exit_code_probe=$probeProcess.ExitCode
    $consumerProcess = Start-Process -FilePath "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $fixture 'consumer.ps1')) -Credential $credential -LoadUserProfile -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $receiver 'consumer-stdout.log') -RedirectStandardError (Join-Path $receiver 'consumer-stderr.log')
    $consumerHandle = $consumerProcess.Handle
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while (-not $consumerProcess.WaitForExit(1000)) {
        if ($watch.Elapsed.TotalSeconds -gt 1500) { throw 'Consumer qualification timed out' }
        $progress = Get-ChildItem -LiteralPath $work -Filter 'progress-*.json' -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if ($progress) {
            Copy-Item -LiteralPath $progress.FullName -Destination (Join-Path $publishedOutput 'progress.json') -Force -ErrorAction SilentlyContinue
        }
    }
    if ($null -eq $consumerProcess.ExitCode -or $consumerProcess.ExitCode -ne 0) { throw "Standard-user consumer returned $($consumerProcess.ExitCode)" }
    $outcome.consumer_exit_code=$consumerProcess.ExitCode
    # Windows PowerShell 5.1 emits a JSON array as one pipeline object. An
    # enclosing @() would nest it and report Count=1 for three successful cases.
    $results = Get-Content -LiteralPath (Join-Path $work 'results.json') -Raw | ConvertFrom-Json
    if ($results.Count -ne 3 -or @($results | Where-Object { -not $_.ok }).Count) { throw 'Incomplete consumer qualification' }
    $attempts = @(Get-ChildItem -LiteralPath (Join-Path $receiver 'root-state\attempts') -Directory)
    if ($attempts.Count -ne 3) { throw 'Expected three durable Root attempts' }
    foreach ($attempt in $attempts) {
        $events = @(Get-ChildItem -LiteralPath $attempt.FullName -Filter 'event-*.json' | ForEach-Object { (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).event })
        if (-not @($events | Where-Object { $_.type -eq 'installation_succeeded' }).Count) { throw 'Durable Root success missing' }
        if (@($events | Where-Object { $_.type -eq 'installation_failed' }).Count) { throw 'Root retained a terminal installation failure' }
    }
    $outcome.ok=$true; $outcome.administrator=$false; $outcome.results=$results; $outcome.root_attempts=$attempts.Count
} catch {
    $outcome.error=$_.Exception.Message
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $publishedOutput 'supervisor-error.log') -Encoding UTF8
} finally {
    foreach ($process in @($consumerProcess,$nodeProcess)) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
    if (Test-Path -LiteralPath $receiver) { Copy-Item -LiteralPath $receiver -Destination (Join-Path $publishedOutput 'receiver') -Recurse -Force }
    if (Test-Path -LiteralPath $work) {
        # Export diagnostics and facts, excluding installed runtimes and payloads.
        $diagnostics = Join-Path $publishedOutput 'consumer'
        New-Item -ItemType Directory -Force -Path $diagnostics | Out-Null
        Get-ChildItem -LiteralPath $work -File | Where-Object { $_.Extension -in @('.json','.log') } | Copy-Item -Destination $diagnostics
        foreach ($scenario in @('uv','pip','uv-failed')) {
            $case = Join-Path $work $scenario
            if (-not (Test-Path -LiteralPath $case)) { continue }
            $target = Join-Path $diagnostics $scenario
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            Get-ChildItem -LiteralPath $case -File | Copy-Item -Destination $target
            if (Test-Path -LiteralPath (Join-Path $case 'outbox')) {
                foreach ($attempt in @(Get-ChildItem -LiteralPath (Join-Path $case 'outbox') -Directory -Filter 'attempt-*')) {
                    $facts = Join-Path $target ('outbox\'+$attempt.Name)
                    New-Item -ItemType Directory -Force -Path $facts | Out-Null
                    Get-ChildItem -LiteralPath $attempt.FullName -File | Where-Object { $_.Extension -in @('.json','.ack') } | Copy-Item -Destination $facts
                }
            }
        }
    }
    $outcome.completed_at=[DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText((Join-Path $publishedOutput 'result.next.json'), ($outcome | ConvertTo-Json -Depth 20))
    Move-Item -LiteralPath (Join-Path $publishedOutput 'result.next.json') -Destination (Join-Path $publishedOutput 'result.json') -Force
}
