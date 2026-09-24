# Gmail CBS Reuse And Builder Beta Proof

Status: validated-local beta proof, 2026-09-24.

This checkpoint proves the first local two-Application reuse path for
`capability:mail.messages.manage@1.0.0`. It is deliberately narrower than a
federated capability registry, automatic capability extraction, or a claim that
Builder development is already reliable in one attempt.

## Result

The previously published `gmail_cbs_cleanroom` Application delivers
`gmail_cbs_cleanroom_skill@0.1.4`. A second Application, `inbox_triage@0.1.4`,
was created from a chat prompt and implements a different product: unread-mail
triage rather than a general mail client. Its semantic requirement remains:

```text
capability:mail.messages.manage@^1.0.0
```

The semantic Application does not name Gmail, the provider skill, a package,
an account, credentials, an endpoint, or a physical member. The exact release
materialization selected the already published package instead of copying its
provider implementation.

The admitted release facts are:

| Fact | Exact result |
| --- | --- |
| Application release | `inbox_triage@0.1.4` |
| Application release digest | `sha256:d6e3b4809c1e2ec531acdf40ae6f858a5f51340113d2bf54ca53d3a12fd92b8a` |
| CBS admission | `admitted`, 2 of 2 requirements |
| Capability contract | `capability:mail.messages.manage@1.0.0` |
| Binding definition | `binding-definition:mail.messages.manage.google-gmail-local` |
| Binding-definition digest | `sha256:5398d3562ed19cb375a2ca1372dbfa1c049f84a6cc8873491ee1282196a54eb2` |
| Shared package | `gmail_cbs_cleanroom_skill@0.1.4` |
| Shared package digest | `sha256:84bf028c8898dec699447552ee63398bb314620cfec0f4c5c4210470a8ce74df` |
| Workflow result | Trial accepted, publication `published`, installation `active` |

Compilation remains context-free and may report production requirements as
unresolved before package resolution. The immutable release admission is the
authority that proves the exact package closure and evidence; it is admitted
with no unresolved requirements.

## Credential Reuse And Application Access

Applications derives and displays exactly these release permissions:

```text
providers.google.gmail
storage.relational
workspace.read
workspace.write
```

The second Application initially had no connected account. Its read-only
discovery returned the existing redacted `google.gmail` connection with
`attached=false`. An explicit attachment through the shared provider returned:

```text
attached=true
reused_credential=true
```

Applications then recorded one connected account for `inbox_triage`, scoped to
`gmail.modify`. The projection exposes provider, opaque account id, subject,
scope, status, expiry and revision. It exposes no client secret, access token,
refresh token, or credential payload. OAuth and credential authority remain in
Core; attachment did not copy a secret and did not modify mailbox data.

A delegated read-only query for unread messages older than seven days returned
25 bounded projections. Every result contained the fields needed by the triage
UI: sender, subject, date and snippet. No mutation tool was invoked. The call
took about 26 seconds, which is functionally correct but remains a beta latency
defect.

## Builder And Browser Evidence

Application source changes were produced only by Builder Automation. No
low-level edit was made to either Application to reach the result. The final
dependency refresh changed four files in the consumer envelope, preserved the
accepted WebUI and semantic CBS requirement, and passed 21 hermetic tests.

Trusted browser feedback passed at 1440 by 1000 and 390 by 844. Both layouts
settled on the reusable-account state without renderer, console, page, or hard
failures. The Trial package then passed native CBS admission and was promoted by
the normal `accept_trial` and `begin_publication` transitions.

The run exposed one Core integration gap. Application runtime authorization
recognized only skill packages in `ProjectRelease.components`; a shared CBS
delivery appears in `ProjectRelease.resolved_dependencies`. Consequently the
resolver admitted the release but the first runtime call failed with
`application_context_invalid`. Core now treats exact resolved skill
dependencies as delivered runtime components. The regression is covered in
`tests/test_application_access_management.py`; the focused Application access,
tool bridge, Gmail provider, and CBS suites pass 124 tests.

## Autonomous Development Assessment

The result is beta-quality, but the process is not yet a reliable one-shot
autonomous development path.

Six Inbox Triage Automation iterations were recorded. Five completed and one
failed. Several iterations were needed because the first exact provider pin was
not yet published, continuation snapshots became stale while Core was repaired,
and Application authority did not understand a reused dependency. Chat-level
correction was sufficient for Application code; platform defects were fixed in
Core rather than bypassed in the Application.

The six model runs accounted for 2,256,621 input tokens, of which 1,949,184 were
cached, plus 19,399 output tokens. Fresh input plus output was approximately
326,836 tokens. Repeated large delivery, manifest, and workflow projections are
the main remaining context cost. This is too expensive for the small final
delivery-pin repair even though the majority of reported input was cached.

Builder also materialized the product as `inbox_triage` although the initial
prompt requested technical id `mail_inbox_triage`; an empty project with the
requested id remains a reversible cleanup item. Identity selection therefore
needs a fail-closed pre-Automation confirmation before this flow is called
reliable.

Reliability assessment:

- capability reuse, Trial, publication, permissions, explicit account reuse,
  secret redaction and a live read-only call: **passed**;
- autonomous Application source implementation with chat correction only:
  **passed, multi-iteration**;
- one-shot project identity selection and dependency admission: **not passed**;
- live list latency suitable for a polished production UX: **not passed**;
- arbitrary provider portability, federated discovery and automatic contract
  extraction: **not claimed**.

## OAuth Setup Documentation

The `gmail_cbs_cleanroom` project README contains the complete current
development setup: enable Gmail API, configure External/Testing audience and
test users, request `gmail.modify`, create a Web application OAuth client, and
register this exact redirect URI without a trailing slash:

```text
http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback
```

It also directs the user to Applications settings for the client id and secret
and states that the secret is stored in the local credential vault.

## Remaining Work

1. Add fail-closed confirmation of the technical Application id and archive the
   accidentally created empty `mail_inbox_triage` project through a governed
   Builder project command.
2. Reduce the Gmail summary read latency, preferably with a provider-owned batch
   metadata request or bounded progressive pagination rather than consumer N+1
   calls.
3. Replace repeated full contract/delivery projections in Builder context with
   registry references and bounded compiler views.
4. Run a new matched CBS9 sample after this reusable inventory exists; do not
   rewrite the historical negative benchmark.
5. Generalize callback materialization through the public integration gateway
   while retaining loopback development fallback.

The public callback design is specified in
[Public Integration Callback Gateway](public-integration-callback-gateway.md).
