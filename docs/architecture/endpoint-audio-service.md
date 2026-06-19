# Endpoint Audio Service

Status: target architecture and roadmap seed.

This document defines the target audio-session layer for ReDevice agents,
browser endpoints, mobile native agents, and future endpoint devices. It
extends [Endpoint Infrastructure](endpoint-infrastructure.md) and should be
implemented as shared subnet infrastructure, not as ReDevice-specific skill
logic.

## Core Decision

Speech recognition is not the center of the voice architecture. AdaOS needs an
audio session layer before STT and after NLU:

```text
audio endpoint
  -> activation and capture
  -> transport selection
  -> EndpointAudioService
  -> STT or transcript source
  -> Voice/NLU/Dialog routing
  -> optional response route
  -> audio/display endpoint
```

The ReDevice Agent is an audio endpoint. It should not become a full voice
assistant, a local LLM host, or a mandatory STT runtime. Legacy ReDevice devices
should capture audio, run cheap activation or PTT, and stream or upload bounded
utterances to a member or hub-hosted service.

## Scope

`EndpointAudioService` owns:

- audio session lifecycle;
- activation strategy;
- audio diagnostics and endpoint profiling;
- audio transport negotiation;
- STT backend routing;
- transcript events;
- dialog/dictation session ownership;
- response routing;
- retention and privacy enforcement.

It does not own:

- deterministic NLU dispatch;
- skill business logic;
- Yjs projections;
- ReDevice-specific settings UX;
- local Android/iOS Bluetooth implementation details.

## Endpoint Audio Capabilities

Endpoint Registry should model audio capabilities as facts and live state:

```json
{
  "endpoint_id": "endpoint:redevice:tf201-01",
  "services": {
    "audio_input_endpoint": {
      "state": "ready",
      "sample_rates_hz": [8000, 16000],
      "formats": ["pcm16"],
      "activation": ["ptt", "vad"],
      "local_stt": {"state": "unavailable"}
    },
    "audio_output_endpoint": {
      "state": "ready",
      "routes": ["built_in_speaker", "bluetooth_a2dp"],
      "volume_control": "best_effort"
    },
    "bluetooth_audio_endpoint": {
      "state": "assisted",
      "profiles": ["a2dp", "hfp_sco"],
      "preferred_output": "speaker:kitchen"
    }
  }
}
```

Capabilities do not grant permission. EndpointPolicy must still authorize
microphone use, cloud STT, retention, response playback, Bluetooth assistance,
and continuous activation.

## Audio Session Types

The service must distinguish session purpose before routing transcripts:

- `command_session`: short utterance intended for normal AdaOS NLU dispatch.
- `dialog_session`: user asks for advice, information, or multi-turn help.
- `dictation_session`: user is building a text buffer.
- `audio_debug_session`: diagnostics, benchmark, or operator inspection.

Session owner is explicit:

```text
session_owner = node_id:skill_id
```

Different nodes may own different sessions for different endpoints. A skill can
subscribe to the session it owns, but it should not take raw audio ownership
outside policy.

## Activation Strategy

Activation is not the same thing as VAD. VAD detects speech; it does not prove
the user is addressing AdaOS.

Supported strategies:

- `ptt`: record only while a screen or hardware button is held.
- `vad`: start when speech-like audio is detected, with pre-roll buffer.
- `wake_word`: optional wake detector on capable endpoints.
- `device_name`: route if transcript begins with an exposed endpoint alias.
- `active_mode`: endpoint is already assigned to an active voice/dictation
  session.
- `hybrid`: combine PTT, VAD, wake word, endpoint name, or UI mode.

Legacy Android ReDevice MVP should use:

```text
ptt or vad
  -> 0.5s to 1.5s pre-roll
  -> record until silence or max duration
  -> segment upload or chunked stream
```

Local Vosk or another on-device STT engine is optional and must pass a local
benchmark before it can be treated as ready.

## Audio Front-End

The target audio front-end should expose diagnostics even before high-quality
processing exists:

- input level and clipping;
- noise floor and SNR estimate;
- silence/speech decision evidence;
- sample rate and channel conversion;
- latency estimate;
- speaker output route;
- echo risk when the endpoint is both listening and playing audio;
- battery and thermal pressure during capture.

Modern endpoints should support or delegate:

- echo cancellation;
- noise suppression;
- automatic gain control;
- jitter buffer and packet loss evidence for realtime streams.

Legacy endpoints may only report simplified metrics. That is acceptable if the
service records degraded quality and chooses safer routing.

## Bluetooth Audio

Bluetooth should be modeled as an assisted audio route, not as a guaranteed
remote-control surface.

Important profile differences:

- `a2dp`: good speaker output, usually no microphone.
- `hfp_sco`: microphone support, lower audio quality.
- `built_in_mic_plus_a2dp`: often the practical best mode for an old tablet
  used as a room terminal.

Target behavior:

- show Bluetooth state and preferred device;
- open native Bluetooth settings when direct control is not available;
- remember preferred output/input route;
- run speaker and microphone tests after route changes;
- attempt reconnect only when policy and platform allow it;
- report best-effort failures without pretending the subnet controls the
  physical pairing stack.

## Transport Ladder

Audio and media bytes must not be transported through Yjs.

Recommended ladder:

1. `webrtc_p2p`: preferred for modern Android, iOS, and browser endpoints.
2. `local_ws_chunked`: direct WebSocket chunks to hub or member.
3. `local_http_chunked`: direct progressive upload/download.
4. `segment_upload`: short utterance segments for legacy devices.
5. `endpoint_poll`: degraded control/events only, not a preferred media path.
6. `root_relay`: emergency fallback with strict policy and size limits.

Every stream or upload must carry:

- session id;
- sequence number;
- content type;
- sample format;
- timestamps;
- final/partial marker;
- ack or completion status;
- transport evidence.

## STT Routing

STT is a routed backend decision:

```text
local_stt if installed and benchmark_passed
else member local STT if available
else cloud STT if policy allows
else transcript unavailable with visible reason
```

The policy must distinguish:

- local-only;
- cloud allowed;
- cloud denied;
- debug recording allowed;
- no retention;
- language hints;
- custom vocabulary or phrase hints.

Phrase hints should include active endpoint names, node names, skill names,
scenario names, app names, ReDevice aliases, and local named entities. Partial
transcripts are useful for UI/debug but must not dispatch mutating actions.

## Dialog And Dictation

`EndpointAudioService` routes final transcripts to one of three layers:

- normal Voice/NLU for command sessions;
- dialog runtime or cloud LLM for advice and conversational help;
- text-buffer service for dictation.

Dictation session example:

```text
"Ada, listen"
  -> dialog_session starts
"write this down ..."
  -> text_buffer.append
"what do you think?"
  -> dialog LLM over current buffer
"add it to notes"
  -> skill action with preview/confirmation policy
```

The active buffer and dialog state must be explicit. They should not be inferred
from unstructured chat history.

## Turn-Taking And Barge-In

The response path is part of the session:

- `audio_output_endpoint`;
- `display_endpoint`;
- browser UI;
- notification or Telegram route;
- text buffer only.

If speech starts while the system is playing an answer, the session should
record an interruption event and the response route should stop or duck output
when supported. This is a dialog policy decision, not an STT decision.

## Multi-Endpoint Arbitration

When several endpoints hear the same utterance, the subnet should avoid
duplicate dispatch:

- group candidate utterances by time window and phrase similarity;
- prefer the endpoint with better SNR, lower latency, explicit PTT, active
  assignment, or addressed name;
- keep rejected endpoint evidence for diagnostics;
- never let two endpoints dispatch the same mutating command independently.

## Contracts

Initial contracts:

- `endpoint-audio-profile.v1`: endpoint audio capabilities, routes, benchmark
  status, and quality metrics.
- `audio-session.v1`: session id, owner, endpoint, mode, policy, state, and
  routing.
- `endpoint-audio-events.v1`: current MVP wire schema for
  `endpoint.audio.record_button`, `endpoint.audio.voice_activity`,
  `endpoint.audio.segment`, and `endpoint.audio.transcript`.
- `audio-chunk.v1`: bounded audio frame or segment metadata.
- `speech-event.v1`: activation, speech start/end, silence, interruption, and
  diagnostics.
- `transcript.v1`: partial/final transcript, backend, confidence, language,
  timing, and policy evidence.
- `dialog-session.v1`: command/dialog/dictation state and active response
  route.
- `text-buffer.v1`: dictation buffer identity, content revisions, and target
  owner.

These contracts should be independent from Android, iOS, browser, or ReDevice
implementation details.

## SDK Surface

Target SDK:

```text
sdk.endpoint_audio.list_profiles(filters)
sdk.endpoint_audio.start_session(endpoint_ref, mode, options)
sdk.endpoint_audio.stop_session(session_id)
sdk.endpoint_audio.subscribe_transcripts(session_id, options)
sdk.endpoint_audio.get_session(session_id)
sdk.endpoint_audio.set_response_route(session_id, route)
sdk.endpoint_audio.append_text_buffer(session_id, text)
sdk.endpoint_audio.get_diagnostics(endpoint_ref)
```

Skills should receive transcript/session events, not raw transport-specific
chunks unless they explicitly own an audio-processing role and policy allows it.

`redevice_voice` is the first debug/control skill over this SDK. It should show
endpoint state, activation mode, current session, STT backend, last transcript,
latency, and the last bounded debug clips. It should not own the general audio
transport or STT architecture.

## Current MVP Implementation

The current implementation provides a stable base layer, not the full target
transport stack:

- `adaos.services.endpoint_audio` receives endpoint audio events, stores only
  the last 10 debug WAV clips, runs member-side Vosk when a model is available,
  and dispatches final transcripts into the normal Voice/NLU route.
- `adaos.sdk.io.endpoint_audio` exposes reusable skill-facing helpers:
  `build_capture_command`, `process_endpoint_audio_event`,
  `endpoint_audio_diagnostics`, `endpoint_audio_policy`, and compact endpoint
  projection.
- Legacy Android ReDevice uses `audio.capture.vad` as the default path:
  sound activation, pre-roll, silence cutoff, bounded segment upload, and no
  required local STT/TTS.
- Push-to-talk remains available as deterministic fallback through on-screen
  and hardware controls.
- `redevice_voice` is a diagnostic skill over the service. It can tune VAD
  thresholds and shows VAD state, STT status, last segment, retention, and
  transport. It can also verify that the latest audio segment is an actual
  readable content artifact, including WAV metadata.
- `redevice_settings` surfaces the endpoint audio, Bluetooth, display, battery,
  active app, subnet, and logout/reconnect controls through Endpoint Registry.

The service state uses `endpoint-audio-diagnostics.v1` as a runtime snapshot:

```json
{
  "schema_version": "endpoint-audio-diagnostics.v1",
  "mode": "voice_activity",
  "vad": {"state": "listening"},
  "record_button": {},
  "last_segment": {},
  "stt": {"state": "model_missing"},
  "retention": {"debug_clip_limit": 10, "stored_debug_clips": 0},
  "transport": {"selected_transport": "segment_upload"},
  "policy": {"microphone_allowed": true}
}
```

`endpoint-audio-content-check.v1` is the first diagnostic content-verification
contract. It does not transcribe or dispatch by itself. It verifies the current
policy projection, selected audio transport, retention settings, and the latest
segment artifact:

```json
{
  "schema_version": "endpoint-audio-content-check.v1",
  "state": "ready",
  "bytes": 32768,
  "sample_rate": 16000,
  "channels": 1,
  "duration_ms": 1024,
  "policy": {"microphone_allowed": true},
  "transport": {"selected_transport": "segment_upload"}
}
```

The debug skill may expose this check, but the reusable owner is
`EndpointAudioService` / SDK. Failed checks should produce actionable states
such as `missing_segment`, `segment_file_missing`, `invalid_wav`, or
`wav_parse_failed`.

## MVP Slice

Recommended MVP:

1. `[done]` Extend Endpoint Registry with `audio_input_endpoint`,
   `audio_output_endpoint`, and `endpoint-audio-profile.v1`.
2. `[done]` Implement legacy ReDevice PTT/VAD capture with pre-roll and max
   duration.
3. `[done]` Store only the last 10 debug utterance files when policy allows.
4. `[done]` Upload short utterance segments to a hub/member endpoint audio
   service.
5. `[done]` Route final transcript to the existing Voice/NLU pipeline when STT
   produces text.
6. `[done]` Verify latest audio-in segment content from a diagnostic skill
   through SDK, without giving the skill ownership of raw transport.
7. `[next]` Return optional response to display or speaker route.
8. `[done]` Show diagnostics in `redevice_voice` and `redevice_settings`.

Do not make local STT mandatory for legacy Android 4.1. Treat Vosk and similar
engines as optional `local_stt` profiles that must pass benchmark gates.
