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
- fixed in-process Weather, AdaOS Connect, Browsers, local Voice Assistant with
  a bounded five-agent dialog roster, Notebook, subnet environment, and Taiga
  demo-metrics handlers, with no subprocess or runtime package install;
- always-on offline Rasa NLU exported from the same promoted model used by
  stationary AdaOS, with training kept off-device;
- allowlisted member RPC to canonical `conversation_companions` and AdaOS
  Connect tools, including the Root-configured external LLM, plus
  low-confidence evidence forwarding to the canonical LLM Teacher;
- fixed UI descriptors for Weather, AdaOS Connect, Browsers, Voice Assistant,
  Notebook, and the Taiga UI demo scenario;
- browser-compatible home navigation: `desktop.webspace.go_home` restores the
  complete `web_desktop` materialization, while unsupported control commands
  receive an explicit negative acknowledgement;
- `Open AdaOS` launch into `https://inimatic.com` zone LO.

This is the PoC11 A0-A7 implementation artifact. It reports `yjs_ready=true`,
renders the packaged desktop through the normal hosted client, calculates
state-vector diffs with native `y-py`, and persists accepted updates in the
app-private SQLite YStore. Weather and host events use `/ws`; Notebook tools use
the existing `/api/tools/call` contract; all visible state returns through Yjs
or bounded WebIO stream events. The native `Open AdaOS` action owns LO. AdaOS
Connect separately enrolls this phone with a Root URL and one-time join code,
then delegates remote browser, Telegram, and other-node invitations to the
canonical Hub skill. Browsers projects bounded active control sessions. Voice
uses half-duplex Android Chrome SpeechRecognition and speechSynthesis while
the hosted client is open. Dialog work runs in one bounded background slot so
a slow Hub/LLM response cannot starve control WebSocket keepalives.
`data/dialog` projects AdaOS
Mobile, Арсений, Ника, Мира, and Строитель; the hosted selector switches them
through acknowledged control commands and the choice survives restart. AdaOS
Mobile and Builder remain bounded local implementations. Companion turns
use the canonical Hub skill and external LLM when the authenticated member link
is available, and expose `android_offline_fallback` otherwise. Prompts,
profiles, tools, Teacher code, and LLM credentials are not copied into the APK.
The APK does not package `sounddevice`, background capture, wake-word support,
an LLM, or the full Builder runtime.

## Build

Install JDK 17 or later, Android SDK platform 36, and Build Tools 36.0.0.
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

Before bumping a promoted Rasa model, train it on a stationary development
node and export its inference state into the pinned Android bundle:

```powershell
py -3.11 export_rasa_mobile_bundle.py <promoted-model>.tar.gz app/src/main/python/adaos/android/bundle/rasa_mobile_bundle.json.gz
```

Update the model id, source hash, and bundle hash in
`android_poc_v1.install.json`, then run `tests/test_portable_rasa.py`. Gradle
copies the shared `src/adaos/services/nlu/portable_rasa.py` runtime into the APK;
there is intentionally no separately maintained Android copy.

If a bundled `webui.json` changes, regenerate the immutable Yjs seed first:

```powershell
py -3.11 generate_yjs_seed.py
```

The repository build handoff copies the same file to
`artifacts/android-node/adaos-android-node-0.1.0-poc12-debug.apk`. This is a
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
restarts it, and verifies the same Yjs value. Its verifier accepts YWS messages
up to the runtime's bounded 4 MiB message contract, including documents whose
retained CRDT history exceeds the WebSocket library's 1 MiB default.
`-OpenBrowser` then launches the hosted client with explicit LO intent. The
browser should show the seven fixed apps, two widgets, and a green YJS status
without login or a development token.
`-VerifySkills` runs Weather offline/recovery, AdaOS Connect member state,
Browsers registration, the dialog roster/agent/channel paths, a local Voice
Assistant turn, Notebook
create/delete/stream/restart, and the Taiga scenario/event round trip against
the physical device.

The YStore structurally compacts over-limit retained history on startup. Its
generation is announced during browser authorization and included in the
physical YWS room name, isolating old browser BroadcastChannels from the new
document after compaction. Inbound client updates are limited to 512 KiB.

To verify the outbound membership protocol, persistence, outage behavior, and
bidirectional Yjs flow on a connected phone, run:

```powershell
.\verify-member-link-device.ps1 -AdbPath "$env:ANDROID_HOME\platform-tools\adb.exe"
```

This starts a protocol-compatible Root/Hub fixture on the workstation, joins
through a one-time code, restarts the Android process, interrupts the hub, and
checks recovery. It forgets the temporary membership when finished. Passing
this fixture is protocol evidence; the A6 product gate still requires a run
against an existing deployed AdaOS subnet.

## Lifecycle and resource gate

PoC8 does not request `largeHeap`. `/api/node/status` exposes a psutil-free
procfs PSS/RSS sampler and the declared bounds for the loopback server, YStore,
member link, Notebook projection/content, and idempotency results. The current
limits are 32 request threads with a backlog of 16, 64 YStore owner tasks, 128
member-link messages, 256 Notebook notes with 32 projected at once and 16,384
characters per note, and 256 idempotency results. The existing Yjs update,
journal, and snapshot limits remain independent bounds.

Run the reproducible physical lifecycle gate with:

```powershell
.\verify-lifecycle-device.ps1 -AdbPath "$env:ANDROID_HOME\platform-tools\adb.exe"
```

Its default duration is 30 minutes. It installs the APK; records APK,
descriptor, device, Chrome, page-size, and memory identities; recreates the
Activity while the service stays ready; tests screen, browser, Wi-Fi, and WAN
transitions; samples PSS/RSS; force-stops and verifies Yjs/Notebook/install and
membership state; then exercises the same start/stop methods as the visible UI
through debug-only intent actions. Evidence is written under
`app/build/reports/android-lifecycle-evidence.json`. A passing high-memory
phone is useful upper-device evidence, but it does not close the separate
physical 2 GB gate.

The final full PoC6 run on an API 36 Samsung SM-F721N lasted 1,805 seconds.
Across 169 steady samples Android total PSS stayed between 124,239 and 126,307
KiB, all bounded queue rejection/drop counters remained zero, and every
lifecycle/persistence check passed. The tested APK SHA-256 is
`bb4abb4b965d7058b42a9c9a9b720c51512dae9014575a978e4408acc6ab41f6`.
The listener is closed before accepted request threads drain during shutdown,
so concurrent status polling observes a safe stopped state without a Python
traceback.

The PoC7 debug APK is 22,464,755 bytes with SHA-256
`0e8cb7a1f08d31d09607ef275982c17ce9483a1718bd6cd38b21ccf769488c3c`.
Installed over the PoC6 data on the same phone, it passed the Yjs restart and
fixed-skill smoke, projected one live Chrome endpoint, completed a local
Voice Assistant turn, and kept the browser materialization ready. Chrome 149
entered SpeechRecognition listening after the normal site microphone prompt;
the rendered assistant reply exercised browser speechSynthesis.

The PoC8 debug APK is 22,464,755 bytes with SHA-256
`b338e718923f36a501ff26231329a0234689cd20b0f1f9e52560e086e21b967d`.
On the same Samsung it passed Yjs/Notebook restart and the complete fixed-skill
smoke, including selection of Ника, the Строитель channel, addressed Арсений,
and selected-agent persistence. The deployed client modal showed all five
agents in its selector; materialization remained ready and Logcat contained no
matching fatal exception, traceback, or unsupported dialog command.

The PoC9 debug APK is 22,927,600 bytes with SHA-256
`37b2960b07af51cc387072451886c17f278e5cf95a96ebcde15e76f1dba21e2b`.
On the same Samsung it loaded the promoted Rasa 3.6.21 model with 27 intents,
reported `rasa/always` and `training=off_device`, and classified `какая погода
в Москве` as `weather.current` at confidence 0.8829258 without a Hub. The
connected run routed an Арсений turn through the canonical companion skill and
Root external LLM (`hub_skill_llm`, `used_llm=true`) and admitted a
low-confidence turn to the canonical LLM Teacher. The Teacher's optional MCP
evidence timed out in that diagnostic run, so final Teacher ledger completion
remains an explicit follow-up rather than claimed evidence.
