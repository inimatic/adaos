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
records plus optional exact `EvidenceClaim`/`EvidenceAssessment` records and
reports compatible requirements and evidence obligations. Stale historical
verification and newly proven incompatibility have different stable
explanations. It is a read-only preflight: `activation_performed` is always
`false`. Exact package closure, evidence admission, planning, and activation
use the existing CBS services after semantic viability succeeds.

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

Trial placement now compiles this setup contract automatically from immutable
component manifests and release permission declarations. A release that
declares `google.gmail`, for example, enters Trial with an exact required
connected-account row; the Application package does not copy setup metadata
and the projection never contains an OAuth token.

For Gmail Automation, Builder receives a conditional implementation contract
only when the accepted task mentions Gmail, `google.gmail`, or
`mail.messages.manage`. The contract requires the typed
`adaos.sdk.providers.gmail` facade, the exact `gmail.modify` scope, fixed Core
provider operations, explicit Application permission review, Application-owned
send deduplication, and secret-free tests. Generic external HTTP and package
credential slots are rejected for this path. See
[Google Gmail Provider](../sdk/google-gmail-provider.md).

The same access read also publishes exact grant, role and permission
declarations. Grant rows carry `grant_id` and `revision` for CAS updates; role
and permission choices bind canonical `id` values and safe titles. Consumers
use the declared result paths and do not copy these release-owned choices into
their package.

`applications.list` and `applications.show` publish the same exact Application
record schema. The list collection is `response.result.applications`; the
selected record is `response.result.application`. Both contracts identify a
record by `application.application_id` and declare the Application,
installation and subscription revision paths plus the effective release
digest. Missing local installation, subscription, release and development
state is represented by documented nullable fields. Catalog unavailability is
represented by `available=false` together with the machine-readable
`attention` reason and message. A missing Application is a transport error,
not an invented empty record.

For locally developed Applications, the same record exposes bounded Builder
lifecycle evidence. `local_development.trial.navigation_target` is present only
for an accepted Candidate whose active Trial placement matches that exact
Candidate; it contains logical Webspace navigation fields and never a private
filesystem path. Stable UI actions are gated by
`local_development.publication.evidence_present`, which becomes true only for a
published version with a recorded publication timestamp.

The Applications detail view also renders `cbs_lifecycle` as a compact derived
projection of requirement, resolution, plan, activation, and lock stages. It
is intentionally read-only and may be rebuilt from the canonical CBS records.
The 2026-09-23 beta proof validated this projection in wide and compact browser
modes, activated Candidate `applications-0-1-34-2a7d35b40618` in Trial, and
accepted the exact Candidate through the public component-update endpoint. The
resulting stable `RuntimeSelection` pins release
`sha256:671797afb038e28b2453de707022d20ede8d7654ba0f7093d0c32a7d35b40618`;
the acceptance path therefore no longer depends on inferring a Candidate from
an ambiguous local-development record.

AdaOS Drive then exercised the same exact-selection boundary with Candidate
`adaos_drive-0-1-10-f404c70ab017`. Trial acceptance and stable Webspace
activation both pin release
`sha256:0048ac17db9a350289b0b878a530bbc9bda20c37ca30366c2b7ef404c70ab017`,
Webspace `desktop`, Application `adaos_drive`, runtime root `workspace`, and
the selected revision. The wide and compact stable browser checks rendered 19
widgets with no page or request failures.

## Builder 0.2.179 beta qualification

The 2026-09-23 completion audit found one real defect in the current Builder
source: the scenario invoked `builder_skill.set_ui_revision_current` and used
the `agent:builder_skill:builder` receiver without declaring `builder_skill` in
both its package dependencies and required runtime skills. The maintained
source was updated directly; teaching Builder to perform this Core-version
adaptation remains outside this beta.

The complete scenario, SDK-control-skill, and Builder-skill test suites pass
with that dependency declared. Checkpoint
`builder-beta-cbs-completion-20260923` produced scenario `0.2.96`,
`builder_sdk_control_skill` `0.1.122`, and `builder_skill` `0.3.178`. The exact
post-checkpoint ProjectRelease is `builder@0.2.179`, release digest
`sha256:828dfe3968f9b5477535ac51c94877103baa57b6c19ec839304073d85508e8a4`,
and source revision
`sha256:950528a09ee1eccf20081da7b923ad9eb15b48fbb73c15560ffdc4b5cfa8a6ea`.
An earlier pre-checkpoint `0.2.178` release was not overwritten or reused.

Candidate `builder-0-2-179-73d85508e8a4` completed an isolated, healthy Trial
with rollback available and was accepted as the exact beta Candidate. Builder
Stable remains `0.2.167`; no Stable promotion was implied by beta acceptance.
The live DEV browser review used the same source revision as the Candidate and
passed three checks in each of the 1440x1000 and 390x844 profiles with no page
errors or failing HTTP responses. This proves source-equivalent Builder UI
behavior, not live Trial execution: the isolated Trial is intentionally not
attached to the running desktop. Client boundaries cover 21 sources, the
44-widget capability inventory is current, and the development client build
passes.

The Builder review harness now uses the canonical local API port `8777` and
waits on current `selectedProjectId` and `workflowActivePhase` state instead of
obsolete nested projection fields. Read-only review preserves the user's
selected project rather than opening a picker and introducing a state race.

## Current migration boundary

The beta does not teach Builder to migrate an Application across Core versions.
Maintained beta Applications may be updated directly to the current Core and
client ABI; source-level reverse compatibility is not an acceptance criterion.
If Builder happens to produce a valid update, it still enters through the same
governed validation and activation path.

The pre-CBS direct Automation path remains only as a bounded runtime/recovery
bridge. A legacy session without canonical Prototype acceptance receives no
fabricated CBS identity. New governed runs with a current acceptance always
compile and pin CBS identity.

The runtime bridge can be removed after all of the following are true:

1. persisted active Builder sessions have either completed or been migrated;
2. all Builder entry points require canonical Prototype acceptance;
3. installed application-building skills emit the current acceptance ABI;
4. Applications exposes CBS compilation and viability in its normal review UI;
5. AdaOS Drive and Flowboard beta E2E suites pass only through the governed
   path for one release window and telemetry shows no legacy write-path use.

No automated migration is required for package contents solely because this
beta exists.
Skills that construct or inspect Builder handoffs should be updated to preserve
`cbs_compilation` and `cbs_compilation_digest`; skills that only invoke stable
Builder SDK operations remain compatible.

The Gmail provider work does not yet close reusable capability authoring. The
first beta intentionally binds one Application to one exact Core provider. A
subsequent reuse proof must extract a package-independent mail contract, admit
it into a local registry, let Builder select it by contract reference, and add
an explicit second-Application attachment/consent operation for the existing
provider-owned account credential.
