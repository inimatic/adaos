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
  `automation_ready`, stable `false`, Automation `not_started`.
- Prototype acceptance:
  `acceptance:builder_change_96bd953f:027:324b392f85df8477`, digest
  `sha256:395776dcc9f9e0713d5ce0deebc6f56cdf913cee1be4a304fd82a37cd56e956d`.
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

The dogfood revisions exposed four reusable platform defects rather than
Applications-local workarounds:

- Required Actions truncated submitted Review text before it became a Dev
  Ticket; the client now preserves and copies the complete submitted note;
- the details renderer unwrapped a one-object operation envelope even when
  configured paths targeted that envelope;
- canonical materialization may reduce a one-widget modal array to a singleton
  object, which the modal renderer previously discarded;
- full acceptance plus repeated context/history exceeded the workflow state
  bound; history now stores acceptance identity only and a large digest-bound
  context packet is stored outside `prompt_state.json` and verified on load.

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

The registry checkpoint ran `adaos project push` for every one of the 25
project manifests at the prior `adaos-registry/main` revision. Their immutable
releases are present at Root, and the source revisions are reachable from the
new registry head `3184787c`. Product DEV projects, including Applications and
Builder, were independently checkpointed through `adaos dev project push`.
Historical generated TLP calibration fixtures remain excluded from this
product checkpoint because they reference the retired dependency identity
`project:adaos_research_platform`; repairing or explicitly classifying those
fixtures is separate research-data migration work.
That debt is tracked as non-blocking
`dticket.01M1ZDCH5A8J4RCHQ9TR4S8A9E`.

Builder DEV revision `059` also replaces the old `push -> stabilize` Prototype
approval chain with one evidence-bearing `accept_prototype` command. Its modal
collects behavior, compact screenshot, and wide screenshot references before
the governed transition.

## Verification Ledger

| Gate | Result |
| --- | --- |
| Application/Builder/UI Python regression group | passed |
| Complete browser-client Karma suite | passed, `1279/1279` |
| Capability qualification | passed, `12/12`, catalog `1.3.24` |
| Formal Prototype acceptance | passed, revision `027`, governed `automation_ready` |
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
| Registry project checkpoint | passed; 25/25 manifests published and reachable from `adaos-registry@3184787c` |

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
- reduce full-recipe repair frequency and token cost with smaller semantic
  patch contracts and deterministic postcondition hints;
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

Prototype work is complete at exact revision `027`. The next valid phase is
Automation from its accepted handoff, followed by real SDK/MCP operation proof,
Candidate, isolated Trial, and release gates. No Trial or publication claim is
made by this record. Infrastate Inventory is intentionally unchanged until the
implemented Applications flow reaches its separate replacement gate.
