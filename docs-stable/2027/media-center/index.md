# Media Center

Status: beta-candidate public documentation.

**Private media. Local control. AdaOS-powered playback.**

AdaOS Media Center is an applied solution for private media libraries. It uses
the AdaOS runtime foundation to discover, catalog, search, and play local media
through a browser-facing application surface.

## Current Capabilities

- Register local library folders without copying large audio and video files
  into AdaOS storage.
- Keep catalog state, favorites, play counts, details, source metadata, and
  playback plans in the Media Center skill.
- Search by title, names, tags, people, source, and MIME type.
- Filter by kind, source, favorite state, and availability.
- Sort by recent scan time, title, size, and play count.
- Plan playback through AdaOS media content paths with browser range streaming.
- Use a browser media surface for catalog-backed playback.

## What Beta Users Should Expect

Media Center is the best first public beta target because it has a concrete
consumer workflow, clear visible value, and a familiar comparison category.

The beta should focus on:

- local library import and re-scan reliability;
- browser playback quality across common audio and video files;
- large-library catalog performance;
- privacy expectations around file paths and metadata;
- installation and recovery instructions that do not require internal
  architecture knowledge.

## Current Limits

- Unsupported codecs and advanced rendition generation are not a stable public
  promise.
- Native mobile and TV background playback require additional platform-specific
  packaging.
- Distributed placement of Media Center capabilities across multiple nodes is a
  direction, not a public beta guarantee.
- Automated duplicate resolution and unattended topology decisions remain
  review-oriented.

## Positioning Draft

Public wording to refine:

> AdaOS Media Center turns your own node into a private media library that stays
> close to your files, your devices, and your rules.

