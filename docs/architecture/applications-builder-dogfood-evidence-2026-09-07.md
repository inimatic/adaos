# Applications Builder Dogfood Evidence - 2026-09-08

Status: Prototype `027` accepted; Automation not started.

This record covers creation and iterative UI development of the protected
Applications system Application through the managed Builder path. It proves an
accepted executable Prototype and exposes the remaining product and lifecycle
work. It does not prove Automation, Trial, prerelease, stable publication, or
replacement of Infrastate Inventory.

## Managed Identities

- Development Ticket inspected and resolved for the preparation defect:
  `dticket.01M1X1X4F3JB8QSYHHBFNYDQYM`, evidence `commit:98bf72e83`.
- Application development operation:
  `appdevop.d80be62139d3cc53fd11282bf907aae2`.
- Governed catalog-metadata operation:
  `appdevop.340c4106a5e15c171f2c5e2a5676d702`, `succeeded`.
- Builder session: `builder_session_f50dcf8e`.
- Source/authoring destination: Webspace `desktop`, scenario `builder`.
- Prototype destination: Webspace `desktop-dev`, scenario `applications`.
- Current UI revision: `027`; workflow generation `48`, governed state
  `automation_ready`, stable `true`, Automation `not_started`.
- Prototype acceptance:
  `acceptance:builder_change_96bd953f:027:324b392f85df8477`, digest
  `sha256:324b392f85df84779173ca89bfc9f53e6739e7b340e61cadef0844e9f8a8b12a`.
- Externalized context packet:
  `sha256:391b5b7b3b32af7c657f79c1dc93844403402864cbfcc35ad9647c69f6d42c9a`.

The Application aggregate is at revision `2`: create plus the bounded metadata
correction. Catalog reads and `Open in Builder` do not create Applications,
Builder sessions, or development operations.

## Prototype Result

Revision `027` provides:

- a wide three-zone workbench and compact drawer/stacked rendering;
- `Marketplace`, `Installed`, and existing-only `My developments` catalogs;
- selected Application identity and bounded detail instead of raw payloads;
- direct user commands `Install`, `Update`, `Save update settings`, and
  `Uninstall`; planning and the exact reviewed receipt remain an internal
  safety boundary rather than user-facing command terminology;
- prerelease-following and automatic-update toggles, defaulting respectively
  to `false` and `true` for a new installation intent;
- `Details`, `Versions`, `Operations`, and `Reports` tabs;
- `Installation`, `Marketplace`, `Categories`, and conditional
  `My development` metadata;
- current Builder phase, status, revision, and exact existing-source link;
- scenario-owned `assets/i18n/en.json` and `assets/i18n/ru.json` dictionaries,
  including lifecycle controls and representative fixtures in both locales;
- install, update, prerelease-following, protected-system, and reviewed-install
  representative states without claiming that the SDK operation has executed.

The application-manager capability catalog version is `1.3.24`. All twelve
postconditions pass. Acceptance binds the exact `webui.json` digest and the
two-locale resource bundle digest
`sha256:142ddffd7fc9db3e8bcb85a15c7773ee92ab151580ba6334a3c4ceed544222bc`.
Changing a declared locale dictionary now makes acceptance stale.

## Generic-Template Prototype Experiment

On 2026-09-08 `applications_phased_prototype_experiment_c118d053` was created
through Builder chat from the universal `scenario_default`. It contains no
subject-scoped scenario template and no inherited `development.initiator_ref`.
Builder session `builder_session_5797dc5f` then composed the Applications UI in
six cumulative phases: catalog shell, Application detail, lifecycle controls,
reviewed operations, representative fixtures, and localization qualification.
Revision `013` first satisfied the final structured review order without model
inference. Revisions `014` through `018` are semantically identical performance
probes; current revision `018` satisfies every cumulative recipe postcondition
and the exact EN/RU key-set check. It remains deliberately unaccepted;
Automation, Trial, implementation, and publication were not started.

This experiment supersedes the earlier `applications_minimum_proven_20260908`
run as evidence of Builder generality. That retained diagnostic used a
subject-scoped materializer and therefore cannot prove that Builder can derive
Applications from generic inputs. No `application_manager` scenario template
or zero-model Applications materializer remains in Core. The recipe now
provides versioned capability compositions, phase dependencies, canonical
vocabulary, and machine postconditions, but not a ready-made WebUI or locale
dictionary.

The measured path is adaptive rather than one fixed request sequence. A narrow
change may select only the affected phase; initial construction traverses the
cumulative dependency graph. Each model call receives three messages: the
system contract, a deterministic cacheable Builder/ABI/active-phase context,
and a dynamic request containing current WebUI, latest semantic delta, bounded
history, runtime context, and the user instruction. Exact request and candidate
artifacts are retained beside the scenario before activation.

The phase run also establishes the practical limits of the current
composition. Prefix caching was effective for the reviewed-operations phase,
while fixture and localization repairs repeated enough current WebUI and
candidate state to consume 56-70k aggregate input tokens. A stored candidate
was subsequently validated and normalized into revision `009` with zero new
model tokens; one narrow locale-quality correction produced revision `010`.
Semantic-delta repair is useful follow-up optimization, but it is not evidence
for bypassing the model with a subject-specific template.

Builder skill `0.3.124` introduced a bounded creation receipt instead of embedding
the complete Preview state, workbench projection, and domain operation in the
chat response. The full state remains available through existing reads, and a
regression fixture keeps the receipt below 10 KiB even when stored state exceeds
100 KiB. Builder skill `0.3.125` moves atomic project selection behind the
public Builder SDK boundary. The qualified experiment currently uses DEV
Builder `0.3.145`.

An earlier deleted diagnostic, `applications_minimal_experiment_20260908`, left
a stale Builder selection and previously surfaced as `The installed skill
runtime is out of sync with its tool manifest`. Builder now returns an ordinary
terminal `not_found` projection for missing Projects, skills, and scenarios,
and renders a bounded recovery action instead of treating absence as a
reconnecting data source.

The same failure was exercised through direct browser addressing with
`expected_scenario_id=applications_minimum_20260908`, another intentionally
absent ID. The current home scenario remained rendered, a localized system
dialog identified the missing scenario and listed bounded available
destinations, and neither reconnect nor tool-manifest diagnostics appeared.
Core also reacts to `scenario.removed` for every affected Webspace: it replaces
a deleted home, switches away from a deleted current scenario, and falls back
to the built-in `web_desktop` when no installed candidate remains. This makes
the no-home case a system-owned 404 surface rather than a scenario-owned view.

The dogfood revisions exposed reusable platform defects rather than
Applications-local workarounds:

- Required Actions truncated submitted Review text before it became a Dev
  Ticket; the client now preserves and copies the complete submitted note;
- the details renderer unwrapped a one-object operation envelope even when
  configured paths targeted that envelope;
- canonical materialization may reduce a one-widget modal array to a singleton
  object, which the modal renderer previously discarded;
- full acceptance plus repeated context/history exceeded the workflow state
  bound; history now stores acceptance identity only and a large digest-bound
  context packet is stored outside `prompt_state.json` and verified on load;
- canonical Builder Preview IDs (`preview-<12 hex>`) were not recognized as
  Prototype Webspaces by the Client, so valid fixtures were ignored in favor
  of live MCP reads;
- nested Builder update calls and their owning chat call could both persist the
  same completion message; the outer chat boundary now owns one result emit;
- locale qualification compared structure but did not reject canonical English
  fallbacks copied into the Russian dictionary; exact key-set and vocabulary
  checks now reject that result before activation.

Final publication checks exposed two independent Root defects rather than an
LLM generation delay. Draft VCS requests first met the default Express JSON
body limit and then the proxy limit. The authenticated route now installs its
70 MiB parser before the default parser, and nginx admits the same bounded
payload class. A separate release failure came from JavaScript `localeCompare`
ordering schema-lock IDs differently from Python code-point sorting; Root now
uses an explicit code-point comparator. Backend commits `84e4494`, `2887cdf`,
and `132d5a8` cover the fixes. Infra run `34179192169` deployed
`backend-132d5a8` healthy to both RU and EU, after which the unchanged
`research_platform@0.1.6` release received `201 Created`.

The minimum-path diagnostics later isolated a separate transient failure:
RU Root completed the local Forge commit but its configured resolver timed out
resolving `github.com`. This was not a request-size or `ru.api.inimatic.com`
proxy failure, and the next unchanged checkpoint succeeded after DNS recovery.
Root backend now retries only bounded, classified Git network failures across
token refresh, fetch/rebase, and push; authentication and conflict failures
still fail immediately.

## Performance Diagnostics

The experiment separates network/model latency from local build and lifecycle
work. Structured `move_before` review operations now use stable component refs
and require zero model calls. Excluding model inference, the best observed
Builder handler path fell from about `15.2 s` to `9.25 s`: its synchronous Forge
checkpoint fell from `6.48 s` to `0.33 s`, and finalization from about `10.5 s`
to `4.93 s`. An unchanged DEV runtime preflight fell from `4.47 s` to `0.85 s`
after source-digest/mtime gating. Workflow projection still varies and remains
instrumented follow-up work.

AdaOS cold process start remains too expensive for an interactive loop. In the
latest local run, application startup to runtime readiness took about `34.3 s`,
including `13.57 s` for 49 skill handler imports; process launch plus imports
before application startup made the observed cold wall time roughly `41-51 s`.
Noncritical catalog/status hydration and lazy skill loading need a formal
readiness budget rather than more timeout tolerance.

The Client was migrated from the legacy browser/webpack build to
`browser-esbuild`. A previous production build took about `646 s`; after moving
Ionicons to on-demand static SVG delivery and the optional CV/TensorFlow
renderers behind the existing asynchronous widget loader, the Node 20
production bundle phase takes `71.35 s` (`84.12 s` command wall time) on this
Windows machine. CPU profiling found a local monolithic dependency graph and
repeated Node `stat/lstat` module resolution, not a Root request timeout. The
source has 228 TypeScript files while `node_modules` contains about 119k files.
The initial production bundle fell from about `6.49 MiB` to `4.53 MiB`; the
`1.13 MiB` CV/TensorFlow payload is now a lazy chunk. All 30 visible icons were
verified in the browser, including 200 responses for dynamically named icons.

Karma now builds through Angular's application builder with `zone.js/testing`
sequenced as a test polyfill. Cold complete browser-suite samples completed in
`68.66-85.01 s`; the final run built in `35.26 s` and executed `1287` tests in
`13.90 s`. It no longer creates the two legacy webpack cache entries that
occupied about `1.46 GiB`. The warmed development server has demonstrated a
`5.49 s` incremental rebuild; cold production qualification remains separate
from the normal edit loop. The legacy `angular-eslint 17` builder remains
incompatible with Angular 19 (`Workspaces is not a constructor`) and needs a
separate tooling upgrade before lint can be a qualification gate.

The final registry checkpoint ran `adaos project push` for all 26 Workspace
project manifests. Five releases were accepted from revision `17f4d6fd`; the
remaining 21 were accepted from `acd0c893` after advancing versions that were
already occupied at Root. All 26 immutable releases are now present at Root,
and both source revisions are ancestors of the final registry head. The sweep
also exposed that local release-cache occupancy is not sufficient to predict a
Root version conflict; a future batch publisher should preflight remote version
occupancy before building archives.

The DEV sweep discovered 177 projects and successfully checkpointed 34 viable
product and experiment projects through `adaos dev project push`, including
Applications, all three Applications prototype experiments, and Builder. The
remaining 143 projects are not silently omitted: 137 generated TLP calibration
fixtures and six older research projects all reference the retired dependency
identity `project:adaos_research_platform`. An automatic rename would be
incorrect because some require `^0.2` while the successor
`project:research_platform` has only `0.1.x` releases. Repair or archival is
separate research-data migration work tracked as non-blocking
`dticket.01M1ZDCH5A8J4RCHQ9TR4S8A9E`.

The final Builder release rehearsal used the complete Project lifecycle. The
owned scenario and two skills were checkpointed under
`builder_change_applications_dogfood_20260908`, ProjectRelease `builder@0.2.104`
was pushed, candidate `builder-0-2-104-73769a3f3733` was materialized under
`.adaos/trials`, explicitly accepted, and promoted into Workspace lock revision
`44`. DEV and Workspace then reported the same source revision
`sha256:d3112b55aad6f4187936cc1b50bb128deaeaeb21f8a2d1d33778616c78ef6118`;
runtime health confirmed `builder_skill@0.3.145` and
`builder_sdk_control_skill@0.1.105`. An initial `push -> trial` attempt failed
correctly because the primary Forge checkpoint was stale. The required order is
`checkpoint -> push -> trial -> trial-decide -> promote`; the CLI should expose
that stale-checkpoint precondition before spending work on a release push.

Builder DEV revision `059` also replaces the old `push -> stabilize` Prototype
approval chain with one evidence-bearing `accept_prototype` command. Its modal
collects behavior, compact screenshot, and wide screenshot references before
the governed transition.

## Verification Ledger

| Gate | Result |
| --- | --- |
| Application/Builder/UI Python regression group | passed |
| Complete browser-client Karma suite | passed, `1287/1287`; application-builder path |
| Capability qualification | passed, `12/12`, catalog `1.3.24` |
| Formal Prototype acceptance | passed, revision `027`, governed `automation_ready` |
| Generic-template Prototype path | qualified revision `018` through six cumulative phases and structured review; not accepted |
| Model input/output evidence | exact requests, options, candidates, usage, and replay artifacts retained per job |
| Candidate replay | valid stored candidate normalized without a second model call; incremental tokens `0` |
| Generic-path browser matrix | passed in EN/RU at 1440x1000 and 390x844; no document/control overflow or reconnect state |
| Lifecycle fixture actions | install exposes install only; update/prerelease expose update, track, remove; protected exposes track only |
| Chat result ownership | passed; nested update produces one persisted completion message |
| Missing direct-navigation target | passed; localized home-preserving chooser, no reconnect or manifest-mismatch diagnostic |
| Removed Project/skill/scenario | passed; terminal Builder projection plus Webspace current/home recovery |
| Locale resource binding | passed, exact EN/RU bundle is acceptance evidence |
| Wide browser layout | passed; 380px catalog, flexible detail, 300px metadata |
| Compact browser layout | passed; no document horizontal overflow |
| EN/RU browser rendering | passed at 1440x1000 and 390x844; no document or control overflow |
| Existing-development navigation | passed; new window targets `desktop/builder` with Applications selected |
| Catalog metadata | passed; concise summary plus `System` and `Management`, original creation prompt absent |
| Metadata recovery | passed; a lost response replays as `succeeded/duplicate` without a second revision |
| Installed fixture | passed; installed `1.2.0`, stable `1.3.0`, prerelease `1.4.0-beta.2` |
| Installed controls | direct update, settings, uninstall, prerelease, and auto-update controls follow exact fixture state |
| Review detail | passed; exact operation, summary, and entitlement/permission snapshot remain visible |
| Builder acceptance modal | passed; canonical singleton form renders all three evidence fields |
| Workflow persistence | passed; `prompt_state.json` is 313,115 bytes and the 182,541-byte context packet is digest-addressed |
| Applications Dev Tickets | no unresolved/in-progress ticket remains; seven addressed tickets are `verified` and await normal user closure |
| Browser runtime errors | none; expected local config/probe/fallback transport noise only |
| Root draft transport | passed at 50, 150, 500, 244, and 805 KiB through `ru.api.inimatic.com` |
| Root release canonicalization | passed unchanged retry of `research_platform@0.1.6` after RU/EU deployment |
| Registry project checkpoint | passed; 26/26 manifests published from `17f4d6fd` or `acd0c893`, both reachable from the final registry head |
| DEV project checkpoint | 34/177 pushed; 143 historical research/calibration projects blocked by retired `project:adaos_research_platform`, explicitly classified for migration or archival |

Relevant final ownership repairs are client `92f61f2` and `2c24867`, and AdaOS
`1d853f613` and `d5cb2e2d1`; the earlier revision history remains in the same
branches. Root/backend and registry changes were pushed only after their local
regressions and publication probes passed; client and AdaOS follow the same
final gate.

## Critical Backlog

### Must

- start Automation only from the exact accepted revision and locale/resource
  evidence; reject any stale UI, data, or locale digest;
- run real browser intent, receipt review, and apply operations for install,
  update, track selection, and remove; cover stale revisions, failed apply,
  restart, and reconnect recovery;
- complete the immutable Trial, link install, exact-digest stable publication,
  clean-subnet install/update/remove, and Development Report round trip;
- retain Infrastate Inventory until the accepted Applications flow provides
  equivalent behavior.

### Should

- verify keyboard navigation, focus order, accessible names, long localized
  publisher/application text, and narrow-screen drawer interaction;
- add representative real Catalog entries and operation reconnect evidence;
- expose richer permissions, dependencies, migration/backup, release notes,
  publisher fingerprint, and report status without making components the
  default product model;
- reduce repair payloads through semantic deltas and digest-bound journal
  references without weakening exact replay evidence;
- require an ABI-impact declaration and affected Client component/browser
  evidence whenever the capability catalog changes;
- keep remaining command results compact by default, including Prototype
  acceptance; expose full workflow/evidence only through an explicit inspect
  operation;
- improve model repair with semantic patches and deterministic postcondition
  hints for noncanonical edits, while preserving the complete prior candidate;
- migrate or explicitly classify historical generated TLP calibration
  projects that still depend on `project:adaos_research_platform`, so bulk DEV
  checkpoint reports distinguish product sources from retained experiment
  fixtures;
- add an opt-in, bounded visual revision gate after deterministic checks:
  capture compact and wide browser screenshots, permit at most two model repair
  attempts, and emit a targeted Core/client/ABI Development Ticket if the
  result still fails.

### Could

- add saved Catalog filters and locally pinned detail sections;
- add optional compact badges for update availability and development stage
  after terminology is validated with real catalogs.

### Deferred

- Root Guard and signed malware-scan receipts;
- ownership transfer, publisher succession, and multi-user development;
- foreign beta proposals, multiple canonical beta lines, and global
  prerelease search;
- simultaneous side-by-side shared component versions and multi-Root failover.

## Handoff Boundary

Primary Prototype work is complete at accepted revision `027`. The separate
generic-template experiment is qualified at revision `018` and intentionally
unaccepted; it proves the Builder composition path without creating another
release candidate. The next valid phase for Applications is Automation from
revision `027`, followed by real SDK/MCP operation proof, Candidate, isolated
Trial, and release gates. No Trial or publication claim is made by this record.
Infrastate Inventory is intentionally unchanged until the implemented
Applications flow reaches its separate replacement gate.
