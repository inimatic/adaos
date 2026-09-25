# External Integration Route Inventory

Status: compatibility inventory for EIG0. Last reviewed: 2026-09-25.

This inventory classifies callable routes; it does not make legacy routes part
of the canonical ingress authority. The canonical model is defined in
[External Integration Ingress And Public Callback Gateway](public-integration-callback-gateway.md).

| Route | Class | Current authority | Credentials | Retention / acknowledgement / retry | Migration |
| --- | --- | --- | --- | --- | --- |
| Core `GET /api/providers/google/gmail/oauth/callback` | `oauth.authorization_response` | Core `IntegrationIngressBroker`; Gmail is completion adapter | OAuth client and tokens remain in Core vault | hashed state, ten-minute TTL, single use; immediate local acceptance; no code retry | retained as the `local-development` compatibility materialization |
| Root `GET /v1/oauth/callback/{callback_profile_id}` | `oauth.authorization_response` | Root registry admits a hashed attempt and exact hub route; Core broker remains attempt authority | Root has no client secret or token; it receives a transient code and immediately encrypts it to a Core public key | Redis TTL bounded by the attempt; atomic delivery claim; exact outbound route and local HTTP acknowledgement; encrypted retry only before expiry | canonical `public-connected` materialization |
| Root `POST /io/tg/{bot_id}/webhook` | `webhook.event` | Telegram IO adapter and configured bot secret | Root-side Telegram webhook secret | provider signature/secret check and IO-specific delivery behavior; not yet an `IngressDelivery` | EIG5 candidate; do not model as OAuth attempt |
| Root `POST /v1/github/core_update/callback` | `webhook.event` | Root core-update service | Root-side GitHub webhook secret | raw-body signature check; immediate fan-out; current durability/retry is service-specific | EIG5 inventory only |
| Vendored Rasa Webex/Twilio webhook routes | `webhook.event` | optional isolated Rasa channel runtime | channel-specific configuration | framework-specific synchronous behavior; no AdaOS durable acknowledgement | deferred until the channel is an admitted provider |

No active AdaOS route was found that needs to be classified as
`provider.challenge` or `async.continuation`. A new route in either class must
declare an `IngressProfile` before it is exposed.

Portable contracts may contain an `ingress-profile:` reference and required
guarantees. They may not contain a literal public callback/webhook URL, a hub,
subnet or node id, or a physical `route.v2.to_hub.*` subject. The canonical
portable-record validator enforces this boundary.
