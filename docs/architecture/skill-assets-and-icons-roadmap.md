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
      url: /api/node/skills/weather/assets/icons/current.svg
      cache_key: sha256:...
      fallback: sparkles-outline
    assistant.default.avatar:
      kind: image
      scope: system
      url: /api/node/resources/system/assistant.default.avatar
      cache_key: sha256:...
    weather.i18n.ru:
      kind: data
      role: i18n
      locale: ru
      url: /api/node/skills/weather/assets/i18n/ru.json
      cache_key: sha256:...
```

The client should cache by `cache_key` and invalidate when the manifest version or hash changes.

## Delivery Model

The authored descriptor is not a browser URL. During materialization AdaOS should
resolve every core-delivered `path` into a stable delivery URL and keep the
original descriptor for diagnostics.

For member-local browsers:

1. The hub/member runtime validates the resource descriptor.
2. The runtime resolves owner, relative path, MIME type, size, and content hash.
3. The node API serves the resource with `ETag`, `Cache-Control`, content type,
   byte-size limits, and path traversal protection.
4. The browser resource resolver maps `resource:<id>` to the delivered URL and
   caches by `cacheKey`.

For Root-routed or remote browsers:

1. The Root resource cache reads the same materialized manifest.
2. The first request pulls the content from the owning member/hub using the
   materialized URL or content hash.
3. Root stores the blob by `cacheKey` and exposes a Root-local URL to the
   browser.
4. Cache invalidation is content-addressed: a new `cacheKey` creates a new cache
   entry, and stale entries can be garbage-collected independently.

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
4. Cache successful responses by `cacheKey` with Cache API or IndexedDB.
5. Return a browser-usable URL or JSON payload and expose degraded status.
6. Apply `fallback` when the resource is missing, blocked, or has an unsupported
   MIME/kind.

The renderer should never treat every visual identity token as an Ionicon name.
Assistant avatars render as images first and fall back to icons only when the
image resource is unavailable.

## Phases

1. Register missing built-in Ionicons and validate legacy icon names during
   projection materialization.
2. Split assistant visual identity into icon fallback and avatar resource fields.
   Add system default assistant avatar descriptors and render avatar images in
   Voice/Chat where space allows.
3. Add a browser resource manifest/materialization endpoint served by the
   hub/member runtime for skill, scenario, and system resources.
4. Add node API delivery for core-owned resources with path validation, MIME
   metadata, ETag/cache headers, and `cacheKey` generation.
5. Add client-side resource resolution and local browser caching for all
   `resource:<id>` references.
6. Wire i18n dictionaries through `resources` so skills can publish localized UI
   text without rebuilding the Angular bundle.
7. Add Root cache/relay support for remote browsers and subnet-hosted resources.
8. Allow external storage URLs for large assets while keeping the subnet-hosted
   manifest as the source of truth.
9. Add diagnostics for missing assets so compact phone layouts still provide
   usable controls, avatar fallbacks, and modal close actions.
