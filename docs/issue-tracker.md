# Issue Tracker

Snapshot: 2026-08-06.

This document contains only active AdaOS execution work. Architecture,
sequencing, and milestone completion are owned by the documents listed in the
[Roadmap Inventory](architecture/roadmap-inventory.md). Completed task blocks
and dated investigation journals are removed from this file; recover them from
Git history or linked release evidence when needed.

Priority uses the shared planning vocabulary:

- `[must]`: blocks the current MVP or governed-evolution proof gate;
- `[should]`: required before broad or repeated use;
- `[could]`: useful but non-blocking;
- `[deferred]`: intentionally postponed until the stated condition.

Execution state is separate from priority: `open`, `in progress`, `queued`,
`ready for stand`, or `conditional`. A task leaves this tracker when its owner
accepts the required evidence; closed tasks do not remain as checked rows.

## Current Focus

The local Wave 0-1 gate is closed. The active sequence is:

1. prove the M1 projection path on the target stand;
2. build the M2 operator truth and durable operation plane;
3. land the shared M3 activation runtime and migrate the remaining heavy
   operator skills in an evidence-driven order selected from current demand,
   pressure, and stand failures;
4. finish browser/webspace acceptance and the release-candidate soak.

## Must

| ID | State | Work and next proof | Owner |
| --- | --- | --- | --- |
| `M2-TRUTH` / `STATUS-007` | in progress | Populate compact runtime/update/slot/route/Yjs/member/guard cards, keep control traffic inside its own budget, and replace repeated full-summary polling with versioned push/delta consumption. | [MVP M2](mvp_roadmap.md#milestone-m2-operator-truth-plane), [Operational Event Model](architecture/operational-event-model-roadmap.md) |
| `M2-OPERATIONS` | ready for stand | Durable history, restart-to-`recoverable`, governed subprocess cancellation, and idempotent retry entry points are implemented. Prove recovery, duplicate-retry suppression, subprocess termination, and notifications through an API restart on a canary stand. | [MVP M2](mvp_roadmap.md#milestone-m2-operator-truth-plane), [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `M3-ACTIVATION` / `LRLT-005` | open | Add one activation runtime for loaded/active state, startup allowance, background refresh, and client presence; prove inactive lazy/on-demand skills remain cheap. | [MVP M3](mvp_roadmap.md#milestone-m3-activation-service-and-skill-migration-wave) |
| `M4-BROWSER` / `BSPH-001` / `MRI-001` | in progress | Keep source kinds explicit, render structure before deferred hydration, make reload/resync recovery declarative, and pass first-paint plus managed-restart browser E2E. | [MVP M4](mvp_roadmap.md#milestone-m4-webspace-and-browser-runtime-stabilization), [Webspace Evolution](architecture/webspace-evolution-roadmap.md) |
| `RCMS-006` | open | Persist hub/root catalog snapshots on members and prove no-git member drift, reconnect refresh, and archive materialization. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md), [MVP M6](mvp_roadmap.md#milestone-m6-endpoint-and-device-reachability-matrix) |
| `RCMS-007` | open | Make inventory, lifecycle, scenario health, operation detail, and log access core-owned contracts shared by UI, API, and MCP. | [MVP M2](mvp_roadmap.md#milestone-m2-operator-truth-plane), [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `M6-IDENTITY` / `DIAU-001` / `DIAU-003` / `NER-002` | in progress | Prove canonical hub/browser/member identity, deterministic display names, and immediate or explicitly governed detach/logout behavior across local and routed topologies. | [MVP M6](mvp_roadmap.md#milestone-m6-endpoint-and-device-reachability-matrix), [Device Access Roadmap](architecture/device-access-roadmap.md) |
| `BUILDER-FORGE` | open | Make Root scenario-draft publication return durable Forge commit acknowledgement instead of `504` plus stale metadata. | [Builder Roadmap](architecture/builder-roadmap.md#phase-4-validation-and-preview), [Builder SDK Boundary](architecture/builder-sdk-boundary.md) |
| `BUILDER-DEV-VERSION-CACHE` | open | Reject different DEV skill bytes under an existing semantic version, or invalidate and refetch every local A/B cache by content digest. Until fixed, Builder acceptance must advance a unique version before activation. | [Builder Roadmap](architecture/builder-roadmap.md#phase-6-runtime-activation-and-rollback), [Skill Runtime Lifecycle](skill_runtime.md) |
| `M7-NEURAL-INSTALL` / `SFH-006` | open | On a clean workspace, install the Neural NLU provider without tracked runtime models and pass provider diagnostics plus dependency-profile checks. | [MVP M7](mvp_roadmap.md#milestone-m7-model-and-nlu-provider-baseline), [NLU Roadmap](concepts/nlu-roadmap.md) |
| `MVP-STAND-001` | in progress | The executable observe/browser evidence runner is implemented. Provision permanent browser access and secrets, prove one direct target-stand run, then wrap the same deterministic runner in the Root-owned validation-campaign and leased test-node flow. | [MVP M9](mvp_roadmap.md#milestone-m9-mvp-release-candidate-acceptance), [Post-Deploy E2E](architecture/post-deploy-e2e-testing.md) |
| `MVP-SOAK-001` / `HMG-005` / `STATUS-008` | queued | Run the bounded two-browser/root-routed soak with process-tree memory, pressure, operation, reconnect, quarantine, and residual-risk evidence. | [MVP M9](mvp_roadmap.md#milestone-m9-mvp-release-candidate-acceptance), [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |

## Should

| ID | State | Work and next proof | Owner |
| --- | --- | --- | --- |
| `RT-FANOUT` / `LRLT-001` / `LRLT-002` / `HMG-001` / `HMG-002` / `HMG-006` | in progress | Single-flight cached supervisor projection, latest-state bounded core-update fanout, and Teacher startup I/O are implemented with regression coverage. Keep event-loop-affine handlers on their owner loop, offload only their blocking sub-operations, and run target-stand pressure evidence before closure. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md), [Rebuild Lag Hardening](architecture/realtime-rebuild-lag-hardening.md) |
| `LRLT-004` | open | Reconfirm y-py thread affinity and safe diagnostics after the patched native-store/runtime changes. | [Runtime Guarding](architecture/runtime-guarding.md) |
| `LRLT-006` | open | Move long-running NLU/Teacher inference and apply work behind bounded workers while retaining cheap resolver/regex fast paths. | [NLU Roadmap](concepts/nlu-roadmap.md) |
| `UILOG-001` / `LRLT-007` | in progress | Add typed UI diagnostic ABI, duplicate suppression, rate/size limits, and post-restart skill-log retrieval acceptance. | [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md) |
| `DIAU-002` | open | Add modal/browser regression coverage for settings controls and destructive detach confirmation. | [Device Access Roadmap](architecture/device-access-roadmap.md) |
| `DIAU-004` | open | Finish the product-copy audit for device, browser, member, node, hub, endpoint, and subnet terms. | [Product Terminology](architecture/product-terminology.md) |
| `MRI-002` | open | Make workspace skill publication verifiable from source edit through targeted tests, push/version evidence, and compatible root/client checks. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `RCMS-001` | open | Make git requirements explicit by role and deployment mode, including member no-git archive materialization and operator diagnostics. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `RCMS-002` | in progress | Finish stale-catalog classification, explicit workspace fallback, row-level diagnostics, and scenario source/runtime actions. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `RCMS-005` | open | Keep production CLI/control actions slot-bound and development commands explicitly rooted in `.adaos/dev`. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `HMG-007` | open | Preserve correlation/generation IDs and reject guard behavior that hides overload rather than reducing it. | [Runtime Guarding](architecture/runtime-guarding.md) |
| `ROOT-MCP-TARGET` / `F3M-006A` / `F3M-006C` | open | Keep selectors out of managed target IDs and separate direct remote MCP health from bearer validity with deployed-session evidence. | [Root MCP Roadmap](architecture/root-mcp-roadmap.md) |
| `ROOT-PUBLIC-SPLIT` / `F3M-006D` | open | Separate public bearer/bootstrap/Codex surfaces from mTLS API surfaces and add deploy smoke for both hosts. | [Security](architecture/security.md), [Root MCP Roadmap](architecture/root-mcp-roadmap.md) |
| `RT-DIAGNOSTICS` / `F3M-002` / `F3M-010` | conditional | Add compact route-timeout summaries and prevent raw diagnostic probes from superseding live runtime connections when the symptom is reproducible. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `UI-RT-001` | in progress | Group repeated UI contract issues for diagnosis and prove the standard skill-log path can read the node-side UI runtime tail on stand. | [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md) |
| `NER-001` | in progress | Complete the canonical read model across device, node, workspace, scenario, skill, and manifest sources. | [Named Entities](architecture/named-entities.md), [NLU Roadmap](concepts/nlu-roadmap.md) |
| `NER-005` | in progress | Finish authoritative lifecycle/conflict events and use `entity.registry.changed` as the shared invalidation signal. | [Operational Event Model](architecture/operational-event-model-roadmap.md) |
| `NER-006` | open | Migrate remaining client/operator consumers to canonical refs and delete duplicate display-name fallbacks after coverage exists. | [Device Access Roadmap](architecture/device-access-roadmap.md) |
| `BUILDER-LEGACY` | open | Move the remaining Prompt IDE DEV file lifecycle behind `adaos.sdk.developer.projects` before legacy retirement. | [Builder SDK Boundary](architecture/builder-sdk-boundary.md) |
| `BUILDER-RELOAD` | in progress | Preserve widget and coarse branch identity on no-op semantic reloads and pass reconnect/reload soak coverage. | [Builder Roadmap](architecture/builder-roadmap.md#phase-8-product-experience), [Webspace Evolution](architecture/webspace-evolution-roadmap.md) |
| `BUILDER-CHANNEL-ACCEPTANCE` | ready for human stand | Complete one human wide/compact Web pass and one physical Telegram-button click with backend receipt. Addressed “что выбрано?”, canonical project topic, five bounded controls, token admission, cross-process DEV dispatch, and originating-conversation reply are locally proven; mutating controls without a registered executor are intentionally withheld and hard channel parity remains out of scope. | [Builder Roadmap](architecture/builder-roadmap.md#phase-11-conversational-development-control-plane), [Builder Conversational Development](architecture/builder-conversational-development.md#channel-capability-boundary) |
| `AP7-14` | in progress | Merge infra `5f9a5b0`, then prove one clean production deploy with candidate warming outside `inimatic_proxy`, admission only after health, strict continuous public samples, and no automatic repeat of the already committed rollout. | [Artifact Pipeline Roadmap](architecture/artifact-source-package-activation-roadmap.md#milestone-ap7-end-to-end-proof-and-legacy-retirement-decision) |

## Could

| ID | State | Work | Owner |
| --- | --- | --- | --- |
| `LRLT-003` | open | Add media-indexer timing, bounded concurrency/payloads, and a focused stall reproducer if media load becomes an active bottleneck. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `MRI-004` | open | Document weather provider selection and API-key behavior so provider failures are distinguishable from modal/rendering failures. | [Post-Deploy E2E](architecture/post-deploy-e2e-testing.md) |
| `BUILDER-EVIDENCE-ACTIONS` | open | Add governed open/copy actions for Automation evidence files. | [Builder Roadmap](architecture/builder-roadmap.md#phase-8-product-experience) |
| `UILOG-LLM-GROUPING` | open | Add richer LLM-oriented grouping beyond the bounded diagnostic aggregation required by `UILOG-001`. | [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md) |

## Deferred

| ID | Resume condition | Owner |
| --- | --- | --- |
| `RCMS-003` | Resume when routine development no longer depends on source-copy `runtime_update` for fast iteration and the slot/package update path is fast enough to enforce a production-only atomic boundary without slowing current work. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `DIRECT-YJS-DENY-BY-DEFAULT` | Resume after M1 warnings and M3 migrations provide a measured exception inventory. | [Semantic State Plane](architecture/semantic-state-plane.md) |
| `CROSS-SKILL-PROJECTION-CLEANUP` | Resume after the three M3 pilot skills pass stand acceptance. | [Projection Subscription Roadmap](architecture/projection-subscription-roadmap.md) |
| `SIDECAR-YJS-AUTHORITY` | Resume after current transport handoff and MVP SyncChannel recovery are accepted. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `REMOTE-MCP-HUB-BRIDGE` | Resume only when public remote Root MCP access to local hubs becomes a product requirement. | [Root MCP Roadmap](architecture/root-mcp-roadmap.md) |
| `BUILDER-AUTONOMOUS-REPRODUCTION` | Resume only after the non-Builder pipeline has also passed human Web/Telegram acceptance and a separate characterization plan prevents self-hosting regressions. | [Builder Roadmap](architecture/builder-roadmap.md) |
| `BUILDER-MODULE-DECOMPOSITION` | Resume under a separate characterization-test plan after MVP behavior is stable. | [Builder Roadmap](architecture/builder-roadmap.md) |
| `UILOG-LLM-DEBUG-WORKFLOW` | Resume when the governed Builder repair loop consumes skill-scoped diagnostics. | [Governed Evolution Roadmap](architecture/governed-evolution-roadmap.md) |
| `RT-SYMPTOM-REOPEN` / `LRLT-008` | Reopen only if a new run reproduces NATS/route/Yjs/event-loop symptoms with a run id and correlated evidence. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |

## Evidence Rules

- Link local, stand, canary, and production evidence without promoting one
  maturity level to another.
- Store verbose logs, metrics, browser captures, and dated incident narratives
  in release-evidence artifacts, not in this file.
- Keep only the next falsifiable proof in each active row. Durable contracts
  and full acceptance criteria belong to the owning roadmap.
- Remove a row after closure; the owner roadmap, evidence artifact, commit, and
  Git history retain the completion record.
