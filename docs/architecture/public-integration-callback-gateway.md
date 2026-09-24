# Public Integration Callback Gateway

## Status and scope

This document defines the target route for external services which must call an
AdaOS installation that is not directly reachable from the public Internet.
OAuth redirect callbacks are the first use case. Provider webhooks may reuse the
ingress and routing infrastructure later, but they have different delivery and
retention semantics and are not part of the first implementation.

The current Gmail development callback remains unchanged:

```text
http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback
```

It is the correct route for local development while the public gateway is not
implemented. Skills and Applications must not invent tunnels or alternative
ports.

## Canonical public route

The target provider-registered redirect URI is stable and independent of an
Application, account, subnet, node, or current physical route:

```text
https://integrations.inimatic.com/v1/oauth/callback/{provider_id}
```

For Gmail the initial value is:

```text
https://integrations.inimatic.com/v1/oauth/callback/google.gmail
```

Provider paths are owned by AdaOS Core. Applications and skills request a
callback by logical provider reference; they do not concatenate this URL.

## Authority boundary

The public gateway owns only Internet ingress and short-lived rendezvous:

- TLS termination, provider path admission, size and rate limits;
- lookup of a one-time callback transaction from an opaque `state` value;
- delivery to the exact admitted Root, subnet and node route;
- replay prevention, expiry, acknowledgement and redacted audit evidence;
- a neutral success/error page for the browser.

The gateway does not own:

- OAuth client secrets, refresh tokens or access tokens;
- the provider account or Application attachment;
- provider-specific business operations;
- selection of another node when the admitted target is unavailable;
- a general public proxy into the subnet.

The target AdaOS Core/provider adapter remains the credential authority. It
validates the callback transaction and exchanges the authorization code for
tokens locally. Long-lived credentials remain in the Core-owned credential
vault.

## Initiation and completion

```text
Application
  -> provider adapter: begin_authorization(application_ref, return_intent)
  -> Core callback broker: create one-time transaction
  -> Root ingress registry: admit hashed rendezvous and exact route
  -> provider authorization URL with opaque state

provider
  -> integrations.inimatic.com callback
  -> validate provider/path/state/expiry/replay
  -> encrypted route delivery to exact subnet/node
  -> local provider adapter validates transaction and exchanges code
  -> Core records connected account and emits completion
  -> gateway renders neutral result page
```

The callback transaction has a stable contract such as:

```yaml
schema: adaos.integration.callback_transaction.v1
transaction_ref: callback-transaction:<opaque-id>
provider_ref: provider:google.gmail
route:
  root_zone: ru
  subnet_ref: subnet:<opaque-id>
  node_ref: node:<opaque-id>
application_ref: application:<id>
subject_ref: user:<id>
return_intent: application.connection.refresh
state_hash: sha256:<digest>
pkce_challenge: <value>
issued_at: <timestamp>
expires_at: <timestamp>
max_deliveries: 1
```

Only the minimum routing projection is registered publicly. The local record
may contain the PKCE verifier and local correlation data; the public record must
not. Raw `state`, authorization codes, client secrets, tokens and provider error
descriptions must not be written to request logs, traces, telemetry or model
context.

## Routing transport

Root already acts as rendezvous for an outbound-connected hub. The gateway
should deliver a compact encrypted callback envelope through the existing
scoped route family, for example:

```text
route.v2.to_hub.<hub_id>.<callback_key>
```

The concrete NATS subject is transport metadata, not part of the skill-facing
contract. A dedicated callback route kind must enforce:

- exact target hub/subnet/node binding from the admitted transaction;
- authenticated Root-to-hub delivery with an audience-bound envelope;
- one successful delivery and idempotent duplicate acknowledgement;
- a short deadline compatible with provider authorization-code expiry;
- no fallback to another tenant, subnet or node;
- encrypted sensitive fields and redacted operational diagnostics;
- an acknowledgement proving local acceptance, not merely NATS publication.

If the target is offline, the gateway may retain an encrypted envelope only for
a small bounded TTL. It must not exchange the code centrally or silently attach
another connection. On expiry it shows a retryable failure and the user starts
a new authorization transaction.

## Skill-facing API

Core should expose one callback broker SDK instead of provider-specific public
route code in every skill:

```python
callback = integrations.begin_callback(
    provider_ref="provider:google.gmail",
    application_ref=application_ref,
    return_intent="application.connection.refresh",
)
```

The selected `EnvironmentProfile` decides the materialization:

- `local-development` returns the current loopback callback;
- `public-connected` returns the canonical `integrations.inimatic.com` route;
- an offline profile fails closed with an actionable explanation.

Provider packages declare a logical callback profile, allowed response fields,
TTL, PKCE policy and completion handler. Applications reference the capability
and provider binding; they never declare a host, subnet route, OAuth secret or
callback handler.

This makes callback routing reusable across skills while preserving CBS
separation: the portable capability describes the integration semantics, the
binding selects the provider adapter, and the local BindingInstance supplies the
route and credential authority.

## Security invariants

1. `state` is high-entropy, single-use, time-bounded and stored only as a hash
   in public authority.
2. The transaction is bound to provider, subject, Application, route and return
   intent; none may be changed at callback time.
3. PKCE is required where the provider supports it. The verifier remains local.
4. Authorization codes and tokens never appear in URLs after ingress, logs,
   telemetry, browser local storage, YJS documents or model context.
5. The callback is not accepted unless both public and local transaction records
   agree and the target node acknowledges it.
6. Completion is idempotent. Replay produces the same redacted terminal result
   and cannot repeat token exchange or attachment.
7. Application attachment remains an explicit, separately authorized operation;
   completing OAuth does not implicitly grant every Application access.
8. Gateway records are deleted after completion or expiry. Durable audit stores
   contain only digests, timestamps, route class and redacted outcomes.

## Delivery plan

1. Define and validate `callback_transaction`, routed callback envelope,
   acknowledgement and redacted audit schemas.
2. Add the Core callback broker with loopback materialization, keeping the Gmail
   route and behavior unchanged.
3. Implement the Root ingress registry and the TLS endpoint on
   `integrations.inimatic.com` with replay/TTL/rate-limit tests.
4. Add authenticated encrypted delivery over the existing outbound hub route and
   explicit offline/expired behavior.
5. Pilot public materialization with the existing Gmail provider while retaining
   loopback as the development profile.
6. Move all provider adapters to the Core SDK and prohibit literal public
   callback URLs in skill/application manifests.
7. Add Applications UI for callback route health, pending transaction expiry,
   connected-account ownership and explicit per-Application attachment.
8. Only after OAuth is stable, define a separate durable webhook subscription
   contract on the same ingress infrastructure.

## Acceptance criteria

- one provider-registered public URI works for every admitted subnet without
  exposing subnet or node identifiers;
- the external provider can complete OAuth while the node has only an outbound
  Root connection;
- the code-to-token exchange and all long-lived credentials stay on the target
  Core;
- wrong-provider, wrong-route, expired, replayed and offline callbacks fail
  closed with redacted evidence;
- two Applications can explicitly attach the same Core-owned connected account
  without copying its credentials;
- changing the physical Root/hub route does not change Application semantics or
  the provider-registered callback URI.
