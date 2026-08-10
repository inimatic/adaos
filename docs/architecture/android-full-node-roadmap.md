# AdaOS Android Full Node Roadmap

Status: active domain roadmap for an experimental proof of concept. The first
A0/A1/A2/A3/A4/A5 vertical slice, the A6 member-protocol slice, the A7
bounded-runtime implementation, the PoC9 Rasa/remote-assistant extension,
the PoC11 routed-connectivity/voice half-duplex corrections, and the PoC13
deployed-membership recovery corrections
are running on a physical Android 16 arm64 phone. It renders `web_desktop` from
native `y-py`, executes the fixed skill profile and offline Rasa inference
in-process, and maintains an outbound member link through the deployed regional
Root route. Keystore custody and the physical 2 GB device gate remain open.

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
11. classify every dialog turn with the promoted Rasa model while fully
    offline, with model training performed outside the phone;
12. invoke the canonical Hub companion skill and its configured external LLM
    for Арсений/Мира without copying their profiles or prompts into Android;
13. forward low-confidence NLU evidence to the canonical LLM Teacher when the
    Hub is reachable.

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
- [x] `[must]` Configure `minSdk 26`, `targetSdk 36`, `compileSdk 36`, and the
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
- [x] `[must]` bound retained Yjs history, structurally compact an over-limit
  snapshot, and fence old browser BroadcastChannels from the new generation.
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
- [x] `[must]` preserve desktop-wide required branches while activating an
  alternate scenario and repair a persisted pre-fix materialization on APK
  restart.
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
- [x] `[must]` implement `desktop.webspace.go_home`, reject unsupported
  control commands instead of acknowledging no-ops, and calculate HTTP
  materialization readiness from the live YDoc.
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

Physical evidence (2026-08-08): PoC4 was installed over the existing PoC3
application data on the Samsung SM-F721N. The hosted Chrome client synchronized
the 1.32 MiB persisted `desktop` document, displayed `Online local`, switched
to Taiga UI, and invoked the actual close control. The node acknowledged
`desktop.webspace.go_home` with `scenario_id=web_desktop`; the browser rendered
the desktop without entering `Recovering`, and live materialization reported
`ready`, no missing branches, and a consistent scenario. A persisted Taiga
projection created by PoC3 was also repaired on startup without clearing data.

PoC5 follow-up evidence: the 1.32 MiB retained-history document was
structurally compacted to a roughly 50 KiB semantic snapshot with its store
generation incremented. The hosted client now qualifies the physical YWS room
with this generation, while Android rejects stale qualified generations and
client updates larger than 512 KiB. This closes the multi-tab path which could
previously replay pre-compaction history after a node restart.

## Phase A5: Skill and Scenario Proof Matrix

Outcome: the fixed bundle demonstrates the actual UI-as-data paths chosen for
the PoC.

### `subnet_env`

- [x] `[must]` invoke a read action and render its Yjs snapshot.
- [x] `[must]` update one allowlisted value and observe the UI change without a
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
- [x] `[must]` keep the already-trusted LO browser behind the native `Open
  AdaOS` action without login or pairing; do not present it as a remote AdaOS
  Connect invitation.
- [x] `[must]` keep Root/member status separate from local-browser readiness and
  show a useful offline/degraded state when the optional upstream is unreachable.
- [x] `[should]` split `Connect this phone` from canonical Hub-delegated `Add
  browser`, `Add Telegram`, and `Add node` actions.
- [x] `[should]` exercise one real Root-backed browser and node preparation flow
  against the deployed subnet after the corresponding Hub build is live.

### Browsers

- [x] `[must]` include the standard Browsers surface in the immutable Android
  install profile.
- [x] `[must]` project active local control sessions and remove them after the
  connection closes.
- [x] `[must]` keep the MVP read-only; rename, detach, lifetime, and remote
  media controls are not acknowledged as implemented.

### Voice Assistant

- [x] `[must]` use browser SpeechRecognition on Android instead of the Hub WAV
  provider which the mobile node does not expose.
- [x] `[must]` accept `dialog.user_message`, project a bounded local dialog tail,
  and render it through the normal Voice/Chat widgets.
- [x] `[must]` speak a new assistant response through browser speechSynthesis,
  including when the Voice Assistant modal is closed.
- [x] `[must]` use shared client half-duplex arbitration so continuous STT is
  stopped during speechSynthesis and resumes only after an acoustic tail.
- [x] `[must]` require the normal `inimatic.com` microphone permission and keep
  local authentication policy independent from hardware permission.
- [x] `[should]` route explicit Weather, node-status, and Notebook commands to
  allowlisted in-process behavior.
- [x] `[must]` publish the standard `data/dialog` active channel/agent shape and
  the additive bounded mobile roster: AdaOS Mobile, Арсений, Ника, Мира, and
  Строитель.
- [x] `[must]` expose channel and agent selectors through the normal hosted
  client, acknowledge `dialog.channel.select` and `dialog.agent.select`, and
  persist the selected agent across a runtime restart.
- [x] `[must]` support addressed turns such as `Арсений, ...` and expose the
  actual response source instead of presenting a local facade as model-backed.
- [x] `[must]` run the promoted Rasa model for every dialog turn, including
  without Hub/WAN connectivity.
- [x] `[must]` route companion turns through the canonical Hub
  `conversation_companions` skill and common external-LLM client when online.
- [x] `[must]` forward low-confidence Rasa turns to the canonical LLM Teacher.
- [ ] `[deferred]` background listening, wake word, app-native AudioRecord/TTS,
  phone-local LLM inference, and full Builder skill generation.

### Taiga UI

- [x] `[must]` switch from `web_desktop` to
  `taiga_ui_demo_scenario` and back.
- [x] `[must]` render the metrics table, tree, chart, and selection from Yjs.
- [x] `[must]` emit a skill event and a host event and observe the live receiver.
- [x] `[must]` verify scenario switching does not create a second legacy
  `default` room or lose the `desktop` document.

Gate A5:

- every must-level interaction completes on the phone through the normal
  hosted client;
- Yjs and stream ownership match the manifests;
- no selected skill starts a child process;
- unavailable optional behavior degrades without blocking desktop readiness.

Physical evidence (2026-08-08): the PoC4 device smoke exercised Weather
through Open-Meteo, AdaOS Connect's bounded offline state, Notebook
create/delete and stream snapshots, the demo stream, and the Taiga scenario
round trip. A Yjs marker and the created Notebook note both survived separate
forced process restarts. The final scenario was `web_desktop`, the process was
still alive, and Logcat contained no Python traceback, fatal application
exception, or ANR.

PoC5 completed the remaining local matrix on the same device. `subnet_env`
read and updated the editable node label through Yjs. Chrome rendered the
Taiga semantic table, five-node tree, SVG chart, event list, chat, and shared
selection, then returned to `web_desktop` with the local connection online and
no materialization blockers.

PoC7 physical evidence (2026-08-09): APK `0.1.0-poc7` was installed over the
existing PoC6 data on the API 36 Samsung SM-F721N. The restart/skill smoke
passed Yjs and Notebook persistence, Weather offline/recovery, Connect LO,
one active Browsers projection, a local voice turn, and the Taiga round trip.
Chrome 149 exposed both SpeechRecognition and speechSynthesis; clicking the
header microphone entered listening and raised the origin-scoped microphone
prompt. A Voice Assistant modal command (`node status`) traversed the real
control socket, appeared in `data/voice_chat`, rendered its local response,
and set `speechSynthesis.speaking=true`. The live materialization remained
ready, displayed Browsers and Voice Assistant, and did not contain a recovery
state. The debug APK is 22,464,755 bytes with SHA-256
`0e8cb7a1f08d31d09607ef275982c17ce9483a1718bd6cd38b21ccf769488c3c`.

PoC8 physical evidence (2026-08-10): APK `0.1.0-poc8` was installed over the
same persisted data on the API 36 Samsung SM-F721N. The host suite passed
12/12 and the physical restart/skill smoke verified the five-agent roster,
explicit selection of Ника, Builder channel selection, addressed activation of
Арсений, and selected-agent persistence after force-stop/restart. The hosted
client's real Voice Assistant modal rendered General/Conversational/Builder,
opened an `Ассистент` selector containing AdaOS Mobile, Арсений, Ника, Мира,
and Строитель, and changed the live Yjs projection through its UI event path.
The final state was `web_desktop`, materialization `ready`, active agent
Арсений, no Recovery text, and no matching fatal/traceback entries in Logcat.
The debug APK is 22,464,755 bytes with SHA-256
`b338e718923f36a501ff26231329a0234689cd20b0f1f9e52560e086e21b967d`.

## Phase A6: Member Link

Outcome: the phone remains locally useful and also participates as a real
member of an existing subnet.

- [x] `[must]` package the minimal `cryptography`/TLS dependency closure needed
  by the existing membership contract, or supply a tested Android adapter for
  that boundary.
- [x] `[must]` store member credentials separately from the local API token.
- [x] `[must]` join using the existing short-lived join contract; a temporary
  manual provisioning step is acceptable before AdaOS Connect owns the whole
  flow.
- [x] `[must]` start the outbound member-link client only after the local
  runtime is ready.
- [x] `[must]` report `connecting`, `connected`, `offline`, and expected-stop
  states without blocking LO.
- [x] `[must]` require an acknowledged hello and recent pong/activity before
  reporting `connected`; reconnect after a bounded heartbeat timeout.
- [x] `[must]` send the member identity and bounded local runtime/desktop
  contribution to the hub.
- [x] `[must]` converge a bounded node-owned semantic snapshot after
  reconnect without merging the phone's raw desktop YDoc into the Hub YDoc.
- [x] `[must]` apply bounded exponential reconnect backoff and cancel it during
  an explicit service stop.
- [x] `[should]` complete member onboarding through the bundled AdaOS Connect
  UI.
- [x] `[must]` acknowledge join submission immediately and publish the final
  result or precise Root error asynchronously through Yjs.
- [ ] `[should]` move long-lived private-key custody to Android Keystore while
  retaining the existing membership contract at the AdaOS boundary.

Gate A6:

- the hub observes the phone as one member with stable identity;
- disabling and restoring network reconnects without restarting the Activity;
- LO remains usable while the hub or WAN is unavailable;
- local Yjs edits made during the outage converge after reconnect;
- no inbound LAN listener or supervisor is required.

Protocol evidence (2026-08-08): PoC5 joined a protocol-compatible Root/Hub
fixture through AdaOS Connect's Root URL and one-time-code contract. The phone
retained membership across a forced process restart, kept LO ready through a
hub outage, reconnected with bounded backoff, and exchanged Yjs updates in
both directions. The temporary token never appeared in status/Yjs evidence
and was removed by `disconnect & forget`. TLS uses the embedded Python
standard-library client with system CA validation, avoiding an Android
`cryptography` dependency in this slice. PoC13 completed the deployed-subnet
run against `sn_6acf0c01`: Root mTLS created and consumed a fresh one-time
code, the phone connected over the public TLS Hub route without ADB reverse,
and Hub logs ingested bounded `yjs.node_state` records for the stable Android
node id. Long-lived key migration to Android Keystore remains open.

## Phase A7: Lifecycle and 2 GB Device Gate

Outcome: determine whether the experiment is viable on the intended class of
older phones.

- [ ] `[must]` measure process PSS and startup peak on a physical 2 GB device;
  do not use only emulator measurements.
- [x] `[must]` verify preferred idle PSS at or below 200 MiB or record the
  import/cache owners responsible for exceeding it.
- [x] `[must]` verify startup peak PSS at or below 320 MiB.
- [x] `[must]` run without `largeHeap`.
- [x] `[must]` keep all selected queues, caches, logs, note lists, and stream
  snapshots within declared bounds.
- [x] `[must]` test Activity destroy/recreate while the service remains ready.
- [x] `[must]` test screen off/on, browser foreground/background, Wi-Fi loss,
  and WAN loss for at least 30 minutes.
- [x] `[must]` force-stop and restart the application, then verify Yjs,
  Notebook, install descriptor, and membership persistence.
- [x] `[must]` verify the user can stop the foreground service and that it does
  not silently resurrect.
- [ ] `[should]` run the same artifact on API 26, API 30/31, API 34, and API 36.
- [ ] `[should]` verify one 16 KB page-size device or emulator image.
- [x] `[should]` capture a reproducible evidence bundle with build identity,
  device facts, logs, memory samples, browser build, and gate results.

Gate A7:

- there are no native crashes, corrupt state, leaked listeners, or unbounded
  memory growth in the matrix;
- the node remains useful on the 2 GB device or the measured blocker is narrow
  enough to drive one explicit optimization phase;
- Android lifecycle behavior is visible and recoverable rather than presented
  as guaranteed always-on service.

Early evidence, not an A7 pass: after the PoC3 skill/restart smoke on the
Samsung SM-F721N, Android reported 79,635 KiB total PSS for the AdaOS process.
After the expanded PoC4 smoke and with the Activity in the foreground it
reported 165,752 KiB total PSS and 253,872 KiB total RSS; the debug APK was
22,448,351 bytes. Both PSS samples are below the provisional steady budget,
but neither is a controlled steady/peak measurement or a substitute for the
required 2 GB device and duration matrix.

The final PoC5 debug APK is 22,464,739 bytes with SHA-256
`d79940a1428de272e1e49352db2c174dd6d03e4552edf63c750cea551f5f1e88`.
After the full skill/restart smoke and member outage/reconnect proof, Android
reported 108,304 KiB total PSS and 197,732 KiB total RSS. The API 36 SM-F721N
has 7,442,748 KiB total RAM and a 4 KiB page size, so this remains useful
upper-device evidence, not the required 2 GB or 16 KiB A7 result. The live
YStore was generation 2 with a 53,081-byte snapshot, `ready` pressure, and no
matching fatal exception, Python traceback, or ANR in Logcat.

PoC6 upper-device evidence (2026-08-09): commit `43046032`, APK
22,549,213 bytes with SHA-256
`bb4abb4b965d7058b42a9c9a9b720c51512dae9014575a978e4408acc6ab41f6`
completed the reproducible lifecycle verifier on the same API 36 SM-F721N.
The final run lasted 1,805 seconds and retained 169 steady samples. Android
total PSS ranged from 124,239 to 126,307 KiB (first 125,539; last 126,263),
with a 129,553 KiB maximum against the startup budget. Activity
recreation, screen off/on, Chrome 149 foreground/background, 30-second Wi-Fi
and full-WAN outages, Yjs/Notebook/install persistence, explicit user stop,
and final restart all passed. YStore, loopback, and member-link rejected or
dropped counters remained zero. The artifact explicitly uses no large heap
and publishes all selected limits through status. The evidence JSON is written
to `app/build/reports/android-lifecycle-evidence.json`.

The first duration run exposed an in-flight status request racing final
runtime cleanup and logging `KeyError('subnet_id')`. The final artifact closes
the listener before draining accepted request threads and returns a safe
stopped status during the transition. An eight-poller host regression test,
a focused physical stop calibration, and the repeated 1,805-second run all
passed. The final Logcat scan contained no matching Python traceback,
bootstrap failure, fatal exception, or ANR.

The full PoC6 run began with no configured membership and verified that this
state remained stable. The checked force-stop membership item combines this
run with the earlier PoC5 configured-member fixture, which preserved the
actual Root/Hub membership across restart and outage.

This closes the A7 implementation and high-memory lifecycle slice, not Gate
A7 itself. The physical 2 GB run is still mandatory; API 26, API 30/31, API
34, and a 16 KiB page-size target also remain as matrix evidence.

## PoC9 Extension: Canonical Rasa, Companions, and Teacher

Outcome: add useful conversational intelligence without creating independent
Android and stationary product lines.

- [x] `[must]` train only off-device and preserve one promoted Rasa source
  model for stationary and Android delivery.
- [x] `[must]` export a deterministic, inference-only Android bundle with the
  source model id and SHA-256 recorded in the immutable install descriptor.
- [x] `[must]` implement tokenizer, regex, vectorizer, logistic intent, CRF
  entity, and synonym inference without unavailable Android native packages.
- [x] `[must]` re-fit only the non-portable CRFsuite component from the dataset
  and configuration persisted in the canonical model archive, and gate the
  result with entity parity probes.
- [x] `[must]` run Rasa on every phone dialog turn (`mode=always`) and retain
  that complete NLU path while Hub/WAN connectivity is absent.
- [x] `[must]` compare portable intent/entity results with canonical Rasa in
  host promotion tests using a sub-ppm numeric tolerance.
- [x] `[must]` copy the shared portable runtime source into the APK during the
  Gradle build; do not maintain a second Android source file.
- [x] `[must]` expose Rasa readiness, source identity, and training policy in
  `/api/node/status`.
- [x] `[must]` add a Hub-side member RPC allowlist containing only public
  `conversation_companions` operations; arbitrary skill and shell calls stay
  impossible.
- [x] `[must]` keep companion profiles, prompts, tools, and external-LLM
  credentials in their canonical Hub/Root owners.
- [x] `[must]` dispatch low-confidence `nlp.intent.not_obtained` evidence from
  the phone to the canonical LLM Teacher.
- [x] `[must]` preserve an explicit local response when the Hub call is
  unavailable and label it `android_offline_fallback`.
- [ ] `[should]` promote Rasa bundles through signed release CI rather than a
  manual export command.
- [ ] `[should]` exercise the final artifact against a long-running deployed
  Hub and capture successful Teacher ledger completion in addition to event
  admission.
- [ ] `[could]` make offline dialog generation richer. This is separate from
  the already complete offline Rasa NLU path and must not fork companion
  profiles or silently package a second LLM.

Anti-drift gate:

1. one canonical Rasa training archive is the source of both deployments; the
   Android CRF is a gated re-fit from its persisted data/config, not an
   independently curated model;
2. the Android bundle records that archive's id/hash and is reproducible;
3. one shared portable runtime source is executed by host parity tests and
   copied by Gradle;
4. assistant and Teacher logic stays on Hub; Android implements transport and
   offline degradation only;
5. every physical response reports `nlu.model_id`, `response_source`, and
   `used_llm`, making accidental divergence observable.

PoC9 physical evidence (2026-08-10): APK `0.1.0-poc9` was installed over the
persisted data on the API 36 Samsung SM-F721N. The final APK is 22,927,600
bytes with SHA-256
`37b2960b07af51cc387072451886c17f278e5cf95a96ebcde15e76f1dba21e2b`.
CPython 3.11.14 loaded model `362b6f47acb743658d8cd4bb8f538a41`
(`rasa 3.6.21`, 27 intents) in `rasa/always` mode. Status reported the promoted
source SHA-256 and `training=off_device`; startup completed in 118 ms and the
sampled process PSS was 88,028 KiB with a 99,890 KiB observed peak. The phone
classified `какая погода в Москве` as `weather.current` at confidence
0.8829258 while its Hub tunnel was absent, then returned the explicit local
fallback response.

In the connected physical run, an Арсений turn completed through
`conversation_companions:talk` with `response_source=hub_skill_llm` and
`used_llm=true`, proving use of the existing Root external-LLM configuration.
A low-confidence mobile turn entered the canonical LLM Teacher; its optional
MCP-evidence lookup timed out independently. The final Teacher ledger result
was not captured, so that narrower acceptance item remains open. A stationary
browser/Yjs restart also exposed pre-existing `y_py` cross-thread drop errors;
that issue is not caused by portable NLU or member RPC and remains a separate
reliability investigation.

## PoC11 Extension: Routed Connect, Voice Arbitration, and Native Identity

Outcome: make the deployed-subnet path match the product semantics and remove
two misleading mobile-only behaviors.

- [x] `[must]` fix Root join responses to advertise the public request base,
  not the backend container listener protocol.
- [x] `[must]` canonicalize public Inimatic membership routes to HTTPS on
  Android and migrate an older persisted plaintext URL without another join.
- [x] `[must]` remove the LO browser link from AdaOS Connect; LO remains the
  native Activity's local `Open AdaOS` path.
- [x] `[must]` expose a distinct `Connect this phone` flow for member
  enrollment.
- [x] `[must]` delegate remote browser, Telegram, and other-node invitations
  to the canonical Hub `adaos_connect:prepare` tool through the bounded member
  RPC allowlist.
- [x] `[must]` acknowledge invitation preparation immediately and finish it
  as a single-flight background RPC so Hub latency cannot starve control
  WebSocket ping/pong and cause false browser Recovery.
- [x] `[must]` make companion projections report `hub_delegated` and
  `model_backed=true` only while the authenticated Hub link is connected.
- [x] `[must]` pause shared-client STT around speechSynthesis and ignore TTS
  echo transcripts until the bounded acoustic tail expires.
- [x] `[must]` execute a dialog turn outside the control-socket reader with a
  single bounded slot, preserving ping/pong throughout a slow Hub/LLM call.
- [x] `[should]` use the canonical AdaOS mark with an indigo native-node
  background, leaving the browser/PWA mark monochrome.
- [x] `[must]` install PoC11 on the physical phone and capture deployed Root,
  companion LLM, remote Connect invitation, transport keepalive, and launcher
  evidence.
- [ ] `[should]` capture one unlocked, hands-free acoustic STT -> TTS -> STT
  cycle after the user grants Chrome microphone permission; deterministic
  client tests already cover the half-duplex state machine.

PoC11 physical evidence (2026-08-10): APK `0.1.0-poc11` was installed over the
persisted application data on the API 36 Samsung SM-F721N. The debug APK is
22,661,987 bytes with SHA-256
`2ee30da3ed6fd8109ebd8843438e12e1b303954c4017cfa5e960cec0316cf0f2`;
status reported CPython 3.11.14, install descriptor
`93980767388ac6f49761ebe6900629e887c61bbcb65ffde6bf3f95407e151502`,
Rasa model `362b6f47acb743658d8cd4bb8f538a41`, and no `largeHeap`. The app
automatically migrated its persisted public member route to
`https://ru.api.inimatic.com/hubs/sn_6acf0c01` and connected with TLS.

The phone prepared a canonical remote browser invitation through the Hub in
1.42 seconds: the control ACK returned `pending` in 125 ms, then Yjs published
`ready`, `source=hub_delegated`, and `zone_id=ru`; no LO invitation was
projected. Арсений and Мира both returned `response_source=hub_skill_llm` and
`used_llm=true` in 3.92 and 9.06 seconds respectively. A deliberately delayed
45-second upstream run retained the same control connection with five-second
keepalives, and the host test proves ping/pong is answered while a dialog
worker is blocked. The launcher rendered the canonical white AdaOS mark on an
indigo native-node background, visibly distinct from the monochrome
browser/ReDevice identity. The Chrome acoustic-loop evidence remains the one
manual item because microphone permission and a spoken turn require an
unlocked browser surface.

Anti-drift rule: Android owns enrollment and projection only. Invitation
generation, companion prompts/profiles, external LLM use, and Teacher behavior
remain canonical Hub skills. The voice arbitration fix is in
`adaos-client/main`, not in a mobile client fork.

## PoC13 Extension: Deployed Root Join and Recovery

Outcome: finish the real membership path and remove the two recovery failures
found by the physical run.

- [x] `[must]` let the Root join-code endpoint accept the Hub identity only
  when it was verified and forwarded by the trusted TLS terminator.
- [x] `[must]` create and consume a fresh regional Root code without exposing
  the code or returned member token in logs, process arguments, Yjs, or status.
- [x] `[must]` prove the phone uses
  `https://ru.api.inimatic.com/hubs/sn_6acf0c01/ws/subnet` after removing the
  temporary ADB reverse.
- [x] `[must]` replace a member worker on explicit reconfiguration even when
  the previous worker is alive but wedged in a native socket operation.
- [x] `[must]` prevent an obsolete worker generation from overwriting the new
  link state or failing its pending RPCs.
- [x] `[must]` delegate a real remote Browser invitation and a companion turn
  through the authenticated public member link.
- [x] `[must]` serialize detached synchronous `y_py` replay for each Hub
  webspace after a Windows `0xC0000005` exit coincided with two concurrent
  `get_ydoc("desktop")` sessions.
- [ ] `[should]` repeat the browser/companion pressure run after the stationary
  Hub activates the serialized-YDoc core and retain a no-restart observation
  window.

PoC13 physical evidence (2026-08-10): debug APK `0.1.0-poc13` is 22,933,564
bytes with SHA-256
`b8a774ede4bcada42933194e3856beee595ae56435074389f9e3f5f2a541ff5c`.
It contains CPython 3.11.14, portable Rasa 3.6.21 model
`362b6f47acb743658d8cd4bb8f538a41`, install descriptor `1.7.1`, and the
generation-aware member worker. On the Samsung SM-F721N (API 36), a fresh
Root code produced a public TLS member route and `hello.ack=true`; a second
join while connected completed with `connect_attempts=2`. AdaOS Connect
returned `ready`, `source=hub_delegated`, and non-empty remote Browser link and
code. Arseni returned `response_source=hub_skill_llm`, `used_llm=true`, and
`llm_route=root_llm` with no route error. Root backend `0.1.172` reports commit
`b808f83` after its successful blue-green deployment.

The same pressure run reproduced the stationary Hub's pre-fix native exit:
two concurrent sync `desktop` sessions were the final native operations and
the process exited with Windows status `0xC0000005`. Host regression now runs
all 35 Yjs document-store tests and proves three simultaneous sync readers
enter native replay one at a time. Physical no-restart evidence remains open
until that new core is active in the Hub A/B slot.

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
| Rasa model | PoC9 | export promoted artifact into portable inference data | reject promotion; never retrain on phone |
| `sounddevice`, `aiortc`, PyAV | deferred | not needed by PoC7 browser voice | explicit native/background capability unavailable |

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
  it pre-emptively;
- sign and publish the portable Rasa artifact in the normal release pipeline;
- extend the local assistant only through allowlisted skill intents and add
  explicit confirmation for every mutating voice command;
- route Builder through a separately reviewed authenticated Hub contract;
- keep the existing companion Hub path and bounded local fallback under
  outage/latency testing before considering any on-phone LLM runtime.
- add shared skill/scenario compatibility metadata for portable-local,
  Hub-delegated, Android-adapted, and unsupported-service execution classes;
- specify a restricted edge-Hub profile with outbound-only Root routing,
  member/Yjs authority, Keystore custody, resource budgets, and no arbitrary
  service skills. External LLM use remains remote and does not block this work.

### Could follow only when demanded

- `x86_64` debug emulator flavor;
- verified pure-Python content updates independent of the APK;
- a local LAN listener with an explicit access policy;
- native Android notifications or widgets backed by AdaOS state;
- app-native foreground audio/TTS/camera adapters when browser mediation is
  insufficient;
- WebRTC data/media upgrade;
- restricted edge-Hub experiments on higher-memory devices after member
  routing, lifecycle, Keystore, and resource-budget gates are specified.

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
- A0 through A7 gate results and the PoC9 NLU/dialog anti-drift checks;
- local API, WS, YWS, and member-link state transitions;
- screenshots or browser automation evidence for the selected scenarios and
  skills;
- PSS startup/steady samples and bounded soak results;
- persistence results after service restart and force-stop;
- known deviations and explicitly deferred capabilities.

Passing on a high-memory emulator alone is not completion evidence. Passing
the local browser slice without the real `y-py` store is also not completion.
