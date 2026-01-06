# I/O Overview

This section describes AdaOS I/O routing and integrations.

## Outgoing events

- Outgoing UI events (legacy): skills publish `ui.notify` and `ui.say`. The RouterService reads `.adaos/route_rules.yaml` and delivers to targets by `io_type` (e.g., `stdout`, `telegram`).
- Outgoing Web IO events (webspaces): skills/tools can publish `io.out.chat.append` and `io.out.say`. The RouterService projects these into the Yjs doc selected by `_meta.webspace_id`:
  - `io.out.chat.append` -> `data.voice_chat.messages`
  - `io.out.say` -> `data.tts.queue`

## Telegram integration

- Receives webhooks, resolves target hub by alias/session/reply/topic.
- Publishes into the bus (NATS) as `tg.input.<hub_id>` (modern envelope).
- For legacy consumers we mirror text to `io.tg.in.<hub_id>.text`.
- Outgoing replies are consumed from `tg.output.<bot_id>.>` (modern) or `io.tg.out` (legacy) and delivered to Telegram.

### NATS subjects

- Modern inbound: `tg.input.<hub_id>` - envelope `{ event_id, kind: 'io.input', ts, payload, meta }`.
- Legacy inbound (text-only mirror): `io.tg.in.<hub_id>.text`.
- Modern outbound: `tg.output.<bot_id>.chat.<chat_id>`.
- Legacy outbound: `io.tg.out`.

See `docs/io/telegram.md` for the Telegram user flow and commands.
