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

## Credential Slots

Declare optional secret slots separately from settings, without values:

```yaml
capabilities: [configuration.read, configuration.write, secrets.read, secrets.write]
configuration:
  schema: {type: object, additionalProperties: false}
  defaults: {}
  credentials:
    service_token:
      purpose: Read the user-configured external service
```

`adaos.sdk.data.secrets.get/set/delete` uses the node's existing vault for these
slots. The verified local owner, manifest capabilities, active grant profile,
Application/component ownership, declared purpose and selected runtime all
apply. The SDK takes a slot name, not another Application ID or credential ref.
DEV uses its own binding namespace and never loads production installations.
Delegated users and background callers are not admitted by this initial adapter.

Beta inherits opaque references. Setting a Beta secret creates a new binding;
the original Stable binding is retained until adoption/recovery. Deletion revokes
the referenced value and removes its active binding: old snapshot references
cannot resurrect it. Vault reads are not cached, so revocation/rotation are live.
Changing the purpose of an inherited slot requires explicit owner rebinding.
The current adapter does not import existing plaintext `files/secrets.json`;
legacy skills retain their old private adapter and are not migration-qualified.

Use synthetic secrets during development. Never include values in prompts,
fixtures, release packages, ordinary configuration, screenshots or logs. Backend
errors are sanitized and cannot switch the caller to the legacy/global vault.
Failed binding CAS can retain an unreferenced private vault item; credential
retention/garbage collection is separate from channel acceptance.

## Qualification Boundary

The local runtime facade and revisioned store are implemented and tested.
Local native Builder prepare/adopt and isolated DEV settings are implemented;
Owner-only declared credential slots pass synthetic channel/denial tests and a
local Windows Keyring write/read/revoke probe. Delegated actor/purpose grants,
legacy credential import, restart/UI qualification and the two complete Reading
List cycles remain open in `APP1-15`; this SDK is not permission to copy production settings
or secrets into model context, packages or synthetic development fixtures.
