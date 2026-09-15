# Application Configuration

`adaos.sdk.data.configuration` reads and replaces declared, typed, non-secret
settings of the active skill's owning Application installation. The owner is
resolved from the selected release's bound composition member, not a supplied
Application ID. Shared components without an unambiguous owner and DEV sources
cannot use production settings through this interface.
DEV source and DEV runtime skills use the same calls with a separate synthetic
store under DEV `.runtime/state`. They start from declared defaults, retain
their own revisioned overrides and never load the production installation or
copy production values. Schema changes still require explicit reconciliation.

```yaml
capabilities:
  - configuration.read
  - configuration.write
configuration:
  schema:
    type: object
    properties:
      page_size: {type: integer, minimum: 1, maximum: 50}
    required: [page_size]
    additionalProperties: false
  defaults:
    page_size: 10
```

```python
from adaos.sdk.data import configuration

settings = configuration.read()
updated = configuration.write(
    {**settings["values"], "page_size": 20},
    expected_revision=settings["revision"],
)
```

Async handlers use `await configuration.a_read()` / `a_write(...)`. Synchronous
I/O on an event-loop thread is rejected. Results contain only `revision` and
typed `values`; opaque credential bindings are retained but not exposed or
changed through this facade. Capability declarations and the active per-skill
grant profile both apply. Stale revisions, inactive channels, pending cutovers
and schema mismatches fail closed instead of resetting defaults.

An unconfigured first Stable reads declared defaults at revision zero. Beta
must already have a configuration record prepared by the admitted lifecycle;
reading Beta must not silently seed it. A schema change needs explicit
configuration migration or review, independent of business-data reseeding.

## Qualification Boundary

The local runtime facade and revisioned store are implemented and tested.
Local native Builder prepare/adopt and isolated DEV settings are implemented;
Application actor/purpose authorization, live credential resolution/rotation/revocation and
two complete Reading List cycles are not qualified by these unit tests. They
remain open in `APP1-15`; this SDK is not permission to copy production settings
or secrets into model context, packages or synthetic development fixtures.
