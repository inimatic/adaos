# Legacy iOS experiment workspace

This directory contains preparation assets for an owner-operated legacy iOS
experiment. The initial target is an iPad mini 1 cellular on iOS 9.3.6.
It is not a supported AdaOS client release or an automatic jailbreak installer.

## Contents

- `device-profile.json`: expected model/OS/build and architecture; no identifiers.
- `dependencies.lock.json`: exact upstream downloads, hashes, source commits and
  container base image used by the lab.
- `smoke/`: a native Objective-C/UIKit app with a persisted tap counter and
  launch count, plus the experimental native iPhone/iPad onboarding and
  Conversational client. The original lab probes remain reachable explicitly;
  the local diagnostics need no network, account or remote AdaOS instance.
- `toolchain/Dockerfile`: isolated Linux builder with Theos and the iOS 10.3 SDK,
  targeting iOS 9.0 and ARMv7.
- [`../tools/jailbreak_lab.py`](../tools/jailbreak_lab.py): preparation commands.
- [`../tests/test_jailbreak_lab.py`](../tests/test_jailbreak_lab.py): bounded
  checks for framing, integrity, archive inspection and ARMv7 admission.

Internal target architecture, roadmap, proof gates and operator instructions
belong to the private documentation repository, according to repository policy:

- [Target architecture](../docs-development/platform/architecture/legacy-ios-terminal.md).
- [Roadmap and acceptance gates](../docs-development/platform/architecture/legacy-ios-terminal-roadmap.md).
- [Operator runbook](../docs-development/platform/integrations/legacy-ios-jailbreak-runbook.md).
- [Current preparation evidence](../docs-development/platform/evidence/legacy-ios-terminal-preparation-2026-11-11.md).
- [Onboarding and Conversational](../docs-development/platform/onboarding/legacy-ios-terminal.md).
- [Iteration-2 evidence](../docs-development/platform/evidence/legacy-ios-onboarding-2026-11-12.md).
- [Initial build attempts](../docs-development/platform/evidence/legacy-ios-terminal-preparation-2026-11-10.md).

Those links resolve in a development checkout that includes `docs-development`.

## Native onboarding and Conversational experiment

The same UIKit source supports phone/tablet layouts; only ARMv7/iOS 9 on the
prepared iPad is built/installed. There is no qualified ARM64 or stock-iOS
distribution. The app keeps its original bundle ID and Keychain access group
to preserve local state, while its displayed name is now AdaOS.

The local RU/EN flow tests speaker, display, four real touch zones and optional
three-second microphone replay. Bundled speech works offline. Results persist
between launches; playback completion and human confirmation remain separate.
No diagnostic result creates a server policy or grants sensor permission.

Connection uses Seed Client's existing browser approval QR/link and a separate
own-app Keychain item. This is a personal client, not managed ReDevice admission.
The server currently returns the approving browser session JWT, not a new
device-limited grant. Local removal does not revoke that browser session.
After WSS registration, the native UI sends text or explicit three-second voice
messages to normal Conversational. It does not load the Angular website.
Optional local iOS voice reads replies; remote TTS and server-history restoration
are not implemented. Commands are never automatically queued or resent.

Native nonsecret checks run as ordinary `mobile` through the existing fixed CLI:
`onboarding-status`, `client-status`, and `client-contract-check`. The contract
check uses in-memory fixtures only, never changes real human diagnostic results,
and never creates a session. Build and device observations are recorded separately
in the private evidence linked above.

Version 0.14.6 is installed on the prepared iPad. The owner passed the local
wizard and reported voice input; an autonomous normal-UIKit text exchange now
passes server registration, accepted message and a reply assigned to this iPad.
The original missing-reply bug involved Core's `hub` sender and optional IDs.
Current replies without IDs receive a local presentation identity; command IDs
and delivery are unchanged.

The follow-up fixes restore composer controls after the keyboard hides and
separate speech start/completion/failure from mere voice availability. A real
keyboard cycle passed on 0.13.5; a fresh own-endpoint reply was synthesized
through More on 0.13.6, with platform callbacks and speaker route confirmed.
Human audibility is recorded separately in the
[focused speech evidence](../docs-development/platform/evidence/legacy-ios-chat-speech-2026-11-12.md).

`client-probe-prepare` stages one private, expiring, fixed text-message check for
the next normal foreground client launch. Close only this app before staging,
then launch it normally. `client-probe-status` reads its nonsecret result; the
embedded `client_probe.state` must be `passed`, not merely the metadata command's
`outcome`. It neither captures audio nor grants access. It requires an existing
approved client session, consumes the job once, and never retries a message.

`local-ui-probe-prepare` stages a real keyboard open/refresh/dismiss cycle and
one fixed local speech phrase for the next client launch; it sends no message
and never uses the microphone. `local-ui-probe-status` returns keyboard flags
and nonsecret speech callback/route metadata. `client-speech-probe-prepare`
instead arms one text roundtrip followed by reading the own-endpoint reply
through the same More dismissal path as the manual action. These fixed private
jobs expire and are consumed once. Speech API completion is not hearing proof.

The experimental More action “Listen for Arseniy” explicitly starts a 90-second
foreground microphone trial. Speech segments go to the existing subnet STT;
only the exact “Арсений” prefix plus a command can request arbitration and dialog
dispatch. The published alias must be unique. This is not local STT or an
offline wake detector. Stop/background/deadline cancels listening; return does
not restart it. Capture pauses while processing a phrase and reading its reply.

`activation-probe-prepare` arms one such trial for the next foreground launch;
`activation-fixture-prepare` instead sends a fixed bundled synthetic recording
without using the microphone. `activation-status` explicitly labels the source
and reports counters without transcripts/audio. The fixture route has passed
through recognition, activation, a subnet reply and local synthesis. A separate
five-second microphone capture also passed recognition, claim, dialog, own reply
and completed local synthesis after Enjoy played “Арсений проверка связи”. This
bypasses VAD; automatic acoustic segmentation is still being qualified. See the
[separate observations](../docs-development/platform/evidence/legacy-ios-voice-activation-2026-11-12.md).
`activation-recorder-prepare` compares one three-second AVAudioRecorder capture;
`activation-queue-prepare` compares one five-second AudioQueue capture without
VAD. Both use the same exact-name gate and stop after the result or reply.
Reports contain bounded route, PCM level/tone and word-class diagnostics, without
audio or transcript text. Version 0.14.6 stops capture before measuring a segment
and excludes stale overrun notifications generated during queue shutdown.
The operator helper `tools/ios_acoustic_probe.py --say "Арсений проверка связи"
--play-only --seconds 8` selects Enjoy stereo explicitly and uses local Russian
SAPI synthesis. PC input is optional and raw audio is discarded unless explicitly
requested; Windows audio defaults and volume are not changed.

Prompt regeneration is explicit:
`powershell -File tools/ios_voice_pack.ps1`. It uses local SAPI synthesis and
records text/audio hashes. Portable transition checks are in
`tests/native/test_ios_onboarding.c`; WSS adapter checks run with
`node --test tests/native/webio_transport.test.cjs` and voice assets with
`python -m pytest --noconftest tests/test_ios_voice_assets.py`.

## Implemented preparation commands

Run from the repository root with Python 3.11 or newer. The helper has no core
AdaOS dependencies and does not require installing the AdaOS Python package.
On this Windows workstation, the verified interpreter is
`.\.venv\Scripts\python.exe`; use it in place of `python` in the examples.

```powershell
python tools/jailbreak_lab.py doctor --capture-private --require-target
python tools/jailbreak_lab.py fetch
python tools/jailbreak_lab.py inspect-ipa .tmp/jailbreak/downloads/EverPwnage-v2.0.2.ipa
python tools/jailbreak_lab.py build
```

`doctor` uses the existing local Apple USB bridge on Windows. It reads only
selected public device values and known Apple backup metadata. Unlock/trust
may be needed for some values. It never requests pairing or changes trust.
`--require-target` requires exactly one matching ProductType/OS/build; it does
not authorize device operations. `--capture-private` records identifiers only
under the existing ignored `.adaos/recovery/jailbreak/` directory.

`fetch` downloads pinned artifacts to `.tmp/jailbreak/downloads/` and verifies
their SHA-256. It never executes them. A mismatching cached file is reported,
not silently replaced. Review a changed upstream release before changing the
lock; do not replace hashes just to make a mismatch pass.
The assets include EverPwnage and a Windows x64 portable Impactor, formerly
PlumeImpactor, for an owner-operated initial IPA installation. Downloading
either program does not install or execute it. Sign-in and device compatibility
remain operator checks; see the private runbook's Windows driver note.

`build` requires a running Docker Linux engine. It stages an explicit build
context containing only the Dockerfile and verified compiler archive. It
fetches pinned Theos/SDK source into the builder, then builds the application
with networking disabled. Source, intermediate files, the `.deb`, extracted
application and `build.json` receipt live under `.tmp/jailbreak/`. The command
checks that the resulting executable is ARMv7 with a compatible minimum OS.
No device is mounted into the builder and no package is installed on a tablet.
It requires at least 6 GiB free on the workspace, Windows system and registered
Docker data volumes before starting. For other Docker storage layouts, also
check the volume actually holding Docker's disk image:
the Docker image may live on C: even when this repository lives on D:.

Preparation status on 2026-11-11: device/dependency checks and 18 helper tests
have passed. Docker's WSL disks were moved to `W:\DockerData\wsl` and runtime
write/read checks passed. The earlier 10.3 SDK snapshot in the lock successfully
built the native package; inspection confirmed ARMv7 and minimum iOS 9.0.
The installed 0.1.0 `.deb` and immutable `build.json` receipt are under
`.tmp/jailbreak/output/20261110T225659279493Z/` (directory timestamps are UTC).
The owner confirmed Zebra opened after all three detached full power-off/on
trials. AdaOS Lab 0.1.0 has now been installed over pinned USB SSH; its installed
binary matches the build receipt and `uicache` completed. The owner confirmed
application launch and saved-counter retention across application restart.
The owner also confirmed launch, retained taps and a new tap after a detached
full power cycle with Wi-Fi disabled. G2 is passed for that 0.1.0 build on this
iPad; network and later endurance/expiry gates remain open.
Version 0.2.0 was built and inspected under
`.tmp/jailbreak/output/20261111T103313438381Z/`, then installed over pinned USB
SSH. The installed hash matched and saved taps remained 22. The owner reported
`HTTPS OK`, a visible error with Wi-Fi disabled, working tap controls and
`HTTPS OK` again after restoring Wi-Fi. Preserve the exact 0.1.0 and 0.2.0
packages for baseline and rollback. The website button
sends one Wi-Fi-only HEAD request to `https://inimatic.com/`, with platform
certificate checks, no redirects, cookies or credentials, and a 15-second
deadline. It checks website HTTPS, not authenticated AdaOS API access.
Version 0.3.0 adds **Check Root API**, a bounded GET of
`https://api.inimatic.com/api/ping`: HTTP 200, JSON MIME, at most 4 KiB decoded,
`ok: true`, `service: root` and a numeric timestamp are required. It keeps the
same TLS/cookie/credential/redirect/deadline boundaries; only one check runs at
a time. A Root liveness result does not establish a user/node session.
The 0.3.0 build is under `.tmp/jailbreak/output/20261111T105948165702Z/`;
its USB installation and on-device binary hash are verified, with saved taps
still 22. The owner subsequently reported **Root API OK**, a visible Wi-Fi-off
error with usable tap controls, and **Root API OK** after restoring Wi-Fi. See
`.tmp/jailbreak/adaos-lab-device-upgrade-0.3.0.json` for the sanitized receipt.
Version 0.4.0 added a non-secret Keychain check. The owner observed error
`-34018` (missing entitlement); no session was created. Version 0.5.0 signs the
app with its own application identifier and exact Keychain group, without
system groups or wildcard access. Inspection of the final binary confirmed
both embedded entitlements. The package in
`.tmp/jailbreak/output/20261111T115815105810Z/` was installed over pinned USB SSH;
the installed hash matched and saved taps remained 22. The owner reported
**Secure storage OK: test value saved, read and removed**. This qualifies the
non-secret local roundtrip; no real session was created. The temporary SSH
relay/password file were retired.

Authenticated node access required a relay correction: live route admission
differed from local API admission. The owner authorized the normal server release and
the HTTP relay correction was merged through PR #47 as
`4f45b09ba8523c827418af1f4878f691fd341c39`. PR #48 corrected a reproduced
asynchronous trace/projection race. Main revision
`8bb0530da162fa92f753eda2ac2a5144613129af` passed full qualification and was
published to RU and Global Root. The selected manual development API was restored
after the owner authorized its restart; its node identity and dev2027 mode match.
Live missing/invalid credential denial, scoped own read, other-desktop/admin
denial, revocation and real expiry all passed through the selected RU route.
The full desktop response exceeded the 64 KiB experiment cap; the existing
`/runtime` read is used for the bounded session probe.

Pinned USB SSH now uses a private administration key. Factory root/mobile
passwords were replaced and their rejection verified; recovery material remains
private. Version 0.6.1 was installed with matching binary hash and saved taps 22.
Its package is under `.tmp/jailbreak/output/20261111T130118350507Z/`.
The app previews then claims a single-session invitation for exactly one test
desktop, stores only its scoped bearer in app-local Keychain, checks that
desktop's runtime over verified HTTPS, and can remove its local session.
No owner token is sent to the tablet. Requests have a 15-second deadline per
hop, a 64 KiB decoded-body cap, no redirects/cookies/retries or TLS exceptions.
A 20-minute invitation was installed with owner mobile/mode 0600. The owner
reported the pairing/access stages passed, then reported **No readable saved
session (code -25300)** in connection with Forget. That message comes from a
Check with no saved Keychain item; the exact Forget confirmation was not quoted.
The server independently confirmed one accepted claim with a session subject.
That first invitation was then revoked on the server; its original bearer was
not extracted from the tablet. The consumed pairing profile was absent on USB
inspection. A second 20-minute invitation for the same test desktop is staged
for pairing, access, full app close/reopen and a saved-session access check.
The owner has now reported the session survives the instructed full app
close/reopen sequence. Server state independently confirms one accepted session
claim for the second invitation. Native app-reopen persistence is owner-reported
passed on 0.6.1. The owner also reported **Session access OK** returned after
the Wi-Fi interruption trial without another Pair. The exact offline error,
elapsed error time and tap-control outcome were not separately quoted.
Invitation 02 was revoked on the server before its original expiry. The owner
subsequently reported **Saved test session expired**: the app's local deadline
check stopped the request, so device-side server-revocation evidence is
inconclusive. This observes the local expiry guard; it does not establish
unrevoked server expiry from the device.
A third, 29-minute single-session invitation was used for a delayed-revoke trial.
The worker observed its single scoped claim at 13:52:42 UTC and confirmed one
revoke at 13:54:16 UTC, over 17 minutes before expiry; it has completed. The
owner reported the pre-revoke steps behaved as expected, including Forget and
initial access, then HTTP 403 about five minutes after revocation. Native
before/after server revocation and local removal followed by fresh pairing are
owner-observed passed on 0.6.1. The refusal's combined diagnostic does not
establish an actual non-JSON or oversized response.
A fourth 29-minute single-session invitation for the same desktop is staged
for a detached full power-off/on trial. Installed 0.6.1/hash were rechecked;
the read-only baseline is saved taps 22, launches 10 and a captured boot time.
The owner is asked to Forget the revoked record, Pair/Check, remember taps,
detach USB, fully power off/on, unlock, reconnect Wi-Fi and Check without Pair.
The owner subsequently reported counter and saved-session access OK after the
detached reboot. USB inspection confirmed a later boot time, saved taps 22,
launches 10 to 11 and the unchanged 0.6.1 hash. That cold-start trial passed.

Versions 0.7.0/0.7.1 add a fixed `--lab-probe` entry in the same app binary,
running as mobile (UID 501) with the same own-app Keychain entitlements. It
returns bounded JSON without bearer/profile values and accepts only seven
named checks. Root execution and unknown commands are refused; unknown argument
values are not echoed. There is no listener or arbitrary target/command input.
Version 0.7.1 also registers `adaos-lab://open` solely to open the normal UI
through the installed URL-only `uiopen`. It does not perform an action from URL
parameters. Compilation disables the archived SDK's unstable Clang module cache.
The qualified 0.7.1 package is under
`.tmp/jailbreak/output/20261111T143750906022Z/`; its installed hash and unchanged
own-app entitlements are checked.

Autonomous real-device checks passed: public HTTPS/Root JSON, Keychain roundtrip,
Pair/read/refused overwrite, real local expiry, server-revoke HTTP 403, local
Forget/absence, two normal UIKit launches with retained taps, timeout during
an API pause and saved-session recovery after a controlled actual dev API restart.
An actual `0.7.1 -> 0.7.0 -> 0.7.1` package rollback retained taps and the same
working Keychain session at every stage. Invitation 06 was then revoked and
removed locally; no saved test bearer remains. Named AdaOS Lab crash reports: 0.
An earlier unexpected API exit was recovered without determining its cause;
that episode's session expired before reconnect and is recorded as inconclusive.
G3 passes for this bounded cohort/flow. Useful chat/audio, endurance, the eight
real elapsed-day installer trial and kiosk startup remain separate work.

For a lab check over the established pinned USB SSH channel, run on the iPad:

```sh
su -c '/Applications/AdaOSLab.app/AdaOSLab --lab-probe status' mobile
su -c '/Applications/AdaOSLab.app/AdaOSLab --lab-probe session-check' mobile
```

Other fixed commands are `website-https`, `root-ping`, `storage-check`,
`session-pair`, `session-forget`, `microphone-status`, `microphone-record` and
`speaker-check`. The microphone commands were added in 0.8.0; `speaker-check`
in 0.9.0 plays a one-second synthetic tone without recording. Pair uses only the pre-installed short-lived
test invitation. A CLI result tests native client code, not visible rendering,
touch, detached power cycling or human consent. Do not replace those observations
with a process exit code.
Administration uses the pinned key through a temporary local USB relay. The
owned relay was stopped at the listening checkpoint; it can be restarted for
the next check. Details and private receipts are in the internal evidence/runbook.
The system volume has about 90 MiB available (96% used), while the data volume
has about 11.9 GiB available. No credentials belong in experiment notes.

Version 0.8.0 adds an explicit iOS microphone permission request and a bounded
local recording test. Its qualified package is under
`.tmp/jailbreak/output/20261111T150336708641Z/`; its installed hash and exact
own-app entitlements are verified, and saved taps remain 22. Before consent,
the native record command refused to start and left no WAV file. The owner
then granted the normal iOS permission dialog. A real-device capture passed:
PCM 16 kHz, mono, 16 bit, 3.00 seconds / 96,000 data bytes. The app deleted its
private test file, and a separate USB inspection found no remaining test WAV.
No audio was uploaded or read by the PC. This proves the local capture path,
not speech intelligibility, playback, remote transcription or complete G4.
The fixed permission job can only request consent when the UI becomes active;
it cannot start a recording. An expired job was consumed without requesting
permission. Native probes remain ordinary mobile-user operations, with no
permission-database edits or additional entitlements.

Version 0.9.0 was qualified under
`.tmp/jailbreak/output/20261111T152056731792Z/`, with matching binary/package
hashes and unchanged client protocol/own-app entitlements. It adds
**Record and replay locally**: an explicit UI action records three seconds,
validates PCM, plays that local sample once, then deletes it. Backgrounding,
cancellation, error and deadline paths also remove the sample. Only a fixed
result code and completion time are saved in preferences, never speech.
An autonomous native playback check completed the one-second in-memory
synthetic tone; public Root JSON/Keychain checks and normal UIKit launch also
passed, taps stayed 22 and microphone permission stayed granted. The owner
confirmed recording/playback successful. Native metadata independently records
`replay-ok` at 15:31:47 UTC; USB inspection found no remaining test WAV. Remote
chat/STT is a separate trial.

The remote microphone utterance was qualified on **0.10.0**, from
`.tmp/jailbreak/output/20261111T155635275942Z/`, with matching package/binary
hashes and the same exact own-app entitlements. It adds **Record and transcribe
on AdaOS**. After checking the scoped voice connection, this explicit UI action
captures three seconds, validates PCM, deletes the local file and sends its
in-memory WAV over verified HTTPS to the selected AdaOS node. It displays the
returned transcript. The fixed CLI commands `voice-capabilities` and
`voice-silence` qualify discovery and recognition using one second of generated
silence, with no microphone capture or transcript text in their output.

Real iPad recognition transport passed, including a cold dev API restart
(3.87-second recognition) and the same saved session. Revoking invitation 07
caused HTTP 403 for discovery and recognition; local removal/absence passed.
Invitation 08 expired before the owner trial and was reconciled and revoked.
Replacement invitation 09 passed the owner's requested short Russian utterance
through the microphone and remote recognition. Native metadata independently
recorded `recognized` at 17:38:25 UTC, with zero remaining test WAV files, taps
22 and UI launches 17. This establishes one phrase, not general recognition
accuracy. Its session expires on 2026-11-11 at **20:56 Moscow**.

An autonomous synthetic request during a bounded pause of the selected dev API
received HTTP 504 after 15.37 seconds. After resuming the same process, access
and a separate explicit recognition request succeeded with the same session.
This tests service unavailability/recovery, not an interruption midway through
upload. No microphone was used in that fault trial.

The server preview reuses desktop-session admission and existing Vosk recognition
at `/api/node/yjs/webspaces/{id}/voice/{capabilities,transcribe}`. It accepts
only bounded Russian PCM speech and returns recognition text without dispatching
dialog/actions. The correction preserves request context in worker threads and
gives the local relay 15 seconds for the handler's 12-second deadline. There are
two workers, one active request per principal, a 180 KiB JSON/128 KiB WAV cap
and a short bounded in-process result cache. It is not durable deduplication
across restarts; the client never retries a sent recognition automatically.
The preview is loaded only on the selected manual development node. Server
[draft PR #49](https://github.com/inimatic/adaos/pull/49) has 145 passing focused
checks; no main merge or global delivery was performed. Chat/scenario execution,
remote TTS, endurance, the eight-day boundary and kiosk startup remain open.

The current installed version is **0.11.0**, from
`.tmp/jailbreak/output/20261111T175012917324Z/`, binary SHA-256
`73eacf9bc7cf4d116146cbc11e72fe226ddf30916f035c1441fe8bf68a77296b`.
It adds a Cancel voice operation button and a private, single-use synthetic
UIKit lifecycle fixture. Normal URLs only open the app; the fixture never
records the microphone. Actual UIKit backgrounding cancelled a pending voice
request, foreground return did not replay it, an expired fixture caused no
request and a separate explicit synthetic request succeeded. The same session,
taps 22 and earlier owner-recognition metadata survived the upgrade. Following
the owner's reported power cycle, USB confirmed a later boot time and unchanged
binary; normal app launch increased 18 -> 19 with taps and microphone grant
retained. The old Keychain record remained but had already expired before boot.
A fresh bounded session restored native and normal UIKit synthetic recognition.
This does not prove stopping server computation after disconnect, mid-upload
interruption or retention of a still-valid session through this particular boot.

The builder may be prepared alone with:

```powershell
python tools/jailbreak_lab.py build --image-only
```

The base image, compiler and source commits are pinned. Apt packages and build
timestamps are not frozen, so identical output bytes are not promised. The
build receipt records the exact source-file and package/binary hashes.

## Bounded verification

```powershell
python -B -m pytest --noconftest tests/test_jailbreak_lab.py -q `
  -o cache_dir=.tmp/jailbreak/pytest-cache --basetemp=.tmp/jailbreak/pytest-tmp
```

`--noconftest` deliberately isolates this standard-library preparation helper
from the core integration fixtures and their runtime-service dependencies.
Passing these checks does not prove device installation, boot persistence or
successful application execution.

After a real trial, append an explicit operator observation with the executable
SHA-256 from its build receipt:

```powershell
python tools/jailbreak_lab.py record --kind cold-boot --result pass `
  --build-sha256 <exact-64-character-binary-hash> --operator <observer> `
  --note "Trial 01: full power-off, USB disconnected, counter retained"
```

This is a template, not an already completed trial. The recorder does not mark
an experiment gate as qualified. Use the private runbook for physical actions
and the evidence requirements. Neither Apple passwords nor device identifiers
belong in observation notes, source or commits.
