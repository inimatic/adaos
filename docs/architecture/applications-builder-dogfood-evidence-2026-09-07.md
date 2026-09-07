# Applications Builder Dogfood Evidence - 2026-09-07

Status: `prototype-candidate`, not accepted.

This record covers creation and iterative UI development of the protected
Applications system Application through the managed Builder path. It proves a
usable Prototype and exposes the remaining product and lifecycle work. It does
not prove Automation, Trial, prerelease, stable publication, or replacement of
Infrastate Inventory.

## Managed Identities

- Development Ticket inspected and resolved for the preparation defect:
  `dticket.01M1X1X4F3JB8QSYHHBFNYDQYM`, evidence `commit:98bf72e83`.
- Application development operation:
  `appdevop.d80be62139d3cc53fd11282bf907aae2`.
- Builder session: `builder_session_f50dcf8e`.
- Source/authoring destination: Webspace `desktop`, scenario `builder`.
- Prototype destination: Webspace `desktop-dev`, scenario `applications`.
- Current UI revision: `011`; workflow `prototype/working`, stable `false`,
  acceptance absent, Automation `not_started`.

Only the original managed create operation exists. Catalog reads and
`Open in Builder` do not create Applications, Builder sessions, or development
operations.

## Prototype Result

Revision `011` provides:

- a wide three-zone workbench and compact drawer/stacked rendering;
- `Marketplace`, `Installed`, and existing-only `My developments` catalogs;
- selected Application identity and bounded detail instead of raw payloads;
- install/update/update-settings/uninstall commands with separate reviewed
  plan and apply boundaries;
- prerelease-following and automatic-update toggles;
- `Details`, `Versions`, `Operations`, and `Reports` tabs;
- `Installation`, `Marketplace`, `Categories`, and conditional
  `My development` metadata;
- current Builder phase, status, revision, and exact existing-source link.

The application-manager capability catalog version is `1.3.12`. All nine
postconditions pass. The final r011 correction completed on the primary
`gpt-5` attempt without repair: response
`resp_00473e1d9452a416006a9e64e68c7087d18fc355fe54453e59`, `12,062` total
tokens. Earlier broad revisions showed a recurring cost/risk: r009 and r010
needed qualification repair and consumed about `29k` tokens each. Narrow,
contract-explicit corrections are materially cheaper and more reliable.

## Verification Ledger

| Gate | Result |
| --- | --- |
| Application/Builder/UI Python regression group | passed |
| Complete browser-client Karma suite | passed, `1270/1270` |
| Capability qualification | passed, `9/9`, catalog `1.3.12` |
| Real local development projection | `prototype`, `working`, revision `011` |
| Wide browser layout | passed; 380px catalog, flexible detail, 300px metadata |
| Compact browser layout | passed; no document horizontal overflow |
| Existing-development navigation | passed; new window targets `desktop/builder` with Applications selected |
| Installed fixture | passed; installed `1.2.0`, stable `1.3.0`, prerelease `1.4.0-beta.2` |
| Installed controls | update, settings, uninstall, prerelease, and auto-update visible; apply hidden before receipt |
| Browser runtime errors | none; expected local config/probe/fallback transport noise only |

Relevant implementation commits are client `508972a` and AdaOS `225bd5231`,
`aa6c02eba`, and `564cf9f12`. No remote push was performed for this checkpoint.

## Critical Backlog

### Must

- obtain explicit human acceptance of one exact Prototype revision before
  Automation starts;
- replace the imperative creation prompt stored as `display.summary` with a
  concise publisher-reviewed product summary through a governed Builder
  metadata operation;
- run real browser plan, receipt review, and apply operations for install,
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
  patch contracts and deterministic postcondition hints.

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

The next valid action is human review of revision `011` or another Builder chat
correction. Prototype acceptance, Codex/Automation handoff, Trial creation, and
publication are intentionally blocked until the Must items that belong before
handoff are satisfied. Infrastate Inventory is intentionally unchanged.
