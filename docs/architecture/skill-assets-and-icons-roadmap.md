# Skill Assets and Icons Roadmap

Skill UI resources must not require a browser rebuild. The current client still registers a fixed Ionicons set at compile time, so missing icon names can produce runtime warnings and empty icons. The immediate rule is to register commonly used built-in Ionicons in the client, but the target model is resource publication by skills.

## Target

Skills should be able to publish UI assets through the assistant runtime:

- icons and small SVG assets
- thumbnails and preview images
- panel templates and metadata
- localized labels and descriptions
- optional external asset URLs for larger files

The browser should resolve these resources through a stable manifest, cache them locally, and fall back gracefully when a resource is unavailable.

## Runtime Shape

Recommended projection:

```yaml
skill_assets:
  skill_id: skill:weather
  version: 1
  icons:
    weather.current:
      kind: svg
      url: /api/node/skills/weather/assets/icons/current.svg
      cache_key: sha256:...
  images:
    preview:
      kind: image
      url: /api/node/skills/weather/assets/preview.webp
      cache_key: sha256:...
```

The client should cache by `cache_key` and invalidate when the manifest version or hash changes.

## Phases

1. Register missing built-in Ionicons and validate icon names during projection materialization.
2. Add a skill asset manifest endpoint served by the hub/member runtime.
3. Add client-side asset resolution and caching for skill-owned resources.
4. Allow skills to publish external storage URLs for large assets, while keeping the subnet-hosted manifest as the source of truth.
5. Add diagnostics for missing assets so phone layouts still provide usable controls and modal close actions.
