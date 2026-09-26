# Application Data Lifecycle

Local Builder Beta preparation is a production data transition, not DEV Preview.
Core verifies the Candidate's immutable composition and package manifests,
fences native tool execution, snapshots the accepted Stable data, applies a
pinned forward migration chain, inherits declared configuration and selects Beta.
There is no second Applications approval for the publisher's local development.

Each owned native request/response skill declares its stores in `skill.yaml`:

```yaml
data_lifecycle:
  schema: adaos.skill.data_lifecycle.v1
  execution: native_tools
  reconstructible_directories:
    - db/backups
  databases:
    - path: records.sqlite3
      migrations:
        - version: 1
          name: initial
          statements:
            - CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, value TEXT)
        - version: 2
          name: optional_description
          statements:
            - ALTER TABLE records ADD COLUMN description TEXT
```

Paths are relative to this skill's SDK data root. Keep the full migration chain
and never edit applied versions or checksums. Core owns the migration transaction:
SQL cannot attach other databases, control the transaction or load extensions.
Fresh-install initialization must produce the same schema. Do not rerun additive
DDL unconditionally in a request handler. A stateless skill declares `databases: []`.

`reconstructible_directories` is an explicit allow-list for derived files that
can be rebuilt from authoritative inputs. Core admits files below these roots
to the runtime inventory but does not transfer them between Stable and Beta.
The directories cannot overlap each other or contain a declared database. Do
not use this declaration for user data, evidence, credentials, the only copy of
an external object, or an expensive cache without a deterministic rebuild
contract. For example, Research Orchestrator declares `db/backups` while its
authoritative SQLite database remains in `databases`.

Development and migration tests use synthetic records only. Existing Stable data,
configuration values and secrets must not enter model inputs, DEV, packages,
screenshots for model review or public test artifacts. The model works from the
schema and code. Migration receipts contain identities, checksums and timings,
not business records. Recovery snapshots remain in the node's private storage.

Accepting the exact Beta adopts its writes into the next Stable data bucket and
adopts typed configuration. A differing Stable snapshot or an existing unrelated
target bucket is a conflict, not permission to overwrite it. An interrupted
transition keeps a persistent execution fence and resumes its original intent;
retrying a completed Candidate does not reseed its data or publish again.

An unfinished Stable-to-Beta preparation can be explicitly cancelled through
`adaos.sdk.builder.applications.abort_local_trial_preparation`, with the exact
Candidate/release and `applications.recover` admission. Core verifies the retained
Stable code/installation, deactivates staged configuration and retains private
failed Beta data/snapshots. Recovery remains fenced on interruption and retries
the exact intent. An aborted operation is not completed migration evidence and
cannot be reused. This operation cannot undo a started Stable adoption or a
successful channel transition; recover publication or use a separately reviewed
snapshot rollback for those cases.

Rejecting an archived Candidate also reconciles an unfinished preparation that
failed before `RuntimeSelection` could point at Trial. The retained transition
journal is aborted against the still-current Stable source before a newer
Candidate is allowed to prepare data. A missing archive with no exact Trial
selection remains a harmless superseded decision; an existing archive is not
silently ignored.

Settings use [Application Configuration](application-configuration.md); no
credential value is copied as ordinary data. Initial cutover supports only
explicit owned SQLite stores and native tools. Shared mutable stores, background
workers, attachments, legacy credential files, Beta-to-Beta loss-confirmed reseeding
and post-cutover snapshot rollback need their own qualified adapters. Do not advertise them
as supported or silently omit their data.

See [the lifecycle roadmap](../architecture/application-lifecycle-and-distribution-roadmap.md)
for the live qualification boundary. Synthetic tests do not complete the two
managed Reading List cycles.
