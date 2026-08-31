param(
    [string]$OutputDir = "$PSScriptRoot\.tmp\wheels-armv7",
    [string]$WorkRoot = "$PSScriptRoot\.tmp\y-py-armv7",
    [string]$AndroidSdkRoot = "",
    [string]$NdkVersion = "27.2.12479018",
    [string]$PythonTargetVersion = "3.11.14-0",
    [int]$AndroidApi = 26
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-ChildPath([string]$Candidate, [string]$Root) {
    $candidateFull = Resolve-FullPath $Candidate
    $rootFull = (Resolve-FullPath $Root).TrimEnd('\') + '\'
    if (-not $candidateFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to use path outside ${rootFull}: $candidateFull"
    }
    return $candidateFull
}

$moduleRoot = Resolve-FullPath $PSScriptRoot
$repoRoot = Resolve-FullPath (Join-Path $PSScriptRoot "..\..\..\..")
$work = Assert-ChildPath $WorkRoot $moduleRoot
$out = Assert-ChildPath $OutputDir $moduleRoot
$sdkRootRaw = $AndroidSdkRoot.Trim()
if (-not $sdkRootRaw) {
    $sdkRootRaw = "$env:ANDROID_SDK_ROOT".Trim()
}
if (-not $sdkRootRaw) {
    $sdkRootRaw = "$env:ANDROID_HOME".Trim()
}
if (-not $sdkRootRaw) {
    throw "Android SDK root is required via -AndroidSdkRoot, ANDROID_SDK_ROOT, or ANDROID_HOME."
}
$sdkRoot = Resolve-FullPath $sdkRootRaw

$ndkRoot = Join-Path $sdkRoot "ndk\$NdkVersion"
$toolchain = Join-Path $ndkRoot "toolchains\llvm\prebuilt\windows-x86_64"
$linker = Join-Path $toolchain "bin\armv7a-linux-androideabi$AndroidApi-clang.cmd"
$readelf = Join-Path $toolchain "bin\llvm-readelf.exe"
if (-not (Test-Path -LiteralPath $linker)) {
    throw "Android NDK linker not found: $linker"
}
if (-not (Test-Path -LiteralPath $readelf)) {
    throw "Android NDK readelf not found: $readelf"
}

$targetUrl = "https://repo1.maven.org/maven2/com/chaquo/python/target/$PythonTargetVersion/target-$PythonTargetVersion-armeabi-v7a.zip"
$stdlibUrl = "https://repo1.maven.org/maven2/com/chaquo/python/target/$PythonTargetVersion/target-$PythonTargetVersion-stdlib-pyc.zip"

Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $work "target"), (Join-Path $work "stdlib"), $out | Out-Null

curl.exe --fail --location --retry 3 $targetUrl --output (Join-Path $work "target.zip")
if ($LASTEXITCODE -ne 0) {
    throw "Downloading Chaquopy target failed."
}
curl.exe --fail --location --retry 3 $stdlibUrl --output (Join-Path $work "stdlib.zip")
if ($LASTEXITCODE -ne 0) {
    throw "Downloading Chaquopy stdlib failed."
}
Expand-Archive -LiteralPath (Join-Path $work "target.zip") -DestinationPath (Join-Path $work "target") -Force
Expand-Archive -LiteralPath (Join-Path $work "stdlib.zip") -DestinationPath (Join-Path $work "stdlib") -Force

$sysconfigPyc = Join-Path $work "stdlib\_sysconfigdata__linux_.pyc"
$sysconfigPy = Join-Path $work "target\jniLibs\armeabi-v7a\_sysconfigdata__linux_.py"
$extractSysconfig = @'
import importlib.machinery
import importlib.util
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
loader = importlib.machinery.SourcelessFileLoader("_chaquopy_sysconfig", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
destination.write_text("build_time_vars = " + repr(module.build_time_vars) + "\n", encoding="utf-8")
'@
$extractSysconfigPath = Join-Path $work "extract_sysconfig.py"
Set-Content -LiteralPath $extractSysconfigPath -Value $extractSysconfig -Encoding UTF8
py -3.11 $extractSysconfigPath $sysconfigPyc $sysconfigPy
if ($LASTEXITCODE -ne 0) {
    throw "Extracting Chaquopy sysconfig failed."
}

$env:CARGO_TARGET_ARMV7_LINUX_ANDROIDEABI_LINKER = $linker
$env:CARGO_TARGET_ARMV7_LINUX_ANDROIDEABI_RUSTFLAGS = "-L native=$(Join-Path $work 'target\jniLibs\armeabi-v7a')"
$env:PYO3_CROSS = "1"
$env:PYO3_CROSS_LIB_DIR = Join-Path $work "target\jniLibs\armeabi-v7a"
$env:PYO3_CROSS_PYTHON_VERSION = "3.11"
$env:_PYTHON_SYSCONFIGDATA_NAME = "_sysconfigdata__linux_"
$env:PYTHONPATH = Join-Path $work "target\jniLibs\armeabi-v7a"
$env:CARGO_TARGET_DIR = Join-Path $work "cargo-target"
$env:ANDROID_API_LEVEL = "$AndroidApi"

py -3.11 -m maturin build `
    --release `
    --out $out `
    --manifest-path (Join-Path $repoRoot "vendor\y-py\Cargo.toml") `
    --target armv7-linux-androideabi `
    --interpreter python3.11
if ($LASTEXITCODE -ne 0) {
    throw "maturin build failed."
}

$wheel = Get-ChildItem -LiteralPath $out -Filter "y_py-*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) {
    throw "No y_py wheel was produced in $out"
}

$wheelExtract = Join-Path $work "wheel"
New-Item -ItemType Directory -Force -Path $wheelExtract | Out-Null
tar -xf $wheel.FullName -C $wheelExtract
$native = Get-ChildItem -LiteralPath $wheelExtract -Recurse -Filter "*.so" | Select-Object -First 1
if (-not $native) {
    throw "No native extension found in $($wheel.FullName)"
}

$header = & $readelf -h $native.FullName
if (($header -join "`n") -notmatch "Machine:\s+ARM") {
    throw "Native extension is not ARM: $($header -join "`n")"
}
$dynamic = & $readelf -d $native.FullName
if (($dynamic -join "`n") -notmatch "Shared library: \[libpython3\.11\.so\]") {
    throw "Native extension does not link libpython3.11.so: $($dynamic -join "`n")"
}

Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName | Select-Object Algorithm, Hash, Path
