# Gmail Mail Client: Stage-One CBS Beta Proof

Status: validated locally on 2026-09-24.

This record closes the first of the two proposed mail capability experiments:
a new Gmail Mail Client Application has a package-neutral semantic requirement,
passes the current Builder beta, Trial, verification, publication, workspace,
setup, permission, and browser runtime boundaries, and is installed as a stable
Application. It does **not** yet prove independently reusable contracts, native
CBS production resolution, autonomous prompt-to-beta Builder execution, or a
shared connected account.

## Result

The retained beta is `gmail_mail_client@0.1.4`:

| Record | Exact identity |
| --- | --- |
| Application release | `sha256:8dde211ab6f6ce5c47059c56ae8e837933026ce3dad55929e58d71c79ddb2c06` |
| Scenario package | `sha256:6970f562cb7f91260aad69758884545c02b2e87984346c0b4cedba6b873e5caa` |
| Provider skill package | `sha256:57945818da0ee3173d4a861523ef2feb15dc4a60ac89ad5852bd81f7a7d975ff` |
| BindingDefinition | `sha256:5398d3562ed19cb375a2ca1372dbfa1c049f84a6cc8873491ee1282196a54eb2` |
| CapabilityContract | `sha256:f694f5fc917ed4a76330740a7ff555c1c6e0514781f3b29d84c7ce55f5d61698` |
| Semantic compilation | `sha256:10d68d734622c568f9499ac2c17e47f085cbddb8a1784750a4092e1f184baea2` |
| Semantic Application revision | `sha256:563f0784bb75748e90ebe56279446663d71147be4dfc19024ab380c96d0e41e9` |
| Publication verification | `sha256:7152fa254001da5e8cf5e2511414ffe93853d388cca11f074aaf00e5b1c7ed20` |
| Permission profile | `sha256:a582f61062fdc9f3d0cf4dee109b69fe4dea14ce1f4c6a3b49c29414c5eb7512` |
| Source commit | `39acae3a3dd5347b610a0b028110c669bc9ee80e` |

The accepted candidate is
`gmail_mail_client-0-1-4-71c79ddb2c06`. The stable installation is active at
revision `1`; the exact `stable_installation` RuntimeSelection is revision `2`
and selects the release above from the workspace runtime root. Publisher access
is represented by owner grant `appgrant.3c5efd1eaae8b2625af5aee35e6e8391`.

## Semantic and CBS Boundary

The accepted semantic document explicitly requires:

```text
capability:mail.messages.manage@^1.0.0
```

It does not name Gmail, a package, a skill, an entry point, an endpoint, an
account, or a credential. Compiler `1.1.0` also generates the UI rendering
requirement. Authoring telemetry therefore records one human-authored and one
compiler-generated requirement, without charging generated canonical material
as human input.

The Application-local provider package uses
`contracts/provider.cbs.yaml`. The deterministic compiler emits a canonical
CapabilityContract, a package-neutral BindingDefinition, and the exact
package delivery. Package admission recompiles these records and verifies their
digests instead of trusting mutable transport-manifest extensions.

The current production viability result is nevertheless `unresolved`. No
admitted `ApplicationResolution` or `ResolutionPlan` exists for this
Application. Applications correctly renders the resulting lifecycle:

```text
requirement compiled
  -> production resolution unresolved
  -> plan not created
  -> compatibility activation selected
  -> workspace publication observed
```

Stable publication used the existing Application compatibility lifecycle. This
is a valid stage-one integration proof, but it is not a native CBS activation or
provider-reuse result.

## Setup, Permissions, and Secrets

Trial required explicit approval of exactly four permissions:

- `providers.google.gmail`;
- `storage.relational`;
- `workspace.read`;
- `workspace.write`.

Declared, statically inferred, and observed permissions are identical. The
Applications Setup and Access views display all four permissions, the external
provider and destination, the `gmail.modify` scope, data practices, retention,
and the publication verification report.

Setup is intentionally `action_required`: placement and all permissions are
ready, while the required `google.gmail` connected account is missing. OAuth
client configuration is not present on this machine, so `begin_connection`
returns the typed `google_oauth_not_configured` domain outcome. The vault key is
already provider/user/account scoped, but no real token was created and the
external Gmail happy path was not claimed.

## Runtime and UI Evidence

The release suite passed `101` tests. The stable browser run exercised all nine
Gmail data-source calls through the installed provider package. Every call
returned HTTP `200` with the deterministic disconnected state. Ten widgets
rendered in wide and 390-pixel layouts without horizontal overflow.

Applications independently showed the installed `0.1.4` release, completed
Builder beta stages, exact setup requirements, permission and privacy records,
and verification evidence. Its wide and compact reviews completed with no API
or browser failures.

Run evidence is retained locally under:

```text
e2e/artifacts/gmail-mail-client-beta-20260924/
```

The evidence includes wide and compact Gmail screenshots, Applications Setup
and Access screenshots, browser review JSON, the lifecycle projection, and the
exact stable acceptance record.

## Runtime Debt Closed During the Proof

The proof also exercised the shared runtime and AdaOS Drive installation. Two
runtime degradations were fixed before accepting the result:

1. repeated installed-skill status scans no longer call filesystem `realpath`
   for roots already normalized by the runtime path authority;
2. `device.register` now acknowledges the control-plane identity before slow
   Yjs presence and catalog projections.

Before the second fix, a healthy browser could exceed its 2.5-second command
deadline during projection work, report a false registration failure, and
replay the command. After a clean `api serve` restart on port `8777`, full boot
readiness, and a repeated stable Gmail browser run, the browser console was
empty and all data-source calls passed. The complete Yjs gateway test module
also passed.

Startup still performs substantial skill import and projection work after the
HTTP listener opens. Browser automation must wait for `/api/ping` to report
`readiness.ready=true`; an HTTP `200` alone is not workload admission. Further
startup optimization is useful but is not a correctness blocker for this
stage.

## Builder Qualification

Builder retains the compact intent, prototype acceptance, Trial, and lifecycle
records and presents the result correctly. However, seven autonomous
implementation attempts did not complete the Application. The retained
implementation was completed through an explicit governed manual takeover and
recorded as the eighth Automation iteration.

Consequently, this proof validates the artifacts and lifecycle that Builder
must produce, not reliable autonomous prompt-to-beta construction. Automatic
Application modernization is also intentionally outside this qualification.

## Stage-One Decision

Stage one passes with explicit qualifications:

- **passed:** package-neutral semantic authoring and deterministic compilation;
- **passed:** application-local provider package and typed Core-owned Gmail
  boundary;
- **passed:** beta, Trial, permission verification, stable acceptance,
  workspace selection, Setup, Applications UI, and disconnected runtime E2E;
- **not exercised:** real Google OAuth and Gmail API effects;
- **not proven:** autonomous Builder construction from the initial prompt;
- **not proven:** admitted native CBS resolution, planning, and activation;
- **not proven:** portable registry reuse, independent provider delivery, or a
  second consumer sharing an account attachment.

The next experiment may begin only by making those missing identities explicit;
copying this Application's generated contracts or credential material into a
second package would not count as reuse.

## Next Gate: Reusable Mail Capability

The second stage should:

1. curate `mail.messages.manage` as an independently governed portable contract
   with a local registry reference;
2. deliver the Gmail binding independently from either consuming Application;
3. create freshness-aware conformance and external-dependency evidence;
4. have the resolver admit an exact ApplicationResolution and create an exact
   ResolutionPlan before activation;
5. configure Google OAuth and prove the real Gmail path;
6. create Mail Manager as an independent semantic consumer and let Builder
   select the existing contract and binding;
7. attach the existing provider/user/account secret to the second consumer by
   explicit consent, without copying it;
8. verify both Applications' effective permissions and account attachments in
   Applications;
9. rerun CBS9 using human-authored, inferred, and generated material as separate
   telemetry dimensions.

Legacy compatibility activation cannot be removed yet. Removal becomes safe
only after active Applications use admitted resolutions and plans, v2 locks and
writer fencing cover their state, recovery has been exercised, and telemetry
shows zero use of the corresponding legacy write path for two release cycles.
