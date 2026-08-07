# AdaOS Android Node

This module builds the first experimental AdaOS-on-Android APK described by
the [target architecture](../../../../docs/architecture/android-full-node.md).

Current scope:

- Android API 26+, `arm64-v8a` only;
- user-started foreground service;
- embedded CPython 3.11 through Chaquopy;
- stable app-private node identity and runtime marker;
- unauthenticated loopback discovery on `127.0.0.1:8777`;
- `Open AdaOS` launch into `https://inimatic.com` zone LO.

This is the A0/A2 sentinel artifact. It intentionally reports
`yjs_ready=false` and `skills_ready=false`; Android `y-py`, YWS, the immutable
skill bundle, and member connectivity remain later roadmap gates.

## Build

Install JDK 17 or later, Android SDK platform 37.0, and Build Tools 36.0.0.
Python 3.11 must be available as `py -3.11` on Windows. Then run:

```powershell
.\gradlew.bat :app:assembleDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`.

The repository build handoff copies the same file to
`artifacts/android-node/adaos-android-node-0.1.0-poc1-debug.apk`. This is a
debug-signed development artifact, not a Play Store release package.

## Install and smoke-test

Connect an arm64 Android 8+ phone with USB debugging enabled, then run:

```powershell
adb install -r artifacts/android-node/adaos-android-node-0.1.0-poc1-debug.apk
adb shell am start -n dev.adaos.androidnode/.MainActivity
```

Tap `Start node`, wait for `READY`, and check the loopback endpoint from the
phone at `http://127.0.0.1:8777/api/node/status`. `Open AdaOS` launches the
hosted client with explicit LO intent. The current sentinel is discoverable by
the client, but does not yet provide `/ws` or `/yws/desktop`.
