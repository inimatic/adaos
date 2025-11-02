# I/O Overview

This section describes AdaOS I/O routing and integrations.

- Outgoing UI events: skills publish `ui.notify` and `ui.say`. The RouterService reads `.adaos/route_rules.yaml` and delivers to targets by `io_type` (e.g., `stdout`, `telegram`). Multiple targets can be active; the router attempts each configured target.
- Telegram integration (root backend): receives webhooks, resolves target hub by alias/session/reply/topic, publishes into the bus (NATS) as `io.tg.in.<hub_id>.text`. Outgoing replies are consumed from `io.tg.out` and delivered to Telegram, prefixed with `🔹[alias]`.
- NATS subjects:
  - `io.tg.in.<hub_id>.text` — inbound text from Telegram to a subnet.
  - `io.tg.out` — outbound messages from subnets to Telegram.

See `io/telegram.md` for full Telegram user flow and commands.

