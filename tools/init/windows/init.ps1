#Requires -Version 5.1
# Minimal "download & bootstrap" entrypoint (Windows PowerShell 5.1+).
# Served from GitHub raw:
#   https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1
# Zone arguments are passed through to tools/bootstrap.ps1, for example: -ZoneId ru -Dev

[CmdletBinding(PositionalBinding = $false)]
param(
  [string]$Dest = "$HOME\\adaos",
  [string]$Rev = "rev2026",
  [string]$RepoOwner = "inimatic",
  [string]$RepoName = "adaos",
  [string]$UseGitFrom = "",
  [Alias("NoGit")]
  [switch]$Archive,
  [switch]$Force,
  [Alias("UseWorkspaceRegistryFrom")]
  [string]$WorkspaceRegistryRepo = "",
  [string]$JoinCode = "",
  [string]$Role = "",
  [switch]$Dev,
  [switch]$NoVoice,
  [ValidateSet("auto", "always", "never")]
  [string]$InstallService = "auto",
  [string]$ServeHost = "",
  [int]$ServePort = 0,
  [int]$ControlPort = 0,
  [string]$RootUrl = "",
  [string]$ZoneId = "",
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$BootstrapArgs
)

$ErrorActionPreference = "Stop"

function Write-Info([string]$s) { Write-Host "[*] $s" -ForegroundColor Cyan }
function Write-Ok([string]$s) { Write-Host "[+] $s" -ForegroundColor Green }
function Write-Warn([string]$s) { Write-Host "[!] $s" -ForegroundColor Yellow }
function Have([string]$cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Test-DirectoryHasItems([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  return @((Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue)).Count -gt 0
}
function Test-AdaosSourceTree([string]$Path) {
  return (Test-Path -LiteralPath (Join-Path $Path "tools\\bootstrap.ps1")) -and
         (Test-Path -LiteralPath (Join-Path $Path "pyproject.toml"))
}
function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw ("Command failed with exit code {0}: {1} {2}" -f $LASTEXITCODE, $FilePath, ($Arguments -join " "))
  }
}
function Ensure-Origin([string]$Path, [string]$Url) {
  $currentOrigin = ""
  try { $currentOrigin = (git -C $Path remote get-url origin 2>$null) } catch { $currentOrigin = "" }
  if ([string]::IsNullOrWhiteSpace($currentOrigin)) {
    Invoke-Checked "git" @("-C", $Path, "remote", "add", "origin", $Url)
  }
  elseif ($currentOrigin -ne $Url) {
    Write-Info ("Updating origin URL: {0}" -f $Url)
    Invoke-Checked "git" @("-C", $Path, "remote", "set-url", "origin", $Url)
  }
}
function Ensure-RequiredSubmodules([string]$Path) {
  if (-not (Have "git")) { return }
  if (-not (Test-Path -LiteralPath (Join-Path $Path ".git"))) { return }
  $rasaPath = "src/adaos/integrations/rasa-port"
  Write-Info "Ensuring required submodules..."
  Invoke-Checked "git" @("-C", $Path, "submodule", "sync", "--", $rasaPath)
  Invoke-Checked "git" @("-C", $Path, "submodule", "update", "--init", "--recursive", $rasaPath)
  $rasaFullPath = Join-Path $Path ($rasaPath -replace "/", "\\")
  if ((Test-Path -LiteralPath (Join-Path $rasaFullPath ".git")) -and -not (Test-Path -LiteralPath (Join-Path $rasaFullPath "pyproject.toml"))) {
    Write-Warn "rasa-port submodule worktree is incomplete; restoring from HEAD..."
    Invoke-Checked "git" @("-C", $rasaFullPath, "restore", "--source=HEAD", "--worktree", ".")
    Invoke-Checked "git" @("-C", $rasaFullPath, "restore", "--source=HEAD", "--staged", ".")
  }
}

if (-not $PSVersionTable -or -not $PSVersionTable.PSVersion) {
  throw "Unsupported PowerShell runtime: unable to detect PSVersionTable.PSVersion. Use Windows PowerShell 5.1+ or PowerShell 7+."
}
if ($PSVersionTable.PSVersion.Major -lt 5 -or ($PSVersionTable.PSVersion.Major -eq 5 -and $PSVersionTable.PSVersion.Minor -lt 1)) {
  throw "Unsupported PowerShell version $($PSVersionTable.PSVersion). This installer requires Windows PowerShell 5.1+ or PowerShell 7+."
}

try {
  # Ensure modern TLS for GitHub downloads.
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch { }

if ([string]::IsNullOrWhiteSpace($Dest)) { throw "Dest is empty" }
if ([string]::IsNullOrWhiteSpace($Rev)) { throw "Rev is empty" }
if (-not [string]::IsNullOrWhiteSpace($UseGitFrom)) {
  $UseGitFrom = $UseGitFrom.Trim()
}
if (-not [string]::IsNullOrWhiteSpace($WorkspaceRegistryRepo)) {
  $WorkspaceRegistryRepo = $WorkspaceRegistryRepo.Trim()
}
if ($Archive -and -not [string]::IsNullOrWhiteSpace($UseGitFrom)) {
  throw "-Archive/-NoGit cannot be combined with -UseGitFrom"
}

Write-Info ("Preparing repo at: {0}" -f $Dest)
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

if (-not (Have "git")) {
  Write-Info "git not found; trying to install (best-effort)..."
  try {
    if (Have "winget") {
      winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements | Out-Null
    }
    elseif (Have "choco") {
      choco install git -y | Out-Null
    }
  } catch { }
  if (Have "git") {
    Write-Ok "git installed"
  } else {
    Write-Warn "git is not available; AdaOS will run in archive (no-git) mode for skills/scenarios until you enable git"
  }
}
if (-not (Have "git") -and -not [string]::IsNullOrWhiteSpace($UseGitFrom)) {
  throw "git is required for -UseGitFrom. Install git or run without -UseGitFrom."
}

$cloneUrl = if (-not [string]::IsNullOrWhiteSpace($UseGitFrom)) { $UseGitFrom } else { "https://github.com/$RepoOwner/$RepoName.git" }
$zipUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Rev.zip"
$useArchive = [bool]$Archive -or -not (Have "git")
$tmp = Join-Path $env:TEMP ("adaos_init_{0}" -f [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zipPath = Join-Path $tmp "adaos.zip"

try {
  if (-not $useArchive) {
    if (Test-Path (Join-Path $Dest ".git")) {
      Write-Info "Existing git repo detected; updating..."
      Ensure-Origin $Dest $cloneUrl
      Invoke-Checked "git" @("-C", $Dest, "fetch", "--all", "--prune")
      Invoke-Checked "git" @("-C", $Dest, "checkout", $Rev)
      Invoke-Checked "git" @("-C", $Dest, "pull", "--ff-only")
      try { Invoke-Checked "git" @("-C", $Dest, "branch", "--set-upstream-to=origin/$Rev", $Rev) } catch { }
    }
    elseif (Test-DirectoryHasItems $Dest) {
      if (Test-AdaosSourceTree $Dest) {
        Write-Info "Adopting existing AdaOS source tree into git checkout..."
        Invoke-Checked "git" @("-C", $Dest, "init")
        Ensure-Origin $Dest $cloneUrl
        Invoke-Checked "git" @("-C", $Dest, "fetch", "origin", $Rev)
        Invoke-Checked "git" @("-C", $Dest, "symbolic-ref", "HEAD", "refs/heads/$Rev")
        Invoke-Checked "git" @("-C", $Dest, "reset", "--hard", "origin/$Rev")
        try { Invoke-Checked "git" @("-C", $Dest, "branch", "--set-upstream-to=origin/$Rev", $Rev) } catch { }
      }
      elseif ($Force) {
        Write-Warn ("Removing non-empty destination because -Force was supplied: {0}" -f $Dest)
        Remove-Item -Recurse -Force -LiteralPath $Dest
        Write-Info ("Cloning {0} ({1})..." -f $cloneUrl, $Rev)
        Invoke-Checked "git" @("clone", "-b", $Rev, $cloneUrl, $Dest)
        try { Invoke-Checked "git" @("-C", $Dest, "branch", "--set-upstream-to=origin/$Rev", $Rev) } catch { }
      }
      else {
        throw "Destination is non-empty and is not an AdaOS git checkout: $Dest. Use -Force to replace it, or choose another -Dest."
      }
    }
    else {
      Write-Info ("Cloning {0} ({1})..." -f $cloneUrl, $Rev)
      Invoke-Checked "git" @("clone", "-b", $Rev, $cloneUrl, $Dest)
      try { Invoke-Checked "git" @("-C", $Dest, "branch", "--set-upstream-to=origin/$Rev", $Rev) } catch { }
    }
    Ensure-RequiredSubmodules $Dest
    Write-Ok ("Source ready at: {0}" -f $Dest)
  }
  else {
    if ($Archive) {
      Write-Warn "Archive mode requested; git metadata and submodules will not be available."
    }
    Write-Info ("Downloading source archive: {0}" -f $zipUrl)
    Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $zipPath
    Write-Info "Extracting..."
    Expand-Archive -LiteralPath $zipPath -DestinationPath $tmp -Force

    $extracted = Get-ChildItem -LiteralPath $tmp -Directory | Where-Object { $_.Name -like "$RepoName-*" } | Select-Object -First 1
    if (-not $extracted) { throw "Failed to locate extracted directory in $tmp" }
    $extractedItems = @(Get-ChildItem -LiteralPath $extracted.FullName -Force)
    if ($extractedItems.Count -eq 0) {
      throw "Extracted directory is empty: $($extracted.FullName)"
    }

    if (Test-DirectoryHasItems $Dest) {
      if (-not $Force) {
        throw "Refusing to overwrite non-empty destination in archive mode: $Dest. Use -Force to replace it, or install with git."
      }
      Remove-Item -Recurse -Force -LiteralPath $Dest
    }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    foreach ($item in $extractedItems) {
      Copy-Item -Recurse -Force -LiteralPath $item.FullName -Destination $Dest
    }
    Write-Ok ("Source extracted to: {0}" -f $Dest)
  }
}
finally {
  try { Remove-Item -Recurse -Force -LiteralPath $tmp } catch { }
}

Set-Location -LiteralPath $Dest

if (-not [string]::IsNullOrWhiteSpace($WorkspaceRegistryRepo)) {
  Write-Info ("Workspace registry repo URL: {0}" -f $WorkspaceRegistryRepo)
  $env:ADAOS_WORKSPACE_REGISTRY_REPO = $WorkspaceRegistryRepo
}

$bootstrapPath = Join-Path $Dest "tools\\bootstrap.ps1"
if (-not (Test-Path -LiteralPath $bootstrapPath)) {
  throw "Bootstrap script not found: $bootstrapPath"
}

$bootstrapParams = @{}
if (-not [string]::IsNullOrWhiteSpace($JoinCode)) {
  $bootstrapParams["JoinCode"] = $JoinCode
}
if (-not [string]::IsNullOrWhiteSpace($Role)) {
  $bootstrapParams["Role"] = $Role
}
if ($Dev) {
  $bootstrapParams["Dev"] = $true
}
if ($NoVoice) {
  $bootstrapParams["NoVoice"] = $true
}
if ($PSBoundParameters.ContainsKey("InstallService")) {
  $bootstrapParams["InstallService"] = $InstallService
}
if (-not [string]::IsNullOrWhiteSpace($ServeHost)) {
  $bootstrapParams["ServeHost"] = $ServeHost
}
if ($ServePort -gt 0) {
  $bootstrapParams["ServePort"] = $ServePort
}
if ($ControlPort -gt 0) {
  $bootstrapParams["ControlPort"] = $ControlPort
}
if (-not [string]::IsNullOrWhiteSpace($RootUrl)) {
  $bootstrapParams["RootUrl"] = $RootUrl
}
if (-not [string]::IsNullOrWhiteSpace($ZoneId)) {
  $bootstrapParams["ZoneId"] = $ZoneId
}
if (-not [string]::IsNullOrWhiteSpace($Rev)) {
  $bootstrapParams["Rev"] = $Rev
}

Write-Info "Running bootstrap..."
& $bootstrapPath @bootstrapParams @BootstrapArgs
exit $LASTEXITCODE
