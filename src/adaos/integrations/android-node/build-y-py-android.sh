#!/usr/bin/env bash
set -euo pipefail

# Reproducible Linux/CI build for the Chaquopy CPython 3.11 arm64 runtime.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
work_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/adaos-y-py-android"
output_dir="${1:-$repo_root/src/adaos/integrations/android-node/wheels}"
python_version="3.11.14-0"
ndk_version="27.2.12479018"
android_api="26"
target_url="https://repo1.maven.org/maven2/com/chaquo/python/target/$python_version/target-$python_version-arm64-v8a.zip"
stdlib_url="https://repo1.maven.org/maven2/com/chaquo/python/target/$python_version/target-$python_version-stdlib-pyc.zip"

: "${ANDROID_SDK_ROOT:?ANDROID_SDK_ROOT must point at the Android SDK}"
ndk_root="${ANDROID_NDK_ROOT:-$ANDROID_SDK_ROOT/ndk/$ndk_version}"
toolchain="$ndk_root/toolchains/llvm/prebuilt/linux-x86_64"
linker="$toolchain/bin/aarch64-linux-android${android_api}-clang"
if [[ ! -x "$linker" ]]; then
  echo "Android NDK linker not found: $linker" >&2
  exit 1
fi

rm -rf "$work_root"
mkdir -p "$work_root/target" "$work_root/stdlib" "$output_dir"
curl --fail --location --retry 3 "$target_url" --output "$work_root/target.zip"
curl --fail --location --retry 3 "$stdlib_url" --output "$work_root/stdlib.zip"
unzip -q "$work_root/target.zip" -d "$work_root/target"
unzip -q "$work_root/stdlib.zip" -d "$work_root/stdlib"

# PyO3 needs a source-form sysconfig module. Chaquopy distributes it as a
# version-matched pyc, so load it with CPython 3.11 and serialize its data.
python3.11 - "$work_root/stdlib/_sysconfigdata__linux_.pyc" \
  "$work_root/target/jniLibs/arm64-v8a/_sysconfigdata__linux_.py" <<'PY'
import importlib.machinery
import importlib.util
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
loader = importlib.machinery.SourcelessFileLoader("_chaquopy_sysconfig", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
destination.write_text(
    "build_time_vars = " + repr(module.build_time_vars) + "\n",
    encoding="utf-8",
)
PY

export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$linker"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS="-L native=$work_root/target/jniLibs/arm64-v8a -C link-arg=-Wl,-z,max-page-size=16384"
export PYO3_CROSS=1
export PYO3_CROSS_LIB_DIR="$work_root/target/jniLibs/arm64-v8a"
export PYO3_CROSS_PYTHON_VERSION="3.11"
export _PYTHON_SYSCONFIGDATA_NAME="_sysconfigdata__linux_"
export PYTHONPATH="$work_root/target/jniLibs/arm64-v8a"

python3.11 -m maturin build \
  --release \
  --out "$output_dir" \
  --manifest-path "$repo_root/vendor/y-py/Cargo.toml" \
  --target aarch64-linux-android \
  --interpreter python3.11

wheel="$output_dir/y_py-0.6.2+adaos.1-cp311-cp311-android_26_arm64_v8a.whl"
test -f "$wheel"
unzip -q "$wheel" 'y_py/*.so' -d "$work_root/wheel"
native="$(find "$work_root/wheel" -name '*.so' -print -quit)"
"$toolchain/bin/llvm-readelf" -h "$native" | grep -q 'Machine:.*AArch64'
"$toolchain/bin/llvm-readelf" -d "$native" | grep -q 'Shared library: \[libpython3.11.so\]'
if "$toolchain/bin/llvm-readelf" -lW "$native" | awk '$1 == "LOAD" && $NF != "0x4000" { exit 1 }'; then
  :
else
  echo "Native extension is not 16 KB page aligned." >&2
  exit 1
fi
sha256sum "$wheel"
