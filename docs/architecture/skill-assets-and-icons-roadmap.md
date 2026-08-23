# Browser Resources, Skill Assets, and Assistant Avatars Roadmap

Browser-facing UI resources must not require a browser rebuild. The current
client still registers a fixed Ionicons set at compile time, so missing icon
names can produce runtime warnings and empty icons. Assistant avatars, skill
icons, preview images, templates, and localized dictionaries need the same
delivery model: authored by the system, a skill, or a scenario; materialized by
AdaOS; delivered through stable runtime URLs; cached close to the browser; and
degraded with explicit fallbacks.

## Target

Skills, scenarios, and system packages should be able to publish browser
resources through the assistant runtime:

- icons and small SVG assets
- default assistant avatars and agent-specific avatar images
- thumbnails and preview images
- panel templates and metadata
- localized labels, descriptions, and i18n dictionaries
- small data files consumed by declarative UI bindings
- optional external asset URLs for larger files

The browser should resolve these resources through a stable manifest, cache them
locally, and fall back gracefully when a resource is unavailable. Remote browsers
should not need direct file access to a member workspace. A Root-facing cache or
relay owns cross-subnet delivery and invalidation, keyed by stable resource ids
and content hashes.

## Resource Classes

`resources` is the common browser resource plane. The `kind` describes the
payload shape, while optional role-specific metadata describes how the payload is
used:

- `icon`, `svg`, `image`: app icons, toolbar glyphs, preview images, assistant
  avatars, and other visual assets.
- `font`, `stylesheet`, `template`: renderer-owned presentation extensions that
  still need normal delivery, integrity, and fallback behavior.
- `data`: structured payloads such as localized dictionaries. A resource can use
  `role: "i18n"` and `locale: "ru"`/`"en"` when the data is a browser language
  source.

Assistant identity should keep icons and avatars separate:

- `active_agent_icon`: legacy Ionicon name or `resource:<id>` fallback for small
  monochrome glyphs.
- `active_agent_avatar_ref`: `resource:<id>`, absolute URL, data URL, or empty.
  The client renders this as an image and falls back to `active_agent_icon`.

System avatars are regular `scope: "system"` resources. They may initially be
packaged with the client or core runtime, but they should be exposed through the
same manifest and cache contract as skill-owned assets so later Root/member
delivery does not change browser code.

## Runtime Shape

Authored `webui.json` should declare stable resource ids. The runtime resolves
those declarations into browser URLs, cache keys, and delivery diagnostics.

Recommended authored shape:

```json
{
  "resources": {
    "weather.current": {
      "kind": "svg",
      "path": "assets/icons/current.svg",
      "mime": "image/svg+xml",
      "delivery": "core",
      "cacheKey": "sha256:..."
    },
    "assistant.default.avatar": {
      "kind": "image",
      "scope": "system",
      "path": "assets/avatars/assistant-default.webp",
      "mime": "image/webp",
      "delivery": "core",
      "cacheKey": "sha256:..."
    },
    "weather.i18n.ru": {
      "kind": "data",
      "role": "i18n",
      "locale": "ru",
      "path": "assets/i18n/ru.json",
      "mime": "application/json",
      "delivery": "core",
      "cacheKey": "sha256:..."
    },
    "weather.preview": {
      "kind": "image",
      "path": "assets/preview.webp",
      "mime": "image/webp"
    }
  },
  "apps": [
    {
      "id": "weather_app",
      "title": "Weather",
      "icon": "resource:weather.current"
    }
  ]
}
```

Recommended materialized projection:

```yaml
browser_resources:
  version: 1
  owner: skill:weather
  resources:
    weather.current:
      kind: svg
      scope: skill
      url: /assets/blobs/sha256/2a/7f/2a7f.../current.svg
      cacheKey: sha256:...
      fallback: sparkles-outline
    assistant.default.avatar:
      kind: image
      scope: system
      url: /assets/blobs/sha256/51/d0/51d0.../assistant-default.webp
      cacheKey: sha256:...
    weather.i18n.ru:
      kind: data
      role: i18n
      locale: ru
      url: /assets/blobs/sha256/c8/11/c811.../ru.json
      cacheKey: sha256:...
```

The client caches URL-backed resources by `cacheKey`; a new content hash creates
a new cache entry and leaves stale entries available for later garbage
collection.

## Delivery Model

The authored descriptor is not a browser URL. During materialization AdaOS should
resolve every core-delivered `path` into a stable delivery URL and keep the
original descriptor for diagnostics.

### Public Asset Store

Public browser resources are published into `.adaos/assets` before the browser
loads them. Skills and scenarios author files under their own `assets/`
directories, but the browser never reads those package paths directly.

The runtime publishes allowed public resources into a content-addressed static
store:

```text
.adaos/assets/
  public/
    blobs/
      sha256/<aa>/<bb>/<digest>/<filename>
  manifests/
    systems/<system>.json
    skills/<skill>.json
    scenarios/<scenario>.json
```

The materialized descriptor points at `/assets/blobs/sha256/...`, not at the
skill package. The static layer can be a mounted static-file app, sidecar, or
reverse-proxy `sendfile` target; it must not inspect skill manifests, resolve
workspace paths, or compute hashes on request. App/runtime code owns publishing,
validation, hashing, manifest generation, and garbage collection.

Private resources are deferred. The initial store accepts only public,
publishable browser resources such as icons, assistant avatars, preview images,
and localized JSON dictionaries. Later private delivery should use short-lived
signed URLs or fetch-to-blob flows without changing `resource:<id>` references.

For member-local browsers:

1. The hub/member runtime validates the resource descriptor.
2. The runtime resolves owner, relative path, MIME type, size, and content hash.
3. The runtime publishes the content into `.adaos/assets/public` by `cacheKey`.
4. A static serving layer returns immutable blobs with `ETag`, `Cache-Control`,
   and content type, without skill/app request logic.
5. The browser resource resolver maps `resource:<id>` to the delivered URL and
   caches by `cacheKey`.

For Root-routed or remote browsers:

1. The Root resource cache reads the same materialized manifest.
2. `/v1/root/browser-assets/cache/ensure` accepts a `cacheKey` and can pull a
   public blob from an explicit `sourceUrl`, verifying SHA-256 and size before
   publishing it into the Root store.
3. Root stores the blob under its own `.adaos/assets/public` and exposes a
   Root-local `/assets/blobs/sha256/...` URL to the browser.
4. Cache invalidation is content-addressed: a new `cacheKey` creates a new cache
   entry, and stale entries can be garbage-collected independently.

Until a blob is present in the Root-local cache, the hub route proxy may relay
the member's public content-addressed store at
`/hubs/{subnet_id}/assets/blobs/sha256/<aa>/<bb>/<digest>/<filename>`. This is a
narrow byte-serving compatibility path, not a general member filesystem proxy:
only `GET` and `HEAD` are accepted, both shard segments must match the digest,
and the filename is bounded. The path is public because the source store admits
only explicitly publishable browser resources. Private resources remain on the
future signed URL/fetch-to-blob path.

The legacy `/api/node/skills/{skill}/assets/...` endpoint is a development and
compatibility fallback. It is not the production byte-serving target.

External resources keep the subnet-hosted manifest as the source of truth. They
may be used for large or public assets, but descriptors must still provide kind,
MIME expectation, fallback, and cache/integrity metadata where possible.

## Browser Resolver

The client should use one resolver for icons, avatars, images, i18n dictionaries,
and declarative data resources:

1. Read descriptors from `ui.application.resources`, `data.catalog.resources`,
   and `registry.resources`.
2. Resolve `resource:<id>` into a descriptor.
3. Prefer materialized `url`/`src`; keep relative authored `path` as diagnostic
   input, not as a final browser URL.
4. Cache successful URL-backed responses by `cacheKey` with the browser Cache
   API and short-lived object URLs.
5. Return a browser-usable URL or JSON payload and expose degraded status.
6. Apply `fallback` when the resource is missing, blocked, or has an unsupported
   MIME/kind.

The renderer should never treat every visual identity token as an Ionicon name.
Assistant avatars render as images first and fall back to icons only when the
image resource is unavailable.

## Implementation Status

Status as of 2026-07-02:

- Public skill resources are materialized into `.adaos/assets/public` with
  SHA-256 blob paths, MIME validation, size limits, `cacheKey`, and owner
  manifests under `.adaos/assets/manifests/skills/`.
- Public scenario resources use the same store and owner manifests under
  `.adaos/assets/manifests/scenarios/`.
- Core-owned system resources use the same store and owner manifests under
  `.adaos/assets/manifests/systems/`.
- System assistant avatars are bundled with the core package as:
  `assistant.default.avatar`, `assistant.voice.avatar`, and
  `assistant.helper.avatar`.
- Skill install, skill sync, explicit skill update, scenario install, and
  scenario sync publish public resources best-effort after the package is
  materialized or pulled.
- API startup publishes core/system resources before mounting `/assets`.
- Webspace materialization includes system resources and resolves skill/scenario
  authored `path` descriptors to `/assets/blobs/sha256/...`.
- `/assets` is mounted as a static immutable blob layer; app logic publishes and
  validates resources ahead of byte serving.
- The Angular `PageDataService` resolves `resource:<id>` descriptors from the
  materialized resource roots and warms the browser Cache API by `cacheKey` for
  URL-backed resources. Current renderer coverage includes the existing
  resource data source, assistant avatar surfaces, and `ion-icon[name]` through
  the shared resource-icon directive.
- Runtime i18n dictionaries can be published as `kind: "data"`,
  `role: "i18n"` resources with an explicit `locale`. The Angular
  `I18nService` loads those dictionaries through the same resource resolver and
  merges them over bundled `assets/i18n/*.json` translations.
- A skill that shares one dictionary between runtime human-readable messages
  and browser UI keeps it canonically under `<skill>/assets/i18n/<lang>.json`.
  Core `I18nService` reads that location directly while retaining
  `<skill>/i18n/<lang>.json` as rolling-upgrade compatibility. Domain strings
  remain owned and versioned by the skill; core owns only loading, fallback,
  interpolation, and browser publication.
- Declarative skill and scenario UI should keep visible fallback text in the
  schema and add adjacent `*_i18n` fields. Runtime localization currently covers
  modal titles and widget configs recursively, including common fields such as
  `title`, `subtitle`, `label`, `placeholder`, `emptyText`, `loadingText`,
  `helper`, and `hint`.
- Root exposes a partial cache control plane:
  `/v1/root/browser-assets/cache-contract`,
  `/v1/root/browser-assets/cache/ensure`,
  `/v1/root/browser-assets/blobs/sha256/{digest}`, and
  `/v1/root/browser-assets/manifests/{subnet_id}/{owner_kind}/{owner_id}`.
  The blob endpoint redirects to static `/assets` instead of serving bytes from
  app logic. `ensure` can pull a public blob from an explicit `sourceUrl` and
  verifies the requested SHA-256 before publishing it into the Root store.
- Root diagnostics and GC endpoints are available at
  `/v1/root/browser-assets/diagnostics` and `/v1/root/browser-assets/gc`.
  Diagnostics report missing referenced blobs and publish errors; GC removes
  unreferenced content-addressed blobs after a dry run or explicit mutation.
- External URL resources are stored in owner manifests with `delivery:
  "external"`, `scope`, `owner`, MIME/cache metadata when provided, and no local
  blob copy. The manifest remains the authoritative descriptor while byte
  serving stays with the external store.
- Private resources remain deferred.

## Phases

1. [x] Register missing built-in Ionicons and validate legacy icon names during
   projection materialization.
2. [x] Split assistant visual identity into icon fallback and avatar resource fields.
   Add system default assistant avatar descriptors and render avatar images in
   Voice/Chat where space allows.
3. [x] Add a browser resource manifest/materialization endpoint served by the
   hub/member runtime for skill, scenario, and system resources.
4. [x] Publish public skill resources into `.adaos/assets/public` with
   content-addressed blob paths, MIME metadata, size limits, and `cacheKey`
   generation.
5. [x] Mount or sidecar a static `/assets` serving layer for public immutable blobs.
6. [x] Add client-side resource resolution and local browser caching for all
   `resource:<id>` references.
7. [x] Wire i18n dictionaries through `resources` so skills can publish localized UI
   text without rebuilding the Angular bundle.
8. [x] Add Root cache/relay support for remote browsers and subnet-hosted resources.
9. [x] Allow external storage URLs for large assets while keeping the subnet-hosted
   manifest as the source of truth.
10. [x] Add diagnostics for missing assets so compact phone layouts still provide
   usable controls, avatar fallbacks, and modal close actions.
