# Public Site Projection Roadmap

Status: proposed roadmap from the 2026-09-30 design review.

Architecture owner: [Public Site Projection And Addressing](public-site-projection-and-addressing.md).

Dependencies:

- [Web UI Architecture](web-ui-architecture.md)
- [UI Addressing](ui-addressing.md)
- [Navigation Intent And Location Architecture](navigation-intent-and-location.md)
- [Public Access Grants](public-access.md)
- [Client Component System Roadmap](client-component-system-roadmap.md)

## Priority Rules

- `[must]`: blocks a usable public site release or the Back/Forward integrity
  gate.
- `[should]`: required before repeated public use across multiple sites.
- `[could]`: useful once the first release path is proven.
- `[deferred]`: explicitly excluded until a later named milestone or policy
  decision.

## PS0. Contract Inventory And Address Baseline

- [ ] `[must]` Inventory every browser URL writer and classify each write as
  hydration, semantic navigation, ingress intent, effectful transition,
  diagnostic-only state, or legacy compatibility. Include
  `NavigationLocationService`, `AppComponent` intent clearing, modal/view
  navigation, `PageActionService`, `YDocService`, and router usage.
- [ ] `[must]` Define `adaos.address.v1` as the shared address envelope for
  public sites and authenticated app/workspace surfaces. Preserve the
  existing `NavigationDestination` and `NavigationLocation` contracts as
  narrower compatibility projections while the new envelope lands.
- [ ] `[must]` Record current renderer reuse boundaries: page schema,
  layout render plan, region rendering, widget host, registry extensions,
  page data sources, theme/style ownership, and desktop-only shell concerns.
- [ ] `[must]` Define the first `adaos.site.v1` source shape and
  `adaos.site_projection.v1` sealed projection shape. The first slice may
  support only one-page sites, but it must already distinguish authoring
  source, runtime page graph, and immutable public projection manifest.
- [ ] `[must]` Choose the platform domain policy for the first release:
  opaque `p.adaos.io/st_<id>` addresses, optional org aliases, and custom
  domains through DNS verification. Record reserved names and anti-squatting
  rules before accepting human-readable platform aliases.
- [ ] `[should]` Add a short architecture decision note comparing the chosen
  approach with modern data-router/static-site patterns so future changes do
  not reinterpret the public shell as a separate website builder runtime.

Exit gate: a reviewer can map every existing URL mutation to an owner and can
describe how a public site request resolves without entering Yjs.

## PS1. Address Router And Browser History

- [ ] `[must]` Introduce a single `AddressRouter` or `NavigationController`
  that owns browser URL writes, canonicalization, Back/Forward handling,
  and transition dispatch.
- [ ] `[must]` Route page `navigate`, modal open, modal route change, and view
  selection through the address router when history binding is enabled.
- [ ] `[must]` Replace direct webspace URL mutation and reload paths with
  address-router transitions. A webspace/scenario switch may perform effects,
  but the resulting browser entry must be a resolved location, not a replayable
  command.
- [ ] `[must]` Preserve the current security invariant that one-time codes,
  auth tokens, pair codes, user codes, and runtime secrets are removed with
  `replaceState` and never copied into canonical links.
- [ ] `[must]` Add browser-history tests for: Back closes an addressed modal;
  Back restores the previous view; Back across a webspace context enters the
  resolver without blindly replaying a scenario command; canonical copy-link
  excludes secrets; public route and anchor round-trip.
- [ ] `[should]` Add transition diagnostics that explain whether a URL update
  was `push`, `replace`, blocked, redirected, or no-op.
- [ ] `[could]` Explore the browser Navigation API as an adapter after the
  History API path is stable. Do not make it a first-slice dependency.

Exit gate: browser history entries correspond to reversible locations, and
effectful commands are not reissued solely by Back/Forward traversal.

## PS2. Public Projection Runtime

- [ ] `[must]` Extract the shared render core used by Desktop into a public-safe
  entry point that accepts `PageSchema -> LayoutRenderPlan -> Region ->
  PageWidgetHost` without Desktop chrome, FABs, Webspace switching, auth
  overlays, or Yjs room admission.
- [ ] `[must]` Add a `site` surface class and register a first public-safe
  `site.*` component family: header, hero, section, card grid, flow,
  media block, call-to-action, and footer.
- [ ] `[must]` Implement static projection loading from cache/CDN with API
  fallback and digest-aware manifest selection.
- [ ] `[must]` Enforce public data-source policy: admit `static` and declared
  public `api` sources; reject direct `y`, private `mcp`, private
  `resourceQuery`, and authenticated desktop state unless a later public
  adapter explicitly admits them.
- [ ] `[must]` Implement theme tokens as validated CSS custom properties with
  component-owned structural SCSS. The first release must not require
  arbitrary user CSS.
- [ ] `[must]` Produce at least one one-page public site projection from
  `adaos.site.v1`, render it through the public shell, and verify desktop and
  mobile screenshots with no unsupported widgets, renderer errors, clipping,
  or incoherent overlap.
- [ ] `[should]` Add SEO metadata, OpenGraph, canonical links, sitemap source,
  robots policy, and route-level titles/descriptions to the projection
  manifest.
- [ ] `[should]` Add long EN/RU text, accessibility-tree, keyboard navigation,
  reduced-motion, empty/error/backend-unavailable, and high-latency API
  component conformance cases.
- [ ] `[could]` Add richer public components such as compare, timeline, FAQ,
  pricing, testimonials, and environment graph after the initial site proves
  the projection/runtime split.

Exit gate: a sealed static projection can be served without Yjs and without
desktop-private runtime dependencies.

## PS3. Public Domain And Release Resolution

- [ ] `[must]` Define the domain binding record:
  organization/project/site/domain/release/current projection digest,
  verification status, certificate status, and rollback target.
- [ ] `[must]` Implement resolver behavior for platform opaque URLs and one
  custom domain path. The resolver must map `host + path` to a site release and
  projection digest before the client renders.
- [ ] `[must]` Require DNS verification for custom domains and prevent one
  domain from being bound to multiple active sites.
- [ ] `[must]` Preserve a fixed public facade address for every site even when
  aliases, custom domains, titles, or route labels change.
- [ ] `[must]` Add cache invalidation and rollback semantics for publishing a
  new current projection.
- [ ] `[should]` Add preview release URLs and owner-only preview access without
  leaking unpublished projection manifests through public cache keys.
- [ ] `[should]` Add certificate renewal, domain health diagnostics, and
  explainable unavailable states for unverified, expired, revoked, or
  conflicting domains.
- [ ] `[could]` Add human-readable org/site aliases after anti-squatting,
  reserved-name, rename, transfer, and abuse policies are accepted.

Exit gate: a public site has a stable opaque platform address, can optionally
serve from a verified custom domain, and can roll forward/back by changing the
release pointer rather than rewriting the site source.

## PS4. Authoring, Editing, And Source Maps

- [ ] `[must]` Build the first compiler path from `adaos.site.v1` to
  `adaos.webui.v1` plus `adaos.site_projection.v1`.
- [ ] `[must]` Store source-map anchors from rendered `site.*` widgets back to
  sections, theme tokens, resources, and routes in the authoring source.
- [ ] `[must]` Admit AI-generated site changes only as validated semantic
  patches against `adaos.site.v1`; reject unchecked DOM, CSS, or JavaScript
  edits.
- [ ] `[must]` Add a preview-publish loop: edit source, compile projection,
  render preview, validate, publish sealed release.
- [ ] `[should]` Add authenticated visual edit overlay support for owners:
  select component, inspect source anchor, propose structured patch, preview,
  and publish.
- [ ] `[should]` Add component-level copy/media/theme editing controls for the
  first `site.*` family, with route and anchor validation.
- [ ] `[could]` Add external design import/export helpers after source maps and
  semantic validation are stable.

Exit gate: a site can be changed without hand-editing the compiled runtime
graph and without relying on a single opaque autogeneration step.

## PS5. Public Backend Components And Hardening

- [ ] `[must]` Define public backend component adapters with bounded inputs,
  readonly policy, cache policy, error presentation, and audit metadata.
- [ ] `[must]` Add CSP/resource policy for public projections, including image,
  font, media, and API endpoint restrictions.
- [ ] `[must]` Add observability for projection load, route resolution,
  component API failures, cache hit/miss, digest mismatch, and domain binding
  failures.
- [ ] `[should]` Add privacy-preserving analytics hooks as explicit public
  components or site options, not as hidden shell behavior.
- [ ] `[should]` Add multi-locale routing and alternate links once the source
  model carries localized content explicitly.
- [ ] `[could]` Add readonly broadcast streams for public status pages or live
  event pages after the static/API runtime has production evidence.

Exit gate: public sites can use declared backend data safely without collapsing
back into authenticated desktop runtime assumptions.

## Deferred

- [ ] `[deferred]` Arbitrary per-site JavaScript execution.
- [ ] `[deferred]` Arbitrary user-authored CSS outside reviewed theme/package
  boundaries.
- [ ] `[deferred]` Public per-visitor Yjs documents for ordinary sites.
- [ ] `[deferred]` Multi-project/custom-code deployment per site.
- [ ] `[deferred]` Marketplace distribution of third-party site themes and
  components.
- [ ] `[deferred]` Ecommerce, public write workflows, or authenticated customer
  portals on top of this first public-site runtime.
- [ ] `[deferred]` SEO-first full static HTML generation. The first contract is
  a static projection loaded by the client; server/edge prerendering can be
  evaluated after the address, cache, and component contracts are stable.
