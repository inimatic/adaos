# Application Preparation Evidence - 2026-09-05

Status: `validated-local` preparation gate.

This record covers the non-deferred Application Core, SDK, Root MCP,
distribution, recovery, and Development Report groundwork required before
Applications is created through managed Builder development. It proves
readiness to start `APP4-01`; it does not claim completion of the Applications
UI or the clean-subnet release proof in `APP6`.

## Scope Verified

- Application schemas, compatibility records, aggregate service, runtime
  selection, deployment planning, reference accounting, snapshots, and restore;
- public `adaos.sdk.applications` and bounded
  `adaos.sdk.builder.applications` facades;
- 44 `ApplicationsPlane` contracts, including development recovery,
  Trial/prerelease/stable transitions, operations, access, and reports;
- Root filesystem CAS, trusted metadata, provenance, retention, backup/restore,
  compaction, rollout control, and public stable-source projection;
- encrypted Development Report intake, publisher triage, status synchronization,
  optional isolated classification, appeal, and cross-zone store-and-forward;
- isolated runtime layout: component-scoped DEV preview slots,
  `.adaos/trials/<candidate-id>`, and stable `.adaos/workspace/.runtime`.

## Verification Ledger

| Gate | Result |
| --- | --- |
| AdaOS complete pytest suite | passed, exit code `0` |
| Root MCP complete test group | passed, `60/60` |
| Changed Python files | `ruff check` passed |
| Browser client Karma suite | passed, `1261/1261` |
| Browser production build | passed, hash `cbb6c2de0c0109b9`; existing `qrcode` CommonJS warning only |
| Backend suite | passed, `46/46` |
| Documentation | `mkdocs build --strict` passed |
| Controlled AdaOS restart | passed; liveness and readiness remained healthy after bootstrap |
| Live Root MCP | Applications plane present; `44` contracts; required lifecycle tools present |
| Live read contract | empty `applications` and development `operations` remain explicit arrays |
| Live mutation guard | create dry-run returned `would_create_application=true` and persisted no Application |
| Live browser | `http://127.0.0.1:8100` returned `200` with the Angular application root |
| Runtime layout | `.adaos/trials` present; Workspace Trial paths absent; component DEV runtime present |

The local source revisions are AdaOS `141e221d3`, client `cedb9e2`, and backend
`c3db070`. The complete AdaOS preparation series starts at `9257be465` on
branch `rev2026`.

## Open Boundary

- `APP1-04`, `APP4`, and `APP6` remain open. Infrastate Inventory is retained
  until the Builder-created Applications product replaces that workflow.
- No fresh Application has yet been created through Builder chat, accepted as
  a Trial, published, installed on a clean subnet, repaired from a Development
  Report, and promoted by exact digest. That evidence belongs to `APP4` and
  `APP6`.
- Root Guard, ownership transfer, collaborative publishers, general backward
  migration, side-by-side shared component versions, and multi-Root failover
  remain explicitly deferred.
- Repository-wide `ruff check src tests` still reports `490` pre-existing
  findings outside this change set. Changed-file lint is clean; the global debt
  is not reclassified as Application readiness evidence.

The next allowed product step is `APP4-01`: ask Builder through the chat
interface to create Applications, then keep every correction on the governed
Builder path and turn missing platform contracts into Core Dev Tickets.
