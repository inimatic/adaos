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
- a generated, packaged Yjs `web_desktop` seed plus a bounded persistent
  browser-update journal;
- fixed UI descriptors for Weather, AdaOS Connect, Notebook, and the Taiga UI
  demo scenario;
- `Open AdaOS` launch into `https://inimatic.com` zone LO.

This is the A0/A2/A4 vertical-slice artifact. It reports `yjs_ready=true` and
renders the packaged desktop through the normal hosted client. It does not yet
embed the native Android `y-py` wheel: the immutable seed is generated with
host `y-py`, and Android replays it plus opaque Yjs updates. Skill execution,
native YStore semantics, and member connectivity remain later roadmap gates.

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

If a bundled `webui.json` changes, regenerate the immutable Yjs seed first:

```powershell
py -3.11 generate_yjs_seed.py
```

The repository build handoff copies the same file to
`artifacts/android-node/adaos-android-node-0.1.0-poc2-debug.apk`. This is a
debug-signed development artifact, not a Play Store release package.

## Install and smoke-test

Connect an arm64 Android 8+ phone with USB debugging enabled, then run the
repeatable smoke test:

```powershell
.\smoke-device.ps1 -AdbPath "$env:ANDROID_HOME\platform-tools\adb.exe" -OpenBrowser
```

The script installs the APK, starts the foreground service through the visible
Activity, creates an adb loopback forward, and fails unless the runtime is
ready, Yjs is available, and local authentication is disabled. `-OpenBrowser`
then launches the hosted client with explicit LO intent. The browser should
show the four fixed apps, two widgets, and a green YJS status without login or
a development token.
