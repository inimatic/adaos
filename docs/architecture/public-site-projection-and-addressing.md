# Public Site Projection And Addressing

Status: target architecture from the 2026-09-30 public-site and browser
addressing design review.

This document defines how AdaOS publishes declarative public sites from the
existing browser renderer without turning a public visitor into an authenticated
Yjs desktop session.

It complements:

- [Web UI Architecture](web-ui-architecture.md) for the shared renderer and
  component ABI.
- [UI Addressing](ui-addressing.md) for typed refs and browser-facing
  addresses.
- [Navigation Intent And Location Architecture](navigation-intent-and-location.md)
  for ingress intents, resolved locations, and browser history.
- [Public Access Grants](public-access.md) for readonly public grants and
  public faces.

## Decision

AdaOS public sites are compiled static projections served through the universal
client. The public runtime may call backend endpoints for explicitly declared
components, but the default public page load is:

```text
host + path
  -> public address resolver
  -> site/release/projection manifest
  -> static projection cache
  -> shared page renderer
  -> optional component backend calls
```

The public visitor does not join a per-user Yjs document by default. Yjs remains
the authenticated collaborative desktop/workspace medium. A future public
workflow may opt into a readonly broadcast stream or a separate guest workflow,
but that is not the default site rendering contract.

The authored site source should be a semantic `adaos.site.v1` document. The
runtime-rendered artifact is a compiled `adaos.webui.v1` compatible page graph
using `site.*` widget types and a `site` surface class. A sealed public release
stores an immutable static projection manifest such as
`adaos.site_projection.v1`.

## Non-Goals

This architecture does not introduce:

- arbitrary user JavaScript on public pages;
- arbitrary per-site CSS as the main theming mechanism;
- public Yjs collaboration for ordinary landing pages and documentation sites;
- custom code deployments per public site;
- a general CMS or marketplace on the first slice;
- a second unrelated renderer for public pages.

## Current Implementation Base

The current client already contains most of the lower-level machinery:

- `PageWidgetHost` resolves widgets by type through a registry.
- The desktop renderer lowers a page schema into a layout render plan and
  regions.
- Page data sources already support static values and API reads, alongside
  authenticated Yjs, stream, MCP, and resource-query modes.
- `NavigationLocationService` is a nucleus for canonical URL projection and
  history writes.
- Public Drive already proves the direction of a public face that renders
  without entering the authenticated desktop/Yjs flow.

The missing target pieces are:

- a public site projection manifest;
- a public renderer shell that excludes desktop auth/Yjs/chrome concerns;
- a formal address model with one owner for URL writes and Back/Forward
  handling;
- `site.*` component contracts and theme tokens;
- host/path resolution for platform domains and custom domains.

## Source And Projection Model

AdaOS should distinguish three artifacts.

### Authoring source

`adaos.site.v1` is the human, Builder, and structured-editor source. It names
the brand, navigation, routes, sections, theme intent, media resources, backend
component bindings, and edit/source-map anchors.

It is not the browser runtime ABI.

### Runtime page graph

The site compiler lowers `adaos.site.v1` into a compatible `adaos.webui.v1`
page graph. This graph uses the existing renderer ABI, data-source model, action
model, layout plan, and widget host, but introduces a `site.*` widget namespace
and `site` surface class.

`ui.*` and `site.*` are compatible at the renderer and data/action ABI level,
but they are separate component namespaces. A `site.hero` is not an alias for a
generic `ui.card` collection; it is a public-page semantic component with its
own accessibility, responsive, media, SEO, and editing contract.

### Static projection manifest

`adaos.site_projection.v1` is the sealed public release artifact. It contains:

- site identity and release identity;
- projection digest and build timestamp;
- route table and canonical URLs;
- compiled page graphs;
- theme token bundle;
- resource manifest for images, fonts, icons, and documents;
- public backend component descriptors;
- cache and invalidation metadata;
- source-map refs for editing and review.

The manifest must not contain secrets, subnet topology, private node ids, or
mutable desktop state.

## Component And Theme Model

The initial public component family should use a dedicated namespace:

- `site.header`
- `site.hero`
- `site.section`
- `site.cardGrid`
- `site.flow`
- `site.mediaBlock`
- `site.compare`
- `site.timeline`
- `site.faq`
- `site.cta`
- `site.footer`

Later additions may include `site.visual.environmentGraph`,
`site.pricing`, `site.testimonials`, and richer documentation components once
there is measured demand.

Theme support should be token-first:

- structural SCSS belongs to shipped components;
- a site supplies validated CSS custom properties and design tokens;
- themes can choose typography, spacing scale, colors, radii, elevation,
  motion, and media treatment inside bounded token slots;
- trusted package-owned CSS may exist for platform-maintained themes;
- arbitrary per-site CSS is deferred until sandboxing, review, and debugging
  policies are explicit.

This keeps public sites visually customizable without making every site a
private frontend fork.

## Address Model

AdaOS should formalize one versioned browser address envelope. The exact schema
name can be `adaos.address.v1`.

```json
{
  "scope": "site",
  "public": {
    "host": "example.com",
    "site_id": "st_8f3k2q",
    "domain_id": "dom_123",
    "release_id": "rel_456",
    "projection_digest": "sha256:..."
  },
  "view": {
    "route": "/docs/install",
    "page": "docs.install",
    "anchor": "sdk"
  },
  "ephemeral": {
    "preview": false,
    "edit": false
  }
}
```

The same envelope can also represent authenticated desktop/workspace surfaces
by using `scope: "app"` and the existing zone/subnet/webspace/scenario fields.

The key rule is separation of lifetimes:

- an intent is an ingress command that may require effects;
- a location is the resolved shareable state;
- a transition is the effectful plan that reaches the location;
- a history entry is the browser-visible location projection.

Back/Forward restores locations. It must not replay registration, scenario
switch, publication, login, or other business commands.

## URL Policy

Public sites should use path-based canonical URLs.

Platform opaque address:

```text
https://p.adaos.io/st_8f3k2q/
https://p.adaos.io/st_8f3k2q/pricing
```

Optional org-scoped alias:

```text
https://adaos.io/@org/site/
```

Custom domain:

```text
https://example.com/docs/install#sdk
```

Opaque platform ids avoid encouraging squatting. Human-readable aliases are
secondary and may require owner verification, reservation rules, and conflict
policy. Custom domains are verified by DNS and then mapped to the site release
resolver.

Path segments carry canonical site routes. Fragments carry section anchors.
Query parameters are reserved for non-canonical runtime states such as preview,
edit, diagnostics, or campaign attribution. Secrets and one-time codes never
become canonical location state.

## Domain Resolution

The edge/root resolver owns host and path resolution:

```text
request.host + request.path
  -> domain binding
  -> site id
  -> active release or preview release
  -> projection digest
  -> static projection manifest
```

Custom domain binding requires:

- owner-controlled DNS verification;
- CNAME/ALIAS or equivalent platform routing;
- certificate issuance and renewal;
- conflict detection so one domain maps to one active site binding;
- explicit release pinning for preview and rollback.

`adaos.io` should be the public product domain family for user-facing sites and
preview/public client surfaces. `inimatic.com` remains appropriate for company,
control-plane, and legacy integration endpoints.

## Runtime Flow

The public shell should be a small client entry point:

1. Resolve the host and path to a public site address.
2. Load the projection manifest from the static cache or public API fallback.
3. Verify the projection digest when present.
4. Instantiate the shared page renderer with `surfaceClass = "site"`.
5. Register only public-safe widgets, actions, data sources, and resources.
6. Execute declared backend component reads through public component adapters.
7. Commit canonical location with `replaceState` after hydration; use
   `pushState` for user navigation across routes, pages, and modals.

The public shell excludes authenticated desktop chrome, Webspace switching,
Yjs room admission, private MCP calls, and desktop FAB behavior.

## Data Source Policy

Public site projections admit:

- `static` for sealed content;
- `api` for public backend component data through explicit adapters;
- `stream` only for readonly broadcast-style public streams after policy
  review.

They do not admit direct `y`, direct `mcp`, private `resourceQuery`, or
authenticated desktop state by default. If a public component needs backend
data, the projection describes a public backend adapter with bounded inputs,
cache policy, failure presentation, and access policy.

## Editing Model

Public site editing should not rely only on one-shot autogeneration. The target
editing loop is:

```text
structured editor / Builder prompt
  -> semantic patch against adaos.site.v1
  -> validation and preview projection
  -> visual review with source-map anchors
  -> publish sealed projection release
```

The rendered public page may expose an authenticated edit overlay for owners.
That overlay selects source-mapped components and proposes structured changes;
it does not let the public runtime mutate arbitrary DOM or CSS. LLM-generated
changes are admitted as validated semantic patches, not as unchecked code.

## History And Back Button Invariants

The browser address layer must be single-owner.

- Hydration, secret removal, projection digest normalization, and redirect
  cleanup use `replaceState`.
- User-visible navigation to another route, page, view, or modal uses
  `pushState`.
- Background data refresh, static cache update, stream delivery, and
  diagnostics do not create history entries.
- `popstate` enters the same resolver used for ordinary navigation.
- Effectful transitions are not replayed merely because the user pressed Back.

For desktop this fixes the current split between `NavigationLocationService`,
manual URL mutation, modal/view state, and Yjs-driven reloads. For sites it is
a release blocker: public paths, anchors, previews, edit mode, and custom
domains must all round-trip through the same address model.

## Security And Privacy

Public projection manifests are browser-visible and cacheable. They must be
treated as public artifacts:

- no secrets;
- no private topology;
- no private user, member, or node identifiers;
- no unpublished source text beyond explicit public content;
- public backend calls require explicit readonly/public adapters;
- custom domains must preserve the same access checks as platform domains;
- owner editing requires authenticated control-plane access, not public URL
  possession.

## Compatibility Boundary

The public site runtime reuses the WebUI renderer contract, not the desktop
runtime contract.

Shared:

- widget registry mechanism;
- layout render plan;
- page widget host;
- static/API data sources;
- typed actions that are explicitly public-safe;
- resource loading and theme tokens.

Separated:

- desktop chrome and FABs;
- Webspace/Yjs lifecycle;
- authenticated MCP/resource queries;
- workspace scenario switching;
- desktop modal catalogs unless explicitly compiled into a public site.

This lets AdaOS build public sites from the same declarative UI foundation
without forcing every public visitor through the private desktop model.
