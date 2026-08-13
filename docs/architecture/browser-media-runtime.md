# Browser Media Runtime

## Purpose

Browser media is a host capability, not widget-local state. A page may render
the persistent header voice control, a transient Voice modal, Chat, WebRTC,
camera/CV, QR scanning, diagnostics, and media playback at the same time. Those
surfaces must not independently decide who owns the microphone, camera, or
assistant output.

The client therefore exposes two root-scoped runtimes:

- `BrowserMediaRuntimeService` owns browser input leases, the speech/audio
  output queue, duplicate-output suppression, and long-running media-output
  activity;
- `BrowserVoiceRuntimeService` owns voice-controller selection and the desired
  continuous-listening state.

The services are shared by stationary-node browsers and by the same hosted
client connected to an Android node through zone LO. There is no mobile fork of
the browser media model.

## Input Ownership

Every capture consumer requests a lease with an owner, purpose, input kind,
constraints, priority, and preemption hint. The runtime publishes
`adaos.browser.media_runtime.v1` with logical leases and physical sources.

```text
Voice / WebRTC / CV / QR / diagnostics
                  |
                  v
        BrowserMediaRuntimeService
          | audio | video | logical Web Speech
          v       v       v
        browser-owned physical sources
```

Compatible audio or video requests share one physical source and receive
cloned tracks. Releasing one consumer does not stop the source while another
lease remains. Browser SpeechRecognition has no exposed `MediaStream`, so it
uses a `logical-audio` lease; this still makes its ownership and lifetime
visible to diagnostics.

The first implementation intentionally does not preempt incompatible
constraints. Such requests remain separate visible physical sources. Priority
and `preemptible` are recorded for a later policy gate; they must not be
described as enforced arbitration yet.

The current adapters are:

- browser SpeechRecognition and Hub WAV STT;
- WebRTC microphone/camera acquisition;
- CV sessions and QR scanning;
- browser media settings, permission checks, input tests, and loopback probes.

Only the media runtime calls `getUserMedia` on these active paths. The lower
level preferences service retains private fallback helpers for isolated use and
tests, while application call sites supply runtime-owned streams.

## Voice Controller Ownership

Rendering a `ui.voiceInput` no longer creates an independent continuous voice
runtime. Controllers register with the singleton voice coordinator. The
persistent header controller has higher priority than modal controllers, so
opening or closing Voice cannot create a second recognizer or destroy the
page's listening intent.

```text
header voice controller (priority 100) ----+
                                            +--> one selected controller
Voice modal controller (priority 50) ------+        + desired mode
```

Continuous listening is held by the coordinator until an explicit stop. When
the selected controller disappears, the coordinator transfers ownership to
the next eligible controller and records the handoff. Widget-local STT state is
reported back to the singleton snapshot instead of being treated as global
truth.

This is browser-session ownership. Native Android foreground capture and
stationary endpoint audio remain separate endpoint owners and continue to use
the shared node voice policy and room arbitration contracts.

## Output Ownership And Duplicate Suppression

Chat and Voice submit browser TTS or routed audio to one serialized output
queue. They use the canonical dialog response id as
`dialog-response:<message_id>`. If both projections observe the same assistant
message, only the first output is rendered. A widget no longer calls global
`speechSynthesis.cancel()` when it starts or is destroyed, so it cannot cancel
another surface's response.

Media elements use explicit output-activity leases. Library audio/video and a
remote WebRTC preview are visible in the runtime snapshot for their complete
playback lifetime, independently of the short TTS queue.

The runtime deduplicates outputs inside one browser page. Cross-device output
selection still belongs to `voice_output_owner` and room arbitration; browser
deduplication is not a substitute for the node contract.

## Compact Communication Informer

The header renders a 30 px graphical channel button (28 px in the narrow
profile), with a state dot and a small contention/degradation badge. It does
not repeat the full route in the already crowded header.

Opening the button presents a viewport-owned Ionic overlay. Its first row is a
graphical route:

```text
microphone -> selected assistant -> selected output
```

The detail rows expose listening intent, selected controller, STT state,
logical leases, physical sources, queued output, long-running media outputs,
node listening policy, room arbitration, controller handoffs, and the last
error. The overlay is attached to the application overlay root; a fixed element
inside the transformed header is forbidden because it is clipped to the header
containing block.

## Skill And Scenario Compatibility

Skills keep their current declarative WebUI ABI. They request `ui.voiceInput`,
`ui.chat`, `media.cvCamera`, or media-player surfaces; they do not receive a
second media implementation and must not call raw browser capture APIs from
skill code.

The 2026-08-13 audit covered all 46 installed skills on the development node.
The relevant combinations were:

- `voice_chat_skill` declares both an auto-start activation-mode Voice input
  and an auto-speaking Chat projection. The singleton voice controller and
  response-id output deduplication resolve the previous double ownership;
- `cv_descriptor` keeps its durable CV session semantics while camera access is
  leased through the media runtime;
- media center/server surfaces use runtime-owned WebRTC input and explicit
  media-output activity;
- QR and browser media diagnostics are host surfaces and use the same lease
  path;
- the remaining installed skill WebUI declarations do not access microphone or
  camera APIs directly.

Consequently no skill manifest fork or Android-specific skill version is
required. A future skill that ships arbitrary browser JavaScript must pass a
separate capability review and use the public media lease contract rather than
`getUserMedia` or `speechSynthesis` directly.

## Verified Evidence

The implementation was verified locally on Windows with Chrome 150:

- TypeScript application compilation passed;
- the complete client suite passed: 1059 tests;
- the Ionic production build passed;
- the targeted media/voice/WebRTC/widget suite passed: 211 tests;
- a live development client connected to the local AdaOS node rendered one
  header voice widget and one communication informer;
- CDP measured the informer at 30 x 30 px on a 1424 x 749 viewport and 28 x 28
  px on a 360 x 780 mobile viewport;
- the detail overlay measured 620 x 545 px on desktop and 328 x 563 px on the
  mobile viewport, with no horizontal or vertical content overflow.
- opening the real Voice application produced two registered controllers but
  retained `header_voice_input` as the single owner; closing the modal returned
  to one controller with the same owner, `handoffCount=0`, one active input
  lease, and one physical audio source.

This proves the shared browser client on the stationary development node. It
does not yet prove microphone handoff or duplicate-output behavior in Android
Chrome on the physical phone; that gate requires publishing/deploying the
client revision and repeating the Voice-modal sequence on the device.

## Next Gates

- publish the client revision through its normal CI/CD path and repeat the
  desktop/Voice-modal/return sequence in Android Chrome;
- prove that one assistant response is rendered once when both Voice and Chat
  projections are mounted;
- exercise Voice plus WebRTC and CV plus QR combinations, and define explicit
  preemption for incompatible capture constraints;
- add permission-revocation, track-ended, device-disconnect, and page-visibility
  recovery tests;
- decide whether browser output interruption/barge-in belongs in this runtime
  after a trustworthy audio render reference exists.
