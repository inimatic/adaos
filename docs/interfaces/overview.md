# I/O Overview

This section describes AdaOS I/O routing and integrations.

## Outgoing events

- Outgoing UI events (legacy): skills publish `ui.notify` and `ui.say`. The RouterService reads `.adaos/route_rules.yaml` and delivers to targets by `io_type` (e.g., `stdout`, `telegram`).
- Outgoing Web IO events (webspaces): skills/tools can publish `io.out.say`
  and, for compatibility, `io.out.chat.append`. Ordinary user-visible dialog
  should go through the conversation SDK (`adaos.sdk.chat`) so the
  RouterService records the node-local ledger and projects a bounded tail into
  `data.dialog.visible_tail` for browser surfaces selected by
  `_meta.webspace_id`:
  - `chat.send(...)` / response envelopes -> conversation ledger -> `data.dialog.visible_tail`
  - `io.out.chat.append` -> compatibility chat projection for legacy Voice surfaces
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

See `docs/interfaces/telegram.md` for the Telegram user flow and commands.
