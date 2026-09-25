# Gmail CBS Third-Consumer Builder Proof

Status: validated-local beta proof with clean-subnet distribution follow-up,
2026-09-25.

Follow-up: the optimized matched authoring run is recorded in
[Gmail CBS Fourth-Consumer Matched Builder Proof](gmail-cbs-fourth-consumer-builder-proof-2026-09-25.md).

This checkpoint exercises the complete Builder-to-workspace path for a third
consumer of `capability:mail.messages.manage@1.0.0`. It follows the
`gmail_cbs_cleanroom` provider Application and the independent `inbox_triage`
consumer. The product is deliberately smaller than either: a desktop-first
mail reader with folders, bounded message summaries, lazy detail, Back, and
only read/unread, star/unstar, and archive actions.

The proof validates the local reusable CBS rail and exposes the remaining
autonomous-development and first-paint costs. It does not claim a federated
capability registry, arbitrary provider portability, or a one-shot Builder.

## Result

Builder created `mail_focus_reader` from a new prompt with the exact technical
Application id confirmed before Automation. The semantic Application requires
the portable mail capability and does not implement or own Gmail API, OAuth,
credentials, or mailbox state. The admitted release selected the existing
provider delivery and reused the Core-owned Gmail connection.

| Fact | Exact result |
| --- | --- |
| Application | `mail_focus_reader@0.1.2` |
| Prototype revision | `009` |
| Candidate | `mail_focus_reader-0-1-2-17975814e387` |
| Application release digest | `sha256:7cf06faa021fc60bf165b8902fec1695f79c1001c067a45a598617975814e387` |
| Application package digest | `sha256:1a07e0d95b0100e48c3ce72cbe899e8a92f147548962b3a7aebbf3645f0f81e4` |
| CBS compilation digest | `sha256:efd03b68a43b70ee3ebbecf30e914b0d1c73394fe194a9e5e829800ecdbd8328` |
| CBS admission | `admitted`, 2 of 2 requirements, no unresolved requirement |
| Capability binding | `binding-definition:mail.messages.manage.google-gmail-local` |
| UI binding | `binding-definition:application.ui.render.webui-v1` |
| Trial | accepted with immutable candidate runtime authority |
| Publication | `published` |
| Stable runtime | `desktop`, `stable_installation`, revision 2 |
| Workspace lock | committed publication at workspace revision 78 |

The original Builder proof above remains pinned to `0.1.2`. A subsequent
source-preserving release published `mail_focus_reader@0.1.3` as Application
release
`sha256:310f01a72ffd473d65348f0146210c8198eb9b9454e886d4635681e858980599`,
with scenario package
`sha256:2b5f8b6ba8b935eaff6ee37599a3a38953d7d52bcb9bad136559a03699e51661`
and the same independently delivered Gmail provider package
`sha256:403e924b61e66ae9d04aa69a4594d3fe2873349f75904c5633e3e01212c0df19`.

The current canonical release is `mail_focus_reader@0.1.4`, digest
`sha256:dcf069de8867b654d79ef17d6278b67d450a84bfc53eb2e7650fd615230fe53d`.
It replaces the invalid legacy `on_install` spelling with the supported
`grant_on_install` policy for all four declared permissions. Builder now
rejects unknown approval policies before packaging. On a clean subnet the
policy correction correctly required reviewed application because it expands
install-time authority; after review, installation revision 5 is active and
native CBS is `compiled -> admitted -> ready -> active -> committed` with both
requirements resolved. This does not weaken the earlier `0.1.2 -> 0.1.3`
automatic-update proof, which remained a compatible no-review transition.

Applications projects the CBS lifecycle as `compiled -> admitted -> ready ->
active -> committed`. The projection is explicitly derived; the admitted
resolution, plans, activation journal, runtime selection, and WorkspaceLock
remain authoritative in their owning stores.

## Provider And Credential Reuse

The consumer contains no Gmail implementation. It uses the exact shared
`gmail_cbs_cleanroom_skill` delivery and the existing explicitly attached
`google.gmail` account. Credential material remains in the Core vault and does
not appear in the Application, manifest, UI state, evidence, logs, or this
proof.

Applications displays exactly these required permissions:

```text
providers.google.gmail
storage.relational
workspace.read
workspace.write
```

It also displays one required `google.gmail` external provider with the
`gmail.modify` scope and a connected account status. The projection exposes no
secret or token.

The stable provider probes returned 21 labels and 25 bounded message summaries.
The latest warm measurements were 2.068 seconds for labels and 3.209 seconds
for the summary page. Full message content was requested only after explicit
selection. This replaces the earlier approximately 26-second N+1 metadata path
with provider-owned Gmail HTTP batch metadata and progressive pagination.

## Browser Evidence

The published Stable release was exercised against the real local runtime on
port 8777 and the real connected provider:

- at 1440 by 1000, folders and 25 message rows rendered, selecting a message
  triggered one lazy `portable_get_message` call with HTTP 200, detail replaced
  the list, and Back restored the list;
- at 390 by 844, the responsive first paint rendered 25 message rows without
  horizontal overflow;
- no renderer page error, console error, access denial, ambiguous runtime
  context, or failed provider request remained;
- every shared-skill request carried `scenario_id=mail_focus_reader` from the
  first request.

The observed Stable first paint was approximately 28.1 seconds wide and 17.1
seconds compact. This includes Webspace materialization, duplicate initial
data-source scheduling, Core admission, skill startup, and live provider IO.
The provider itself is no longer the old 26-second bottleneck, but the complete
first-paint path is still too slow for production polish and remains measured
performance debt.

## Clean-Subnet Publication And Auto-Update Follow-Up

The public registry revision `7e898cb156b8381d57edd14f590eaf92f6d2df6f`
contains the exact `0.1.3` release and its portable CBS projection. A separate
subnet at `192.168.0.30` imported a local portable catalog with 11 immutable
identities/records, including `capability:mail.messages.manage@1.0.0`, its
Gmail binding definition, and the exact provider delivery. Applications
`0.1.36` and Desktop `0.3.52` were installed from the same public registry.

After the subnet initially installed `mail_focus_reader@0.1.2`, the post-ready
registry synchronizer found `0.1.3` and the auto-update service ran the normal
reviewed plan/apply machinery. Operation
`appop.31a9057df62bb87719506bb3e8ffbca0` completed fetch, verification, staging,
activation, health and commit for all three release components, and advanced
the installation revision from 3 to 4. No direct update command was used.

The clean subnet has no Gmail OAuth secret or connected account. Provider reuse
discovery now returns an ordinary empty result without opening an unconfigured
credential vault, while mail operations return the actionable
`account_not_connected` result. This is the required authority boundary, not a
provider outage: portable contracts, packages and evidence cross the registry;
credentials do not. The remaining `CBS10-07` proof step is to provision that
subnet's own Google OAuth client/account and attach it to the imported binding.

The public notification path is also live. The deployed signed registry
webhook delivered registry revision
`54b7cddc7cd2731c8a38a9110e5cc3dbd6a89827` through global Root and the RU
zone to the clean subnet. Its durable auto-update receipt completed with zero
candidates and `mail_focus_reader`, `applications`, and `web_desktop` all
classified `already_current`.

## Builder And Platform Corrections

No low-level edit was made to the `mail_focus_reader` consumer source after the
reviewed Automation task started. Corrections were made through Builder
iterations and through reusable Core/client rails when the run exposed a
platform defect. This is not a manual takeover of the Application, but it is
also not autonomous one-shot development.

The run caused the following systemic corrections:

1. Automation now requires an exact technical Application id confirmation
   before the model starts. The previously mistaken empty
   `mail_inbox_triage` project was archived by a governed reversible command.
2. Builder projects package registry references and bounded compiler views
   instead of repeating complete delivery artifacts. In the measured mail
   context this reduced the relevant view from 60,266 to 48,980 bytes and from
   18 to 10 exposed tools, with no consumer data routes copied.
3. Shared delivery views now include contract-stability and provider behavior
   guarantee provenance, so the consumer can reuse an implementation without
   authoring compiler-owned canonical contracts.
4. Continuations preserve and reseal owned tests, effects, conformance
   evidence, and exact release contracts instead of losing already valid work.
5. Trial project closure is inferred from the Application composition before
   component validation. The previous component-only path incorrectly treated
   compiler-owned CBS outputs as consumer-authored files.
6. A shared skill delivered to several Applications is authorized by the
   materialized Scenario/Application entrypoint. Client data-source calls carry
   that hint, including during first paint before the materialization snapshot
   arrives. Server resolution still verifies it against installed release
   authority.
7. Terminal publication replay no longer trusts `WorkspaceLock` alone. If an
   immutable package directory or its retained development projection is
   missing, Core restores the exact promoted CAS objects under the Workspace
   writer lock, verifies them, and repeats runtime reload and health admission
   before reporting success. This closes the observed `scenario_not_found`
   failure where Applications was installed and locked but its physical
   Scenario directory had disappeared.
8. The post-ready Builder catalog prewarm keeps automatic cyclic collection
   suspended only for its bounded executor offload, then collects and restores
   it on the event-loop owner thread. A plain JSON allocation in that worker
   previously triggered finalization of an unrelated cyclic `YDoc` there and
   degraded Yjs despite the catalog never accessing Yjs itself.

The first Trial attempt failed closed because the lifecycle command omitted
the publication Project reference and entered component-only validation. The
second attempt correctly required an explicit permission decision. After the
project-closure correction and structured decision, the same governed Trial,
acceptance, publication, and workspace path completed without bypassing
authority.

## Autonomous Development Assessment

The product result is beta-quality. The development process is not yet
reliable or efficient enough to call one-shot, or even one-chat-correction.

The workflow records 11 Automation iterations. Nine model runs completed a
turn; three additional scheduled tasks stopped before a model turn. The nine
model turns consumed 4,254,789 reported input tokens, of which 3,840,384 were
cached, plus 26,322 output tokens. Fresh input plus output was 440,727 tokens.
The high cached share confirms that much of the expense was repeated context,
but fresh work is still excessive for this product size.

Most repeated iterations were platform convergence rather than product
authoring: stale continuation state, incomplete evidence resealing, missing
delivery provenance, contract-stable dependency publication, and candidate
browser feedback. The final model turn was small (34,644 fresh input plus 927
output tokens) after the compact views and continuation rails were in place.

Reliability assessment:

- exact Application identity, no mistaken sibling project: **passed**;
- Application source produced through Builder/chat without low-level consumer
  takeover: **passed**;
- reusable contract, binding, package, and provider-owned credential:
  **passed**;
- native CBS admission, Trial, Applications review, publication, workspace,
  and Stable runtime: **passed**;
- real provider list and lazy detail browser journey: **passed**;
- one-shot or one-chat-correction development: **not passed**;
- production-quality first-paint latency: **not passed**.

## OAuth Documentation

The `gmail_cbs_cleanroom` Project README contains the complete tested local
setup: enable Gmail API, configure External/Testing audience and test users,
request `gmail.modify`, create a Web application OAuth client, and register
this exact URI without a trailing slash:

```text
http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback
```

It directs the user to Applications Settings for the client id and secret and
states that AdaOS stores them in the local credential vault. The loopback URI
remains the current beta path. Public routing through `inimatic.com` is
specified separately by
[External Integration Ingress And Public Callback Gateway](public-integration-callback-gateway.md)
and is not claimed as implemented by this proof.

## Next Actions

1. Reduce Stable first paint by deduplicating initial labels/messages loads and
   measuring Webspace materialization, access resolution, skill startup, and
   provider IO separately.
2. Convert the remaining full Builder workflow projections to digest-addressed
   registry views and stop retrying unchanged evidence phases after an exact
   platform failure has been classified.
3. Add a matched fourth consumer run only after those two changes; the target
   is one model turn plus at most one chat correction, not another sequence of
   blind retries.
4. Implement the public callback gateway `must` slice: typed ingress
   declaration, opaque endpoint materialization, signed state correlation,
   relay, replay defense, redacted observability, and loopback fallback.
5. Keep the current Gmail provider and Applications permission projection as
   regression fixtures while proceeding to the CBS9 matched benchmark.
