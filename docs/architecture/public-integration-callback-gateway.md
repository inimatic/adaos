# External Integration Ingress And Public Callback Gateway

Status: target architecture. OAuth redirect materialization is the first
delivery slice; durable webhooks and other callback classes follow only after
the OAuth slice is proven.

Last reviewed: 2026-09-25.

Roadmap: [External Integration Ingress Roadmap](public-integration-callback-gateway-roadmap.md).
Compatibility inventory: [External Integration Route Inventory](external-integration-route-inventory.md).

## Decision

AdaOS standardizes the control plane for callbacks, not one universal callback
protocol. `ExternalIntegrationIngress` owns endpoint registration,
materialization, routing, verification policy, delivery evidence and lifecycle.
OAuth redirects, webhooks, provider challenges and asynchronous continuations
remain distinct ingress classes because they have different authority,
retention, acknowledgement and retry semantics.

A callback URL is not an architectural identity. It is an environment-specific
materialization of an admitted `IngressEndpointRevision`:

```text
public_uri = materialize(IngressEndpointRevision, EnvironmentProfile)
```

Applications and skills never construct this URI, use it as authority, or
persist physical subnet and node routes in portable artifacts.

The current Gmail development callback remains valid while the public gateway
is not implemented:

```text
http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback
```

It is the `local-development` materialization of the first OAuth ingress
profile. Skills and Applications must not invent tunnels, callback ports or
alternative public routes.

## Ingress Classes

| Class | Identity and lifecycle | Verification | Delivery and acknowledgement |
| --- | --- | --- | --- |
| `oauth.authorization_response` | one short-lived `CallbackAttempt` initiated locally | exact profile and issuer, high-entropy `state`, PKCE where supported, expiry and replay check | immediate single-use delivery; local code exchange; neutral browser result |
| `webhook.event` | long-lived `WebhookSubscription` and repeated `IngressDelivery` records | provider signature or other admitted verifier, timestamp window, event type and delivery identity | durable at-least-once acceptance, deduplication, retry and dead-letter policy |
| `provider.challenge` | bounded endpoint-registration or ownership challenge | provider-specific challenge profile | synchronous deterministic response or fail closed |
| `async.continuation` | one bounded external operation correlated with a local attempt | signed/opaque correlation, expected provider and response schema | single-use or explicitly bounded retry according to profile |

New classes may reuse the ingress registry, TLS edge, rate limiting, encrypted
route delivery and redacted evidence. They do not inherit OAuth or webhook
semantics implicitly.

## Canonical Model

### IngressProfile

`IngressProfile` is a portable, immutable description of the protocol and the
requirements that an environment must satisfy. Its minimum contents are:

```yaml
schema: adaos.integration.ingress_profile.v1
profile_ref: ingress-profile:oauth.authorization-code.google@1
ingress_class: oauth.authorization_response
protocol: oauth2.authorization_code
issuer_ref: issuer:google
request_schema_ref: schema:oauth.authorization-response@1
verification:
  state: required
  pkce: S256
  issuer_binding: required
delivery:
  mode: immediate_single_use
  acknowledgement: local_acceptance
retention:
  ttl_seconds: 600
response:
  mode: neutral_browser_result
compatibility:
  major: 1
```

The profile describes guarantees and policies. It contains no tenant, account,
Application, subnet, node, callback host, credential or secret.

### IngressEndpoint

`IngressEndpoint` is a stable local identity. An immutable
`IngressEndpointRevision` materializes one profile in one environment:

```yaml
schema: adaos.integration.ingress_endpoint_revision.v1
endpoint_ref: ingress-endpoint:<opaque-id>
revision: 3
profile_ref: ingress-profile:oauth.authorization-code.google@1
environment_profile_ref: environment-profile:public-connected@1
zone_id: ru
callback_uri: https://ru.integrations.inimatic.com/v1/oauth/callback/cbp_<opaque-id>
provider_registration_ref: provider-registration:<opaque-id>
route_binding_ref: ingress-route:<opaque-id>
credential_authority_ref: credential-authority:core-local
generation: 7
status: active
```

`endpoint_ref` is stable; revision, route, health and generation are not
silently mutated. An exact endpoint revision is admitted before use and can be
pinned by a local binding or activation record.

The public endpoint identifier is not a secret. It reveals no tenant, subject,
Application, skill, subnet or node. Those values are resolved from admitted
server-side records.

### CallbackAttempt

`CallbackAttempt` represents one locally initiated request/response exchange.
For OAuth it contains at least:

```yaml
schema: adaos.integration.callback_attempt.v1
attempt_ref: callback-attempt:<opaque-id>
endpoint_ref: ingress-endpoint:<opaque-id>
endpoint_revision: 3
provider_connection_ref: provider-connection:<opaque-id>
binding_instance_ref: binding-instance:<opaque-id>
application_ref: application:<id>
subject_ref: user:<id>
return_intent: application.connection.refresh
state_hash: sha256:<digest>
issued_at: <timestamp>
expires_at: <timestamp>
max_deliveries: 1
status: pending
```

The local record may hold a PKCE verifier and local correlation data in the
credential authority. The public rendezvous receives only the minimum route
projection. Raw `state`, authorization codes, secrets, tokens and provider
error payloads must not enter request logs, traces, telemetry, YJS state or
model context.

### WebhookSubscription And IngressDelivery

A webhook is not represented as a repeating `CallbackAttempt`.
`WebhookSubscription` binds a provider-side subscription to an exact endpoint
revision, verifier and allowed event set. Every accepted request creates an
immutable `IngressDelivery` with provider delivery identity, digest, receive
time, verification outcome, acknowledgement and redacted processing state.

Deduplication is scoped by subscription and provider delivery identity. A
successful duplicate returns the same terminal acknowledgement without
repeating an effect. Durable payload retention, retry and dead-letter policies
are explicit and bounded by the profile.

## Public URI Namespace

Use one isolated integration origin per physical Root-zone authority, with
separate operational route classes:

```text
https://{zone-ingress-authority}/v1/oauth/callback/{callback_profile_id}
https://{zone-ingress-authority}/v1/webhooks/{endpoint_id}
https://{zone-ingress-authority}/v1/continuations/{attempt_id}
```

The current placement map is:

| Subnet/Root zone family | Ingress authority |
| --- | --- |
| central/shared (`us`, `eu`, `in`, `ch`) | `integrations.inimatic.com` |
| isolated RU (`ru`) | `ru.integrations.inimatic.com` |

This mapping is platform authority. Provider packages, Applications and
Builder never derive a host by concatenating a zone label. Adding or splitting
a zone changes the admitted placement map and endpoint revisions, not portable
capability or ingress-profile identity.

`callback_profile_id` identifies a stable OAuth authorization-server/client
registration profile, not a skill or business capability. A Google OAuth
registration may serve Gmail, Drive and Calendar capabilities; conversely two
Google registrations may require different consent, scopes, regions or
verification. Therefore `google.gmail` is not a canonical public route
identity.

One OAuth redirect URI is registered per admitted callback profile and zone
authority by default. A provider registration may list multiple zonal URIs
only as explicit endpoint revisions; wildcard redirect hosts are forbidden.
Sharing a URI across issuers is allowed only when the profile has an explicit
issuer-identification and mix-up defense. Changing a provider-registered URI is
a coordinated endpoint migration, not a normal package or Application update.
Old URI revisions remain routable only for their bounded migration window.

Each `*.integrations.inimatic.com` authority is intentionally separate from
the main `inimatic.com` web origin and from every other zone authority. It has
independent cookies, CSP, request limits, WAF policy, logging redaction and
operational ownership. The main client must not observe authorization codes or
raw webhook payloads.

## CBS Placement

The ingress service is a native AdaOS infrastructure provider. A provider
`BindingDefinition` declares typed ingress ports required by its
implementation:

```yaml
ingress_ports:
  - name: authorization_return
    profile_ref: ingress-profile:oauth.authorization-code.google@1
    required_guarantees:
      single_use: true
      local_acceptance: true
```

Resolution admits an environment/provider combination only when:

```text
Requirements(ingress_port) subset_of Guarantees(IngressEndpointRevision)
```

The local `BindingInstance` attaches the exact endpoint revision and provider
connection. The Application depends only on its semantic capability. It does
not name the ingress host, callback path, OAuth registration, secret, provider
account or physical route.

Completing an authorization flow creates or updates a Core-owned
`ProviderConnection`. Attaching that connection to an Application remains an
explicit permissioned operation. Multiple Applications can reuse a connection
without copying its credentials.

## Authority Boundary

The public gateway owns only Internet ingress and bounded rendezvous:

- TLS termination, route-class admission, body-size and rate limits;
- lookup of an admitted endpoint revision and attempt/subscription projection;
- protocol-neutral envelope capture and exact route delivery;
- replay, expiry, acknowledgement and redacted operational evidence;
- a neutral OAuth completion page or the response required by an admitted
  synchronous challenge profile.

The public gateway does not own:

- OAuth client secrets, refresh tokens, access tokens or provider credentials;
- the provider account or Application attachment;
- provider-specific business operations;
- selection of another node when the admitted target is unavailable;
- arbitrary redirects supplied by query parameters;
- a general public proxy into the subnet.

The target Core/provider adapter is the credential and provider-semantic
authority. It validates the local record, performs an OAuth code exchange,
verifies provider-specific webhook semantics and records the provider
connection or effect locally.

## OAuth Initiation And Completion

```text
Application
  -> provider adapter: begin_authorization(binding_instance_ref, return_intent)
  -> Core ingress broker: create CallbackAttempt
  -> Root ingress registry: admit hashed rendezvous and exact route
  -> provider authorization URL with exact redirect_uri and opaque state

provider authorization server
  -> admitted zone integration authority OAuth route
  -> validate endpoint profile, state projection, expiry and replay
  -> encrypted delivery to the exact admitted subnet/node route
  -> local broker and adapter validate the full attempt and issuer
  -> local adapter exchanges code and stores tokens in Core vault
  -> Core records ProviderConnection and explicit completion evidence
  -> gateway renders a neutral result page
```

`return_intent` is an allow-listed semantic intent, never an arbitrary return
URL. The browser completion page contains no authorization code or token and
uses a restrictive CSP and referrer policy.

If the target is offline, the gateway may retain an encrypted OAuth envelope
only for a short bounded TTL compatible with authorization-code expiry. It must
not exchange the code centrally or silently attach another connection. On
expiry the operation fails closed and the user starts a new authorization
attempt.

## Webhook Acceptance And Delivery

```text
provider
  -> admitted zone integration authority webhook endpoint
  -> endpoint/profile lookup, generic admission and bounded body capture
  -> verification according to the admitted verifier placement
  -> durable encrypted IngressDelivery acceptance
  -> timely provider acknowledgement
  -> authenticated delivery to the exact Core binding
  -> provider adapter verifies semantic envelope and applies idempotently
  -> receipt, retry or dead-letter transition
```

Verifier placement is explicit. A public-key verifier may run at ingress. A
shared-secret verifier runs locally unless a separately admitted minimal
verification projection is provisioned to the edge. The edge never acquires a
long-lived provider credential merely for convenience.

Acknowledging a webhook means the admitted durability boundary has accepted
it, not that a NATS publication was attempted. Ordering guarantees are absent
unless declared by the profile. Consumers must tolerate duplicate and, where
the provider permits it, out-of-order delivery.

## Routing Transport

Root already provides rendezvous for an outbound-connected hub. The gateway
delivers a compact encrypted envelope through a dedicated scoped route family,
for example:

```text
route.v2.to_hub.<hub_id>.<ingress_key>
```

The concrete subject is transport metadata, not part of a portable contract.
The route must enforce:

- exact Root/hub/subnet/node binding from the admitted endpoint revision;
- authenticated, audience-bound and encrypted delivery;
- no fallback to another tenant, subnet or node;
- an acknowledgement proving local acceptance;
- class-specific retention and replay rules;
- redaction of URI query data and sensitive payloads from diagnostics.

The subnet's admitted Root zone selects the ingress authority when the endpoint
revision is materialized. `zone_id` is bound into the endpoint digest, attempt,
Root rendezvous, encrypted envelope and evidence. The selected zone also
becomes part of provider registration. It is never inferred from a callback,
redirected to another zone or changed while an attempt is active.

## Core And Skill-Facing API

Core exposes typed operations rather than a protocol-erasing
`begin_callback`:

```python
authorization = integrations.begin_authorization(
    binding_instance_ref=binding_instance_ref,
    application_ref=application_ref,
    return_intent="application.connection.refresh",
)

subscription = integrations.ensure_webhook_subscription(
    binding_instance_ref=binding_instance_ref,
    ingress_port="change_events",
)
```

The selected `EnvironmentProfile` decides materialization:

- `local-development` returns an admitted loopback endpoint;
- `public-connected` returns the endpoint admitted for the subnet's Root zone;
- a profile without the required ingress guarantees fails closed with an
  actionable explanation.

Provider packages declare logical ingress profiles, request/response schemas,
allowed fields, verifier requirements, TTL/retention policies and completion
handlers. Applications never declare a callback URL or handler.

## Security Invariants

1. Public routes match an admitted class and exact endpoint revision; unknown,
   inactive and ambiguous endpoints fail closed.
2. OAuth `state` is high-entropy, single-use, time-bounded and stored only as a
   hash in public authority.
3. An OAuth attempt is bound to zone, endpoint revision, issuer, provider
   connection, binding, subject, Application, route and return intent.
4. PKCE S256 is required where supported. The verifier remains in local
   credential authority.
5. Authorization codes, tokens, secrets and unredacted provider error payloads
   never enter logs, telemetry, browser storage, YJS or model context.
6. No callback endpoint is an open redirector. Post-completion navigation is
   selected from an allow-listed server-side intent.
7. Webhook signatures are checked against the raw body; timestamp/replay and
   provider delivery-identity policies are profile-defined and fail closed.
8. Callback and webhook completion is idempotent. A replay cannot repeat token
   exchange, account attachment or a provider effect.
9. Public and local records must agree before an OAuth completion is accepted.
10. Application attachment remains separately authorized; a successful
    callback grants no implicit Application access.
11. Gateway records are deleted after their retention boundary. Durable audit
    contains only digests, timestamps, route/profile refs and redacted outcomes.
12. Public ingress never broadens an unreachable Core into a general inbound
    network route.

## Compatibility And Migration

The provider-specific Gmail loopback endpoint is retained until the Core
broker owns the same behavior and regression evidence. During migration it is
an adapter into the canonical broker, not a second authority.

Portable provider packages migrate from literal callback paths to
`ingress_ports`. Existing local OAuth configurations remain usable until their
environment profile is explicitly switched. Public callback activation
requires the exact public URI to be registered at the provider before the new
endpoint revision can become active.

Webhook support does not block the OAuth slice. No generic webhook contract is
accepted until durable acceptance, signature verification, deduplication,
retry and dead-letter behavior have executable evidence.

## Acceptance Criteria

- one provider-registered OAuth URI per callback profile serves every admitted
  subnet in its zone authority without exposing tenant, subnet or node
  identifiers;
- the provider completes OAuth while the target node has only an outbound Root
  connection;
- code exchange and all long-lived credentials remain in target Core;
- wrong class, issuer, endpoint, route, state, generation, expiry and replay
  fail closed with redacted evidence;
- cross-zone registration, callback delivery and replay fail closed without
  falling back to a global or neighbouring zone;
- local-development and public-connected materializations preserve portable
  binding and Application semantics;
- two Applications explicitly reuse one Core-owned provider connection without
  credential copying;
- physical Root/hub route changes do not change provider registration or
  portable artifacts;
- webhook admission, when enabled, proves signature verification, durable
  acknowledgement, duplicate delivery, offline retry and dead-letter recovery;
- derived health and audit views can be rebuilt from canonical endpoint,
  attempt/subscription and delivery records.
