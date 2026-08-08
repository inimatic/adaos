# AdaOS Android Full Node Roadmap

Status: active domain roadmap for an experimental proof of concept. The first
A0/A1/A2/A3/A4/A5 vertical slice is running on a physical Android 16 arm64
phone. It renders `web_desktop` from native `y-py` and executes the fixed skill
profile in-process. Member connectivity and the 2 GB device gate remain open.

Architecture owner: [AdaOS Android Full Node](android-full-node.md).

This roadmap chooses the smallest sequence which can put a real AdaOS runtime
on a physical phone. It does not begin by porting the full desktop installation
or reproducing supervisor behavior. Every phase must leave a runnable artifact
and answer one uncertain question before the next phase expands scope.

## Proof Goal

The PoC is successful when one arm64 phone with 2 GB of RAM and Android 8 or
later can:

1. start AdaOS from a user-visible foreground service;
2. persist and reopen a real `y-py` document;
3. expose the local runtime on `127.0.0.1:8777`;
4. let `https://inimatic.com` discover and use zone LO without AdaOS login;
5. render the bundled `web_desktop` from `/yws/desktop`;
6. run the fixed in-process skill set and show its Yjs/stream outputs;
7. switch to the Taiga UI scenario and back;
8. join a hub as a member and reconnect after a transient network loss;
9. survive Activity recreation and a controlled runtime restart without losing
   Yjs or Notebook state;
10. remain within the initial memory gates without `largeHeap`.

Production distribution, arbitrary skill installation, media, phone-as-hub,
and continuous unattended daemon operation are outside this proof.

## Delivery Rules

- `[must]` blocks the proof gate of the current phase.
- `[should]` is required before repeating the experiment across several
  devices.
- `[could]` is useful evidence but does not block the PoC.
- `[deferred]` is intentionally excluded until the named prerequisite exists.

The fastest path is serial at the proof-gate level. Work within a phase may run
in parallel, but later feature work must not hide a failed native-runtime or
Yjs foundation.

## Phase A0: APK and Embedded-Python Sentinel

Outcome: the smallest debug APK proves that the selected Android embedding
toolchain works on one physical arm64 phone.

- [x] `[must]` Create a separate native Android application module for the full
  node; do not modify or fork the ReDevice application.
- [x] `[must]` Configure `minSdk 26`, `targetSdk 36`, `compileSdk 37`, and the
  single `arm64-v8a` ABI.
- [x] `[must]` Embed CPython 3.11 with Chaquopy behind a small `PythonHost`
  interface.
- [x] `[must]` Start Python from a visible Activity action and return a
  structured version/result payload to Kotlin.
- [x] `[must]` give Python an explicit app-private data directory and prove a
  write/read/restart round trip there.
- [x] `[must]` Produce an installable debug APK from CI or a documented local
  build command.
- [x] `[should]` Upload the signed debug APK and checksum from a path-filtered
  Android CI workflow and keep a repeatable physical-device smoke command.
- [ ] `[should]` Record APK size, install time, Python cold-start time, ABI,
  Python version, device model, Android version, and page size.

Gate A0:

- the APK installs and launches on a physical API 26+ arm64 device;
- CPython reports its version and imports one bundled pure-Python AdaOS module;
- a file written under the app-private root survives application restart;
- no Kivy, Cordova WebView, ReDevice runtime, external terminal, or Termux is
  required.

Stop condition: do not port the API, skills, or member link while this gate is
red.

## Phase A1: Android `y-py` and Persistence Spike

Outcome: the custom CRDT foundation works before the rest of AdaOS is packaged.

- [x] `[must]` Add an Android arm64 build target to the AdaOS `y-py` fork.
- [x] `[must]` Produce a CPython 3.11 wheel accepted by the Android embedder.
- [x] `[must]` Verify native libraries for Android linker compatibility and 16
  KB page-size alignment.
- [x] `[must]` Import `y_py` on the phone and create a YDoc.
- [x] `[must]` Write a deterministic map update, encode it, apply it to a
  second document, and compare state.
- [x] `[must]` persist encoded state under the app-private directory and reload
  it after process restart.
- [x] `[must]` exercise the Android AdaOS YStore with the same document rather than
  stopping at a direct `y_py` unit test.
- [x] `[should]` publish the wheel with version, hash, source revision, Android
  API floor, ABI, Python ABI, and native-page alignment metadata.
- [x] `[should]` add the Android wheel build to the existing patched-wheel
  release workflow.

Gate A1:

- the patched `y-py` import and CRDT round trip pass on the physical device;
- the AdaOS YStore reopens the persisted update after a forced process stop;
- no state corruption or native crash appears in Logcat.

Stop condition: if this gate fails, keep work in the wheel/build layer. Do not
replace Yjs with a mock or a second mobile-only state model.

Physical evidence (2026-08-08): `y_py 0.6.2+adaos.1` loaded under Chaquopy
CPython 3.11.14 on a Samsung SM-F721N (API 36, arm64-v8a). The device accepted
a `/yws/desktop` update, was force-stopped, restarted, and returned the same
marker from `android-yjs.sqlite3`. The wheel's AArch64 ELF depends only on
`libpython3.11.so`, `libdl.so`, and `libc.so`, with 16 KB LOAD alignment.

## Phase A2: First Runnable AdaOS Kernel

Outcome: a foreground service starts a minimal real AdaOS runtime and local
status endpoint. This is the first runnable AdaOS-on-phone draft.

- [ ] `[must]` Add `ADAOS_RUNTIME_PROFILE=android_poc` and a typed runtime
  capability description.
- [x] `[must]` Create a non-exported foreground `NodeService` with explicit
  `Start node` and `Stop node` actions and a persistent notification.
- [ ] `[must]` Run one Python asyncio loop owned by the service.
- [ ] `[must]` initialize `AgentContext`, the local event bus, SQLite paths,
  YStore, and the canonical `desktop` webspace.
- [ ] `[must]` start the local ASGI server programmatically on
  `127.0.0.1:8777`; do not use a subprocess or reload worker.
- [ ] `[must]` expose `GET /api/ping` and authenticated-compatibility
  `GET /api/node/status` using the existing `dev-local-token` protocol path.
- [ ] `[must]` make the status payload identify the runtime profile, member
  role, build, Python version, and local readiness without exposing secrets.
- [ ] `[must]` disable supervisor, sidecar, autostart subprocesses, media,
  audio, WebRTC, service skills, NLU, Builder, and update workers before their
  imports can start background work.
- [ ] `[must]` implement ordered stop: close API, disconnect optional upstream,
  drain runtime, flush storage, and stop the loop.
- [ ] `[should]` keep Activity recreation independent from service lifetime.
- [ ] `[should]` export bounded logs and the last structured startup failure.

Gate A2:

- `Start node` reaches `ready` and shows the foreground notification;
- an on-device browser can read `/api/node/status` from loopback;
- `Stop node` releases port 8777 and exits without a leaked Python thread;
- three start/stop cycles preserve the YDoc and do not require force-stop or
  reinstall.

This gate intentionally does not require the hosted client, skills, or hub
membership. It answers whether the real runtime kernel can live under Android
lifecycle at all.

## Phase A3: Reproducible Android Content Bundle

Outcome: the phone materializes one fixed, offline-installable webspace and
skill set without git, `pip`, venvs, or subprocess preparation.

- [x] `[must]` define the versioned `android_poc_v1` install descriptor with an
  exact version and hash for every scenario, skill, Python package, and native
  wheel.
- [x] `[must]` include `web_desktop` as the `desktop` home scenario.
- [x] `[must]` include `taiga_ui_demo_scenario` as an alternate scenario.
- [x] `[must]` include these in-process skills:
  `web_desktop_skill`, `subnet_env`, `weather_skill`, `adaos_connect`,
  `notebook_skill`, and `demo_metrics_skill`.
- [ ] `[must]` convert the `web_desktop` manifest so only
  `web_desktop_skill` is required and greeting, device pairing, and voice
  helpers are optional.
- [ ] `[must]` align the Taiga scenario version declared in `scenario.yaml` and
  `scenario.json` before locking the bundle.
- [x] `[must]` add an Android packaged-runtime path to skill installation which
  verifies and activates bundled in-process code without `prepare_runtime`,
  venv creation, git, shell, or `pip`.
- [x] `[must]` reject activation of a skill not present in the descriptor.
- [x] `[must]` seed `desktop`, rebuild its effective projection, and verify
  `ui.application`, `data.catalog`, and `data.installed` are present.
- [ ] `[must]` add an Android dependency lock which excludes desktop-only
  packages and records the transitive native closure.
- [ ] `[should]` make first-run bundle materialization transactional and
  repeatable after an interrupted install.
- [x] `[should]` expose installed descriptor id, versions, and hashes in local
  diagnostics.

Gate A3:

- a fresh offline installation seeds `desktop` from packaged content;
- all selected skills load in-process and unsupported skills stay inactive;
- a second launch reuses verified materialized content without reinstalling it;
- no runtime command invokes git, `pip`, a virtual environment, or a child
  process.

Physical evidence (2026-08-08): PoC3 verified the descriptor on startup,
executed Weather against Open-Meteo, emitted Notebook and demo-metrics stream
events, rejected an undeclared skill, switched Taiga UI and back, and recovered
Notebook/Yjs state after forced process restarts on the Samsung SM-F721N.

## Phase A4: Hosted Browser and LO Vertical Slice

Outcome: the normal hosted AdaOS client becomes the phone's UI and renders real
local Yjs state.

- [x] `[must]` expose the existing browser-required HTTP, `/ws`, and
  `/yws/<webspace>` routes from the Android runtime.
- [x] `[must]` keep the listener on `127.0.0.1` and restrict HTTP CORS and
  WebSocket Origin admission to `https://inimatic.com` plus explicit debug
  origins.
- [x] `[must]` add `Open AdaOS` to launch
  `https://inimatic.com/?zone=lo&try_local_hub=1`.
- [ ] `[must]` treat the Chrome Local Network Access prompt as the only
  first-use browser approval, not as AdaOS login or pairing.
- [x] `[must]` make explicit LO intent override a remembered remote owner
  session without deleting the remote session.
- [x] `[must]` advertise the no-auth local listener and omit
  `dev-local-token`; display no local login or pairing UI.
- [x] `[must]` connect to `/yws/desktop`, complete first sync, and render
  `web_desktop` from the local document.
- [x] `[must]` connect `/ws` and prove one browser action and one live stream
  event.
- [x] `[must]` distinguish local API, local Yjs, and remote member-link status
  in diagnostics.
- [ ] `[should]` rename new client diagnostics from `local hub` to
  `local runtime` while preserving compatibility keys.
- [ ] `[should]` remember explicit LO opt-in so a later normal
  `https://inimatic.com` visit can probe the phone after browser permission was
  granted.

Gate A4:

- starting the node and tapping `Open AdaOS` selects LO on the same phone;
- no AdaOS credentials, pair code, or owner login are requested;
- `web_desktop` is loaded from `/yws/desktop`, not from a hard-coded mobile
  page;
- stopping the service causes an explicit local-runtime unavailable state;
- restarting the service reconnects HTTP, WS, and YWS without reinstalling the
  app or clearing browser storage.

## Phase A5: Skill and Scenario Proof Matrix

Outcome: the fixed bundle demonstrates the actual UI-as-data paths chosen for
the PoC.

### `subnet_env`

- [ ] `[must]` invoke a read action and render its Yjs snapshot.
- [ ] `[must]` update one allowlisted value and observe the UI change without a
  full page reload.

### Weather

- [x] `[must]` select a city and observe `data/weather/current` update in the
  widget and modal.
- [ ] `[should]` grant browser geolocation and repeat the update with device
  coordinates.
- [x] `[must]` show a bounded, understandable error while offline.

### Notebook

- [x] `[must]` create, edit, and delete a plain-text note.
- [x] `[must]` verify the note list arrives through the declared stream
  snapshot and the compact durable projection remains in Yjs.
- [x] `[must]` restart the node and verify skill-memory rehydration republishes
  the same note state.
- [ ] `[could]` attach one browser-selected file and reopen it through the local
  file route.
- [ ] `[deferred]` require Telegram export; it is not part of the local-node
  proof.

### AdaOS Connect

- [x] `[must]` open the modal and render its Yjs state.
- [x] `[must]` show a useful offline/degraded state when Root is unreachable.
- [ ] `[should]` exercise one real Root-backed browser or node preparation flow
  after Phase A6 connectivity exists.

### Taiga UI

- [x] `[must]` switch from `web_desktop` to
  `taiga_ui_demo_scenario` and back.
- [ ] `[must]` render the metrics table, tree, chart, and selection from Yjs.
- [x] `[must]` emit a skill event and a host event and observe the live receiver.
- [x] `[must]` verify scenario switching does not create a second legacy
  `default` room or lose the `desktop` document.

Gate A5:

- every must-level interaction completes on the phone through the normal
  hosted client;
- Yjs and stream ownership match the manifests;
- no selected skill starts a child process;
- unavailable optional behavior degrades without blocking desktop readiness.

## Phase A6: Member Link

Outcome: the phone remains locally useful and also participates as a real
member of an existing subnet.

- [ ] `[must]` package the minimal `cryptography`/TLS dependency closure needed
  by the existing membership contract, or supply a tested Android adapter for
  that boundary.
- [ ] `[must]` store member credentials separately from the local API token.
- [ ] `[must]` join using the existing short-lived join contract; a temporary
  manual provisioning step is acceptable before AdaOS Connect owns the whole
  flow.
- [ ] `[must]` start the outbound member-link client only after the local
  runtime is ready.
- [ ] `[must]` report `connecting`, `connected`, `offline`, and expected-stop
  states without blocking LO.
- [ ] `[must]` send the member identity and bounded local runtime/desktop
  contribution to the hub.
- [ ] `[must]` converge the allowed default webspace state after reconnect.
- [ ] `[must]` apply bounded exponential reconnect backoff and cancel it during
  an explicit service stop.
- [ ] `[should]` complete member onboarding through the bundled AdaOS Connect
  UI.
- [ ] `[should]` move long-lived private-key custody to Android Keystore while
  retaining the existing membership contract at the AdaOS boundary.

Gate A6:

- the hub observes the phone as one member with stable identity;
- disabling and restoring network reconnects without restarting the Activity;
- LO remains usable while the hub or WAN is unavailable;
- local Yjs edits made during the outage converge after reconnect;
- no inbound LAN listener or supervisor is required.

## Phase A7: Lifecycle and 2 GB Device Gate

Outcome: determine whether the experiment is viable on the intended class of
older phones.

- [ ] `[must]` measure process PSS and startup peak on a physical 2 GB device;
  do not use only emulator measurements.
- [ ] `[must]` verify preferred idle PSS at or below 200 MiB or record the
  import/cache owners responsible for exceeding it.
- [ ] `[must]` verify startup peak PSS at or below 320 MiB.
- [ ] `[must]` run without `largeHeap`.
- [ ] `[must]` keep all selected queues, caches, logs, note lists, and stream
  snapshots within declared bounds.
- [ ] `[must]` test Activity destroy/recreate while the service remains ready.
- [ ] `[must]` test screen off/on, browser foreground/background, Wi-Fi loss,
  and WAN loss for at least 30 minutes.
- [ ] `[must]` force-stop and restart the application, then verify Yjs,
  Notebook, install descriptor, and membership persistence.
- [ ] `[must]` verify the user can stop the foreground service and that it does
  not silently resurrect.
- [ ] `[should]` run the same artifact on API 26, API 30/31, API 34, and API 36.
- [ ] `[should]` verify one 16 KB page-size device or emulator image.
- [ ] `[should]` capture a reproducible evidence bundle with build identity,
  device facts, logs, memory samples, browser build, and gate results.

Gate A7:

- there are no native crashes, corrupt state, leaked listeners, or unbounded
  memory growth in the matrix;
- the node remains useful on the 2 GB device or the measured blocker is narrow
  enough to drive one explicit optimization phase;
- Android lifecycle behavior is visible and recoverable rather than presented
  as guaranteed always-on service.

Early evidence, not an A7 pass: after the PoC3 skill/restart smoke on the
Samsung SM-F721N, Android reported 79,635 KiB total PSS for the AdaOS process
(APK 22,448,355 bytes). This is comfortably below the provisional idle budget,
but it is not a substitute for the required 2 GB device and duration matrix.

## Dependency Work Queue

These tasks may begin early, but a dependency is admitted to the APK only when
the phase which needs it is active.

| Dependency | Needed by | First action | Fallback |
| --- | --- | --- | --- |
| CPython 3.11 | A0 | embed arm64 runtime | change embedder, not AdaOS semantics |
| `y-py` fork | A1 | cross-build Android wheel | none; gate stops |
| `pydantic-core` and FastAPI closure | A2 | inventory embedder-compatible wheels | minimal ASGI composition only if full app imports are too broad |
| SQLite / SQLAlchemy closure | A2 | prove app-private DB and selected sync paths | use pure-Python SQLAlchemy path; do not replace storage model |
| `cryptography` | A6 | build/prove Android arm64 package | Android TLS/Keystore adapter behind existing contract |
| `psutil` | A7 | isolate import and required metrics | Android diagnostics adapter |
| `sounddevice`, `aiortc`, PyAV | deferred | none in PoC | explicit capability unavailable |

The Android lock must be built from imports reached by the selected profile,
not by copying `pyproject.toml` wholesale.

## First-Draft Repository Shape

The initial implementation should keep Android-specific code visibly separate:

```text
src/adaos/integrations/android-node/
  app/                         # Kotlin Activity, NodeService, PythonHost
  python/                      # thin Android bootstrap entry point
  packaging/                   # install descriptor and wheel inputs
  README.md                    # developer build/run instructions

src/adaos/services/
  ...                          # shared runtime code with typed profile gates
```

The exact Gradle directory names may follow Android conventions, but Android
code must not be placed in the ReDevice submodule and shared AdaOS behavior
must not be copied into a mobile fork.

## Verification Layers

### Host tests

- runtime-profile composition excludes forbidden services;
- install descriptor schema, hash verification, and allowlist behavior;
- `web_desktop` required/optional dependency normalization;
- bundle materialization and migration;
- local-token compatibility without user login;
- stop/drain idempotence;
- existing Yjs, scenario projection, selected skill, member-link, and browser
  client tests.

### Android instrumentation

- foreground service start/stop and Activity recreation;
- PythonHost start, structured status, and failure propagation;
- app-private storage and bundle materialization;
- loopback HTTP, WS, and YWS readiness;
- process restart persistence;
- ABI/page-size load test for every native library.

### Browser end-to-end

- Chrome Local Network Access approval;
- explicit LO discovery from `inimatic.com`;
- remote owner session retained while LO is selected;
- first Yjs sync and reconnect;
- selected skill/scenario matrix;
- visible service-stop and upstream-offline behavior.

### Physical-device evidence

- lower-bound API 26 arm64 phone;
- representative 2 GB phone;
- current API 36 phone or emulator;
- memory, startup, restart, screen-off, network-loss, and 16 KB native-library
  evidence.

## Post-PoC Work

### Should follow a successful A7 gate

- replace sentinel lifecycle status with a bounded Android watchdog and restart
  policy;
- add signed release builds and application-update migration tests;
- widen the physical device matrix;
- make version and native-wheel evidence part of CI artifacts;
- finish Android Keystore custody;
- define battery, thermal, and long-duration soak budgets;
- decide whether API 26 remains supportable from evidence rather than lowering
  it pre-emptively.

### Could follow only when demanded

- `x86_64` debug emulator flavor;
- verified pure-Python content updates independent of the APK;
- a local LAN listener with an explicit access policy;
- native Android notifications or widgets backed by AdaOS state;
- Android audio/TTS/camera adapters;
- WebRTC data/media upgrade;
- phone-as-hub experiments on higher-memory devices.

### Deferred until a separate architecture exists

- service-skill subprocess isolation;
- arbitrary `pip` and marketplace code installation;
- Android-local core A/B slots;
- self-modifying native dependencies;
- boot-time silent daemon guarantees;
- Android below API 26 or 32-bit full-node builds;
- replacing ReDevice for legacy hardware.

## Completion Evidence

The roadmap is complete only when one evidence bundle identifies:

- APK version, commit, signing mode, install descriptor id, and artifact hashes;
- Python, Android, ABI, device RAM, page size, WebView/Chrome, and hub versions;
- A0 through A7 gate results;
- local API, WS, YWS, and member-link state transitions;
- screenshots or browser automation evidence for the selected scenarios and
  skills;
- PSS startup/steady samples and bounded soak results;
- persistence results after service restart and force-stop;
- known deviations and explicitly deferred capabilities.

Passing on a high-memory emulator alone is not completion evidence. Passing
the local browser slice without the real `y-py` store is also not completion.
