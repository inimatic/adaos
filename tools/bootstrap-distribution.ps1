#Requires -Version 5.1
# Standalone regional Windows installer; runs before Python/UV are available.
[CmdletBinding()]
param(
    [string]$Dest = "$HOME/adaos",
    [string]$DistributionRoot = '',
    [string]$ZoneId = 'global',
    [string]$Channel = 'stable',
    [string]$Release = 'current',
    [string]$Rev = 'main',
    [string]$SourceDir = '',
    [switch]$WithoutUv,
    [switch]$RuntimeOnly,
    [string]$UvBinary = '',
    [string[]]$ConfigureArgs = @()
)
$ErrorActionPreference = 'Stop'
$env:PYTHONNOUSERSITE = '1'
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
if ($env:ADAOS_BOOTSTRAP_WITHOUT_UV -eq '1') { $WithoutUv = $true }
$ZoneId = $ZoneId.Trim().ToLower()
if ($Release -eq 'current' -and $Rev -ne 'main') { $Release = $Rev }
foreach ($value in @($ZoneId,$Channel,$Release)) {
    if ($value -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$') { throw 'Invalid distribution identity' }
}
if (-not $DistributionRoot) { $DistributionRoot = $env:ADAOS_BOOTSTRAP_ROOT_URL }
if (-not $DistributionRoot) { $DistributionRoot = if ($ZoneId -eq 'ru') {'https://ru.api.inimatic.com'} else {'https://api.inimatic.com'} }
$DistributionRoot = $DistributionRoot.TrimEnd('/')
function Assert-Url([string]$Value) {
    $uri = [Uri]$Value
    if (-not $uri.IsAbsoluteUri -or $uri.UserInfo -or $uri.Fragment -or ($uri.Scheme -ne 'https' -and -not ($env:ADAOS_BOOTSTRAP_ALLOW_HTTP_TEST -eq '1' -and $uri.Scheme -eq 'http'))) { throw 'Distribution requires an admitted HTTPS URL' }
}
Assert-Url $DistributionRoot
$rootOrigin = [Uri]$DistributionRoot
if ($rootOrigin.Query -or $rootOrigin.AbsolutePath -notin @('','/')) { throw 'Distribution Root must be a bare origin' }
$Dest = [IO.Path]::GetFullPath($Dest)
$state = $env:ADAOS_BOOTSTRAP_STATE_DIR
if (-not $state) { $state = Join-Path $HOME '.local/state/adaos/bootstrap' }
$state = [IO.Path]::GetFullPath($state)
New-Item -ItemType Directory -Force -Path $state | Out-Null
if ((Get-Item -LiteralPath $state).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing reparse-point diagnostic storage' }
if (@(Get-ChildItem -LiteralPath $state -Directory -Filter 'attempt-*').Count -ge 128) { throw 'Diagnostic outbox reached 128 attempts; preserve pending records before retrying' }
$env:ADAOS_BOOTSTRAP_STATE_DIR = $state
$identity = [Guid]::NewGuid().ToString()
$attempt = Join-Path $state ('attempt-' + $identity)
New-Item -ItemType Directory -Path $attempt | Out-Null
$script:stage = 'resolve'; $script:route = 'standard'

function Write-Durable([string]$Path,[object]$Value) {
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($Value | ConvertTo-Json -Depth 12 -Compress))
    $next = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.candidate'
    $stream = New-Object IO.FileStream($next,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    if ([IO.File]::Exists($Path)) { [IO.File]::Replace($next,$Path,$null) } else { [IO.File]::Move($next,$Path) }
}
function Add-Event([string]$Kind,[string]$Reason,[int]$Status = 0) {
    $eventId = [Guid]::NewGuid().ToString()
    Write-Durable (Join-Path $attempt ('event-' + $eventId + '.json')) @{
        attempt_id=$identity;event_id=$eventId;type=$Kind;stage=$script:stage;reason=$Reason;route=$script:route;status=[string]$Status
    }
}
function Post-Fact([string]$Root,[string]$Endpoint,[string]$File,[int]$Timeout = 6000) {
    Assert-Url $Root
    $request = [Net.HttpWebRequest]::Create($Root + '/v1/bootstrap/' + $Endpoint)
    $request.Method = 'POST'; $request.ContentType = 'application/json'; $request.Timeout = $Timeout; $request.ReadWriteTimeout = $Timeout
    $request.AllowAutoRedirect = $false
    $bytes = [IO.File]::ReadAllBytes($File)
    if ($bytes.Length -gt 16384) { throw 'Diagnostic fact exceeds budget' }
    $request.ContentLength = $bytes.Length
    $stream = $request.GetRequestStream()
    try { $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
    $response = $request.GetResponse()
    try {
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        try { $text = $reader.ReadToEnd() } finally { $reader.Dispose() }
        if ($text.Length -gt 16384) { throw 'Diagnostic receipt exceeds budget' }
        return @{Body=($text | ConvertFrom-Json);Headers=$response.Headers}
    } finally { $response.Dispose() }
}
function Flush-Reports {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $failed = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($directory in @(Get-ChildItem -LiteralPath $state -Directory -Filter 'attempt-*' | Sort-Object Name | Select-Object -First 128)) {
        if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $root = ''
        try {
            $root = [IO.File]::ReadAllText((Join-Path $directory.FullName 'root-url')).Trim()
            $files = @((Join-Path $directory.FullName 'request.json')) + @(Get-ChildItem -LiteralPath $directory.FullName -File -Filter 'event-*.json' | Sort-Object Name | Select-Object -ExpandProperty FullName)
            foreach ($file in $files) {
                $ack = [IO.Path]::ChangeExtension($file,'.ack')
                if (Test-Path -LiteralPath $ack) { continue }
                if ($clock.Elapsed.TotalSeconds -ge 10 -or $failed.Contains($root)) { throw 'Diagnostic delivery budget exhausted' }
                $fact = [IO.File]::ReadAllText($file) | ConvertFrom-Json
                $endpoint = if ([IO.Path]::GetFileName($file) -eq 'request.json') {'resolve'} else {'events'}
                $receipt = (Post-Fact $root $endpoint $file ([Math]::Max(100,[Math]::Min(5000,10000-$clock.ElapsedMilliseconds)))).Body
                if ($receipt.durable -ne $true -or $receipt.attempt_id -ne $fact.attempt_id -or ($endpoint -eq 'events' -and $receipt.event_id -ne $fact.event_id)) { throw 'Root has not acknowledged this durable fact' }
                Write-Durable $ack @{durable=$true}
            }
        } catch { if ($root) { [void]$failed.Add($root) } }
    }
}
function Fetch-Verified([string]$Url,[string]$Path,[string]$Sha256) {
    Assert-Url $Url
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.Timeout = 15000; $request.ReadWriteTimeout = 600000
    $response = $request.GetResponse()
    try {
        if ($response.ContentLength -gt 2147483648) { throw 'Distribution object exceeds budget' }
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Create($Path + '.partial')
        try {
            $buffer = New-Object byte[] 65536; [long]$total = 0
            while (($count = $inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                $total += $count
                if ($total -gt 2147483648) { throw 'Distribution object exceeds budget' }
                $outputStream.Write($buffer,0,$count)
            }
            $outputStream.Flush($true)
        } finally { $outputStream.Dispose();$inputStream.Dispose() }
    } finally { $response.Dispose() }
    $script:stage = 'verify'
    if ($Sha256 -and (Get-FileHash -Algorithm SHA256 -LiteralPath ($Path + '.partial')).Hash.ToLower() -ne $Sha256) { throw 'Distribution object SHA256 mismatch' }
    Move-Item -LiteralPath ($Path + '.partial') -Destination $Path
}
function Extract-Tar([string]$Archive,[string]$Directory) {
    if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { throw 'Windows tar.exe is required to extract the managed runtime' }
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    & tar.exe -xzf $Archive -C $Directory
    if ($LASTEXITCODE -ne 0) { throw 'Distribution extraction failed' }
}

[IO.File]::WriteAllText((Join-Path $attempt 'root-url'),$DistributionRoot,(New-Object Text.UTF8Encoding($false)))
$rootFile = [IO.File]::Open((Join-Path $attempt 'root-url'),[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $rootFile.Flush($true) } finally { $rootFile.Dispose() }
$arch = if ($env:PROCESSOR_ARCHITEW6432) {$env:PROCESSOR_ARCHITEW6432} else {$env:PROCESSOR_ARCHITECTURE}
$arch = switch ($arch.ToLower()) { 'amd64' {'x86_64'} 'arm64' {'aarch64'} default {$arch.ToLower()} }
$policy = if ($WithoutUv) {'disabled'} else {'auto'}
$requestFile = Join-Path $attempt 'request.json'
Write-Durable $requestFile @{attempt_id=$identity;installer_version='1';zone_id=$ZoneId;channel=$Channel;requested_release=$Release;uv_policy=$policy;
    profile=@{os='windows';distribution='windows';distribution_version=[Environment]::OSVersion.Version.ToString();arch=$arch;libc='none';libc_version='0.0';python='3.11';features=@('core')}}
$exitCode = 1
try {
    $status = 'unavailable'; $resolution = $null
    try {
        $resolution = Post-Fact $DistributionRoot 'resolve' $requestFile
        if ($resolution.Body.durable -eq $true -and $resolution.Body.attempt_id -eq $identity) {
            Write-Durable (Join-Path $attempt 'request.ack') @{durable=$true}
            $status = $resolution.Headers['X-AdaOS-Distribution']
        }
    } catch { Write-Host 'Distribution Root is unavailable; the configuration request remains in the durable outbox.' }
    if (Test-Path -LiteralPath (Join-Path $Dest '.git')) { throw 'Prepared distribution cannot replace a Git checkout; select a fresh destination' }
    $python = ''; $kit = ''; $pythonHome = ''; $uv = $UvBinary
    if ($status -eq 'ready') {
        $script:route = 'bundle';Add-Event 'distribution_selected' 'compatible_bundle'
        $script:stage = 'download'
        $sha = $resolution.Headers['X-AdaOS-Bundle-Sha256']
        if ($sha -notmatch '^[a-f0-9]{64}$') { throw 'Invalid distribution digest' }
        $bundle = Join-Path $attempt 'bundle.tar.gz'
        Fetch-Verified $resolution.Headers['X-AdaOS-Bundle-Url'] $bundle $sha
        $script:stage = 'extract';$kit = Join-Path $attempt 'kit'
        Extract-Tar $bundle $kit
        $python = Join-Path $kit 'runtime/python/python.exe'
        $SourceDir = Join-Path $kit 'source'
    } else {
        if ($status -eq 'distribution_missing') { Add-Event 'distribution_missing' 'compatible_bundle_missing' }
        Add-Event 'fallback_started' $(if ($status -eq 'distribution_missing') {'configuration_unavailable'} else {'root_unavailable'})
        if ($arch -ne 'x86_64') { throw 'Standard managed Windows runtime is not prepared for this architecture; configuration request retained' }
        Write-Host 'Prepared distribution unavailable; using a managed Python and standard downloads.'
        $script:stage = 'python';$runtime = Join-Path $attempt 'runtime'
        New-Item -ItemType Directory -Path $runtime | Out-Null
        if (-not $WithoutUv -and -not $uv) {
            try {
                $uvArchive = Join-Path $attempt 'uv.zip'
                Fetch-Verified 'https://github.com/astral-sh/uv/releases/download/0.13.7/uv-x86_64-pc-windows-msvc.zip' $uvArchive '0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021'
                Expand-Archive -LiteralPath $uvArchive -DestinationPath $runtime
                $uv = (Get-ChildItem -LiteralPath $runtime -Recurse -Filter uv.exe -File | Select-Object -First 1).FullName
            } catch { Add-Event 'stage_failed' 'uv_download_failed' 1 }
        }
        $pythonArchive = Join-Path $attempt 'python.tar.gz'
        Fetch-Verified 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.11.18%2B20261110-x86_64-pc-windows-msvc-install_only.tar.gz' $pythonArchive '5ca7e23bbcc8933a75f4ae9c3a5e5d1ffae5fd143aec9a9d77cf5124cc6e0eef'
        Extract-Tar $pythonArchive $runtime
        $pythonHome = Join-Path $runtime 'python';$python = Join-Path $pythonHome 'python.exe'
        if (-not $SourceDir) {
            if ($Rev -notmatch '^[A-Za-z0-9._/-]+$' -or $Rev.Contains('..') -or ($Release -ne 'current' -and $Release -ne $Rev)) { throw 'Exact missing release requires its source revision' }
            $archive = Join-Path $attempt 'source.zip'
            Fetch-Verified ('https://github.com/inimatic/adaos/archive/' + $Rev + '.zip') $archive ''
            $unpacked = Join-Path $attempt 'source';Expand-Archive -LiteralPath $archive -DestinationPath $unpacked
            $SourceDir = (Get-ChildItem -LiteralPath $unpacked -Directory | Select-Object -First 1).FullName
        }
    }
    $script:stage = 'python'
    $arguments = @((Join-Path $SourceDir 'tools/bootstrap_distribution.py'),'--source',$SourceDir,'--attempt',$attempt,'--dest',$Dest)
    if ($kit) { $arguments += @('--kit',$kit) } else { $arguments += @('--python-home',$pythonHome) }
    if ($uv) { $arguments += @('--uv',$uv) }
    if ($WithoutUv) { $arguments += '--without-uv' }
    if ($RuntimeOnly) { $arguments += '--runtime-only' }
    $arguments += @('--') + $ConfigureArgs
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Managed runtime installation failed; see retained stage diagnostics' }
    $exitCode = 0
} catch {
    Add-Event 'installation_failed' 'stage_execution_failed' 1
    Write-Host ('Bootstrap failed at ' + $script:stage + '. Diagnostic outbox: ' + $attempt)
    Write-Host $_.Exception.Message
} finally {
    Flush-Reports
    if ($exitCode -eq 0) {
        foreach ($name in @('kit','bundle.tar.gz','runtime','python.tar.gz','uv.zip','source','source.zip')) {
            $path = [IO.Path]::GetFullPath((Join-Path $attempt $name))
            if (-not $path.StartsWith($attempt + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe distribution cleanup path' }
            if (Test-Path -LiteralPath $path) {
                if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing reparse-point cleanup' }
                Remove-Item -LiteralPath $path -Recurse -Force
            }
        }
    }
}
exit $exitCode
