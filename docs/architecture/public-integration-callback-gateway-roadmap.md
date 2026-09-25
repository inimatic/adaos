# External Integration Ingress Roadmap

Status: implementation roadmap for
[External Integration Ingress And Public Callback Gateway](public-integration-callback-gateway.md).

Last reviewed: 2026-09-25.

Implementation checkpoint (2026-09-25): EIG0's OAuth contracts and inventory,
the EIG1 broker/loopback implementation, and the repository implementation of
EIG2's Root rendezvous, encrypted delivery, bounded retry, neutral response and
exact Core acknowledgement are complete. The zone-aware isolated nginx/ACME
configuration is committed for `integrations.inimatic.com` and
`ru.integrations.inimatic.com`, but DNS, certificate issuance, WAF/rate-limit
policy and a live Internet-to-Core proof remain deployment work. Accordingly
`EIG2-01` and the live EIG3 items stay open; repository tests are not presented
as external operational evidence.

## Outcome

AdaOS provider bindings can declare a typed external ingress requirement and
receive either an admitted loopback or public materialization without exposing
callback topology to Applications or skills. OAuth is proven first. Durable
webhooks reuse the registry and transport only after their distinct delivery
semantics have executable evidence.

## Priority Vocabulary

- `[must]`: required for the first production-capable public OAuth callback and
  safe coexistence with the local-development path.
- `[should]`: required before enabling another provider or production webhook
  traffic broadly.
- `[could]`: useful operability or ergonomics that must not delay the OAuth
  proof.
- `[deferred]`: deliberately excluded until its prerequisite proof exists.

Priority is not completion state. An item is checked only when implementation,
tests and exact-revision evidence exist.

## Scope Guardrails

1. Do not build a general reverse proxy into an AdaOS subnet.
2. Do not place tenant, Application, skill, subnet or node identity in a public
   URI.
3. Do not treat a public endpoint id, OAuth `state` or webhook query secret as
   sufficient authority.
4. Do not move token exchange or long-lived provider credentials to the public
   gateway during the OAuth proof.
5. Do not reuse OAuth attempt lifecycle for webhook delivery.
6. Do not acknowledge a webhook merely because an in-memory/NATS publish was
   attempted.
7. Do not let provider packages or Builder concatenate callback hosts/paths.
8. Do not make webhook generalization a prerequisite for the Gmail public
   callback proof.
9. Do not derive callback hosts in a provider package, Application or Builder;
   materialize them from the admitted subnet/Root zone map.
10. Do not redirect or fall back between zone ingress authorities during an
    active callback attempt.

## Delivery Sequence

```text
EIG0 contracts and inventory
  -> EIG1 Core broker and loopback compatibility
  -> EIG2 public OAuth ingress and routed delivery
  -> EIG3 Gmail public-connected proof
  -> EIG4 lifecycle, UI and provider adoption
  -> EIG5 durable webhook vertical proof
  -> EIG6 additional ingress classes and optimization
```

## EIG0: Contracts And Compatibility Inventory

**Outcome:** ingress identities, classes and existing provider-specific routes
are explicit before authority changes.

- [x] `[must]` `EIG0-01` Add fail-closed schemas and immutable models for
  `IngressProfile`, `IngressEndpointRevision`, `CallbackAttempt`, routed ingress
  envelope, acknowledgement and redacted audit evidence.
- [x] `[must]` `EIG0-02` Define canonical refs, revisions, digest normalization,
  supported ingress classes and unknown-field rejection.
- [x] `[must]` `EIG0-03` Inventory existing OAuth, webhook, Telegram and other
  externally callable routes; classify authority, credentials, retention,
  acknowledgement and retry behavior.
- [x] `[must]` `EIG0-04` Define `BindingDefinition.ingress_ports` and effective
  guarantee validation without changing Application semantic requirements.
- [x] `[must]` `EIG0-05` Add a terminology/schema lint preventing literal public
  callback URLs and physical route identities in portable artifacts.
- [ ] `[should]` `EIG0-06` Define webhook subscription/delivery schemas now but
  keep their runtime mode unsupported until EIG5.
- [ ] `[could]` `EIG0-07` Publish a developer identity view showing profile,
  endpoint revision, binding instance and redacted provider registration.
- [ ] `[deferred]` `EIG0-08` Migrate every legacy externally callable endpoint.

**Exit proof:** Gmail's current loopback route deterministically projects to an
OAuth ingress profile and endpoint revision without changing runtime behavior.

## EIG1: Core Broker And Loopback Compatibility

**Outcome:** one Core-owned broker creates and validates OAuth attempts while
the existing `127.0.0.1:8777` flow remains functional.

- [x] `[must]` `EIG1-01` Implement typed `begin_authorization` and completion
  APIs over `CallbackAttempt`; prohibit generic caller-supplied callback URLs.
- [x] `[must]` `EIG1-02` Materialize an admitted `local-development` endpoint
  from the environment profile.
- [x] `[must]` `EIG1-03` Route the existing Gmail handler through the broker and
  remove provider-owned state storage as an independent authority.
- [x] `[must]` `EIG1-04` Bind state to issuer, endpoint revision, provider
  connection, binding instance, subject, Application and return intent.
- [x] `[must]` `EIG1-05` Enforce expiry, single use, replay idempotency, PKCE S256
  where supported and log/model-context redaction.
- [x] `[must]` `EIG1-06` Cover success, denial, wrong issuer/profile, missing and
  expired state, replay, token-exchange failure and restart recovery.
- [ ] `[should]` `EIG1-07` Expose typed redacted diagnostics through Applications
  rather than raw provider errors.
- [ ] `[could]` `EIG1-08` Add a developer-only endpoint inspection command that
  never prints secrets or raw OAuth response fields.
- [ ] `[deferred]` `EIG1-09` Remove the provider-specific Gmail callback path.

**Exit proof:** the configured Gmail account can be disconnected and connected
again through the unchanged loopback URI, and every negative/replay test fails
closed through the common broker.

## EIG2: Public OAuth Ingress And Routed Delivery

**Outcome:** the ingress authority admitted for a subnet's Root zone can
deliver a bounded OAuth response to an exact outbound-connected Core without
becoming credential authority.

- [ ] `[must]` `EIG2-01` Provision the isolated TLS origin, DNS, CSP, WAF/body
  limits, request redaction and route-class rate limits.
- [x] `[must]` `EIG2-02` Implement the Root ingress registry containing only a
  hashed rendezvous and the minimum admitted route projection.
- [x] `[must]` `EIG2-03` Implement an authenticated, audience-bound encrypted
  envelope over the existing outbound Root/hub route.
- [x] `[must]` `EIG2-04` Require local-acceptance acknowledgement; no alternate
  node/tenant fallback is allowed.
- [x] `[must]` `EIG2-05` Implement short bounded encrypted retention and explicit
  offline/expired failure without central token exchange.
- [x] `[must]` `EIG2-06` Render neutral success/error pages with restrictive CSP,
  no code/state leakage and no arbitrary return URL.
- [x] `[must]` `EIG2-07` Exercise wrong class, endpoint, issuer, route,
  generation, expiry, replay, offline node and acknowledgement loss.
- [x] `[must]` `EIG2-08` Bind endpoint materialization, Root rendezvous,
  encrypted delivery and evidence to the subnet/Root zone; support the central
  `integrations.inimatic.com` and isolated
  `ru.integrations.inimatic.com` authorities without automatic zone switching.
- [ ] `[could]` `EIG2-09` Add synthetic health probes that cannot create an OAuth
  attempt or provider connection.
- [ ] `[deferred]` `EIG2-10` Exchange OAuth codes or retain provider tokens at the
  public edge.

**Exit proof:** a test authorization response reaches exactly one selected Core
through outbound connectivity, produces local acceptance evidence, and cannot
be replayed or rerouted.

## EIG3: Gmail Public-Connected Proof

**Outcome:** the existing Google/Gmail binding works through the canonical
public profile without changing consumer Application semantics or credentials.

- [x] `[must]` `EIG3-01` Create an immutable Google OAuth callback profile and
  public endpoint revision; do not use `google.gmail` as its semantic identity.
- [ ] `[must]` `EIG3-02` Register every exact activated zonal public URI in the
  Google OAuth client while retaining the loopback URI during the migration
  window; wildcard or cross-zone redirect fallback is not accepted.
- [x] `[must]` `EIG3-03` Select `public-connected` through environment/binding
  resolution rather than a provider-specific flag.
- [ ] `[must]` `EIG3-04` Complete live authorization, local code exchange, vault
  storage and explicit attachment from an outbound-only Core.
- [ ] `[must]` `EIG3-05` Prove that `gmail_cbs_cleanroom`, `inbox_triage` and a
  third consumer retain byte-identical semantic requirements across loopback
  and public materializations.
- [ ] `[must]` `EIG3-06` Prove connection reuse without credential copying and
  exact per-Application permissions in Applications.
- [ ] `[must]` `EIG3-07` Capture redacted E2E evidence for consent denial,
  disconnected Core, expiry/retry, successful completion and replay.
- [ ] `[should]` `EIG3-08` Document provider registration, callback-profile
  migration and incident rollback for operators.
- [ ] `[could]` `EIG3-09` Add a guided Applications action that verifies the
  registered URI before activating an endpoint revision.
- [ ] `[deferred]` `EIG3-10` Remove loopback development materialization.

**Exit proof:** a real Google account connects through the public URI and all
long-lived credentials remain in Core; switching materialization changes no
Application or portable binding identity.

## EIG4: Lifecycle, UI And Provider Adoption

**Outcome:** endpoints are operable and another OAuth provider can adopt the
contract without copying Gmail routing code.

- [ ] `[must]` `EIG4-01` Add create, validate, activate, rotate, cordon, migrate
  and retire transitions for endpoint revisions with expected-generation
  checks.
- [ ] `[must]` `EIG4-02` Add Applications views for profile, route health,
  pending-attempt expiry, connected-account ownership and explicit attachments.
- [ ] `[must]` `EIG4-03` Add operational telemetry for attempts, acceptance,
  latency, expiry, replay, offline failure and redacted provider outcome.
- [ ] `[must]` `EIG4-04` Migrate one non-Google OAuth provider using only the Core
  broker SDK and a provider profile/adapter.
- [ ] `[should]` `EIG4-05` Add endpoint revision impact analysis for provider
  registration and active bindings.
- [ ] `[should]` `EIG4-06` Add documented key rotation and compromised-endpoint
  incident procedures.
- [ ] `[could]` `EIG4-07` Add provider conformance fixtures reusable by Builder
  and package admission.
- [ ] `[deferred]` `EIG4-08` Automatically rewrite third-party provider
  registrations during migration.

**Exit proof:** an endpoint revision is safely rotated and a second provider is
integrated without a new public routing implementation.

## EIG5: Durable Webhook Vertical Proof

**Outcome:** one real provider webhook proves the separate subscription,
verification and durable-delivery model over the shared ingress infrastructure.

- [ ] `[must]` `EIG5-01` Implement immutable `WebhookSubscription` and
  `IngressDelivery` records with provider delivery identity and payload digest.
- [ ] `[must]` `EIG5-02` Implement raw-body signature verification, timestamp
  window, allowed event set and explicit verifier placement.
- [ ] `[must]` `EIG5-03` Establish a durable encrypted acceptance boundary before
  returning provider success.
- [ ] `[must]` `EIG5-04` Implement at-least-once delivery, idempotent duplicate
  acknowledgement, bounded retry and dead-letter recovery.
- [ ] `[must]` `EIG5-05` Exercise offline Core, duplicate, replay, out-of-order,
  invalid signature, queue restart and manual redelivery.
- [ ] `[must]` `EIG5-06` Prove one provider challenge/endpoint-verification flow
  without weakening normal webhook verification.
- [ ] `[should]` `EIG5-07` Add Applications delivery health, retry and redacted
  dead-letter views.
- [ ] `[could]` `EIG5-08` Add provider-specific ordering partitions where the
  provider contract can actually guarantee them.
- [ ] `[deferred]` `EIG5-09` Claim exactly-once external effect semantics.

**Exit proof:** the selected webhook survives duplicate delivery and Core
offline/restart, applies its effect once, and leaves reproducible delivery and
recovery evidence.

## EIG6: Additional Classes And Optimization

**Outcome:** proven infrastructure is generalized only from measured demand.

- [ ] `[should]` `EIG6-01` Implement a typed provider-challenge profile for a
  second provider family.
- [ ] `[should]` `EIG6-02` Implement one asynchronous continuation flow with an
  explicit authority and compensation boundary.
- [ ] `[should]` `EIG6-03` Feed endpoint freshness, provider dependency versions
  and delivery health into CBS EvidenceClaims and Viability Projection.
- [ ] `[could]` `EIG6-04` Add adaptive queue placement, batching and regional
  routing based on measured telemetry.
- [ ] `[could]` `EIG6-05` Publish an integration-provider test kit and local
  callback simulator.
- [ ] `[could]` `EIG6-06` Add registry discovery for independently reusable
  ingress profiles and verifiers.
- [ ] `[deferred]` `EIG6-07` Accept arbitrary user-defined public HTTP handlers.
- [ ] `[deferred]` `EIG6-08` Make derived graphs or Evolver recommendations an
  endpoint activation authority.

## Program Success

The program succeeds when OAuth and webhook proofs both preserve the following
separation:

```text
portable integration semantics
  != local endpoint and credential authority
  != public URI and physical route
  != transient attempt or delivery
  != derived health and impact views
```

Public ingress may evolve, split zone authorities and change physical Root
routes without changing unaffected Application or capability identities. A
subnet's zone placement, provider registration, credential authority or
delivery-guarantee change is explicit, planned, evidenced and reversible
within its declared boundary.
