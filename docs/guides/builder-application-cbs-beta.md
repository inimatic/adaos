# Builder and Application CBS beta

Status: beta contract, introduced with the CBS1-CBS5 implementation.

## Boundary

An accepted Builder Prototype is compiled into
`adaos.builder.cbs_compilation.v1` before a governed Automation run starts.
The compilation contains:

- the semantic Application revision digest;
- exact `ApplicationRequirement` records;
- reconstructible simulation `StateSpace` attachments;
- unresolved Automation obligations;
- separate semantic, simulation, and production viability states.

The compilation never selects a package, a physical member, a
`BindingInstance`, or a production `StateSpace`. Those decisions remain with
the CBS resolver, `ResolutionPlan`, and activation coordinator. Registering or
inspecting a compilation therefore cannot change workspace authority.

Builder passes the exact compilation in the Automation request as
`artifacts.cbs_compilation` and pins its digest in
`links.cbs_compilation_digest`. The local Application registry stores each
compilation by digest and updates its latest pointer with optimistic
concurrency.

## Application API

Authenticated endpoints are mounted below `/api`:

```text
POST /v1/applications/{application_ref}/cbs/compilations
GET  /v1/applications/{application_ref}/cbs
POST /v1/applications/{application_ref}/cbs/semantic-viability
```

The compile endpoint accepts a canonical
`adaos.builder.prototype_acceptance.v1` document. `expected_previous_digest`
must pin the current compilation when replacing an existing revision. A
duplicate request for the same digest is idempotent.

The semantic viability endpoint accepts portable `CapabilityContract`
records and reports which requirements have a compatible contract. It is a
read-only preflight: `activation_performed` is always `false`. Exact package
closure, evidence admission, planning, and activation use the existing CBS
services after semantic viability succeeds.

### Release-driven setup editors

`applications.setup.show` projects the safe, supported subset of each
release-owned JSON Schema into `setup.editors.settings` and
`setup.editors.credentials`. The projection is data, not executable UI: the
Application-owned WebUI contract still owns the read and mutation actions.

A dynamic `ui.form` binds those rows with:

```json
{
  "selectedStateKey": "selectedSetupComponentRef",
  "recordsPath": "setup.editors.settings",
  "fieldsPath": "fields",
  "valuesPath": "values"
}
```

The client keeps the selected editor row as `$event.record` and sends the
edited draft as `$event.values`. Package-owned actions pass
`application_id`, `release_digest`, `component_ref`, and
`expected_revision` from the immutable record to
`applications.setup.configure`; credential actions additionally pass `slot`
to `applications.setup.credential`. This preserves compare-and-swap behavior
without hard-coding a release's settings in the Applications package.

Only scalar strings and their common formats, booleans, integers, numbers,
string arrays, and finite enums are projected in this beta. Unsupported
schema properties are reported in `unsupported_fields` and set
`supported=false`; the client must not guess an editor. Credential values are
write-only: the read projection exposes presence and revision but never a
secret. Provider-projected fields cannot introduce their own data sources,
file transports, or mutation targets.

`applications.access.users` likewise publishes the exact subject-choice path
`response.result.users_access.subjects`. Each option carries a canonical
`subject_ref`, a safe `display_label`, `kind`, and
`eligible_for_application_access`; Applications binds those fields instead of
accepting a free-text identity or copying the Users & Access directory.
`applications.access.show` publishes connected accounts at
`response.result.access.sections.connected_accounts`; every redacted record
contains `account_id` and `revision`, so account updates can bind identity and
`expected_revision` without exposing credentials or inventing CAS state. The
binding declares revision `0` for create and the selected record's `revision`
for update.

## Compatibility and migration

The beta keeps the pre-CBS direct Automation path operational. A legacy
session without a canonical Prototype acceptance receives no CBS compilation;
it is not given fabricated semantic identity. New governed runs with a current
acceptance always compile and pin CBS identity.

This compatibility branch can be removed after all of the following are true:

1. persisted active Builder sessions have either completed or been migrated;
2. all Builder entry points require canonical Prototype acceptance;
3. installed application-building skills emit the current acceptance ABI;
4. Applications exposes CBS compilation and viability in its normal review UI;
5. AdaOS Drive and Flowboard beta E2E suites pass only through the governed
   path for one release window.

No migration is required for package contents solely because this beta exists.
Skills that construct or inspect Builder handoffs should be updated to preserve
`cbs_compilation` and `cbs_compilation_digest`; skills that only invoke stable
Builder SDK operations remain compatible.
