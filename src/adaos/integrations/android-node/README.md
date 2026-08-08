# AdaOS Android Node

This module builds the first experimental AdaOS-on-Android APK described by
the [target architecture](../../../../docs/architecture/android-full-node.md).

Current scope:

- Android API 26+, `arm64-v8a` only;
- user-started foreground service;
- embedded CPython 3.11 through Chaquopy;
- stable app-private node identity and runtime marker;
- unauthenticated loopback discovery on `127.0.0.1:8777`;
- restricted CORS/private-network admission for the hosted client;
- browser control `/ws` and Yjs `/yws/desktop` endpoints;
- the AdaOS `y-py` fork as a CPython 3.11 Android arm64 wheel;
- a real `desktop` YDoc with a bounded SQLite snapshot/update YStore;
- the verified, immutable `android_poc_v1` install profile;
- fixed in-process Weather, AdaOS Connect, Notebook, subnet environment, and
  Taiga demo-metrics handlers, with no subprocess or runtime package install;
- fixed UI descriptors for Weather, AdaOS Connect, Notebook, and the Taiga UI
  demo scenario;
- `Open AdaOS` launch into `https://inimatic.com` zone LO.

This is the A0/A1/A2/A3/A4/A5 vertical-slice artifact. It reports `yjs_ready=true`,
renders the packaged desktop through the normal hosted client, calculates
state-vector diffs with native `y-py`, and persists accepted updates in the
app-private SQLite YStore. Weather and host events use `/ws`; Notebook tools use
the existing `/api/tools/call` contract; all visible state returns through Yjs
or bounded WebIO stream events. Member connectivity remains a later gate.

## Build

Install JDK 17 or later, Android SDK platform 37.0, and Build Tools 36.0.0.
Python 3.11 must be available as `py -3.11` on Windows. Then run:

```powershell
.\gradlew.bat :app:assembleDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`.
Pushes which touch this module also run the `Android node APK` workflow. It
executes the host runtime tests, verifies the debug signature, records the
SHA-256 checksum, and uploads the APK as a 14-day workflow artifact.

The checked-in Android wheel is pinned by
`wheels/y_py-0.6.2+adaos.1-android-arm64.provenance.json`. The patched-wheel
workflow rebuilds it on Linux with Rust 1.72.1, NDK r27c, and 16 KB ELF segment
alignment. Its standalone build entry point is `build-y-py-android.sh`.

If a bundled `webui.json` changes, regenerate the immutable Yjs seed first:

```powershell
py -3.11 generate_yjs_seed.py
```

The repository build handoff copies the same file to
`artifacts/android-node/adaos-android-node-0.1.0-poc3-debug.apk`. This is a
debug-signed development artifact, not a Play Store release package.

## Install and smoke-test

Connect an arm64 Android 8+ phone with USB debugging enabled, then run the
repeatable smoke test:

```powershell
.\smoke-device.ps1 -AdbPath "$env:ANDROID_HOME\platform-tools\adb.exe" -VerifyYjsRestart -VerifySkills -OpenBrowser
```

The script installs the APK, starts the foreground service through the visible
Activity, creates an adb loopback forward, and fails unless the runtime is
ready, Yjs is available, and local authentication is disabled.
`-VerifyYjsRestart` also writes through `/yws/desktop`, force-stops the app,
restarts it, and verifies the same Yjs value. `-OpenBrowser` then launches the
hosted client with explicit LO intent. The browser should show the four fixed
apps, two widgets, and a green YJS status without login or a development token.
`-VerifySkills` runs Weather, the AdaOS Connect degraded path, Notebook
create/delete/stream/restart, and the Taiga scenario/event round trip against
the physical device.
