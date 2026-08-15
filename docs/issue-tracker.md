# Issue Tracker

Snapshot: 2026-08-13.

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
| `M2-TRUTH` / `STATUS-007` | in progress | The runtime/details split and event-boundary runtime probes removed periodic reliability polling from the browser hot path. Populate the remaining compact runtime/update/slot/route/Yjs/member/guard cards, keep control traffic inside its own budget, and promote the boundary probe to versioned push/delta consumption. | [MVP M2](mvp_roadmap.md#milestone-m2-operator-truth-plane), [Operational Event Model](architecture/operational-event-model-roadmap.md) |
| `M2-OPERATIONS` | ready for stand | Durable history, restart-to-`recoverable`, governed subprocess cancellation, and idempotent retry entry points are implemented. Prove recovery, duplicate-retry suppression, subprocess termination, and notifications through an API restart on a canary stand. | [MVP M2](mvp_roadmap.md#milestone-m2-operator-truth-plane), [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `M3-ACTIVATION` / `LRLT-005` | open | Add one activation runtime for loaded/active state, startup allowance, background refresh, and client presence; prove inactive lazy/on-demand skills remain cheap. | [MVP M3](mvp_roadmap.md#milestone-m3-activation-service-and-skill-migration-wave) |
| `M4-BROWSER` / `BSPH-001` / `MRI-001` | in progress | Keep source kinds explicit, render structure before deferred hydration, and make reload/resync recovery declarative. The `.30` `0.1.684` managed restart preserved an active control WS but produced no YWS reattach attempt within three minutes; prove provider reattach and first sync rather than treating server handoff readiness as browser recovery. | [MVP M4](mvp_roadmap.md#milestone-m4-webspace-and-browser-runtime-stabilization), [Webspace Evolution](architecture/webspace-evolution-roadmap.md) |
| `BROWSER-LIFECYCLE-001` | ready for stand | Deploy the bounded lifecycle snapshot recovery and reproduce a silent or dropped Root lifecycle SSE event while snapshot GET stays available. Prove `Recovering` lasts no longer than the expired lease plus one bounded GET, retries back off to at most 15 seconds, a later SSE event cancels polling, and browser diagnostics preserve reason, attempt, delay, and recovery duration. Correlate the browser evidence with node and Root logs before closure. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md), [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md) |
| `RCMS-006` | open | Persist hub/root catalog snapshots on members and prove no-git member drift, reconnect refresh, and archive materialization. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md), [MVP M6](mvp_roadmap.md#milestone-m6-endpoint-and-device-reachability-matrix) |
| `RCMS-007` | open | Make inventory, lifecycle, scenario health, operation detail, and log access core-owned contracts shared by UI, API, and MCP. | [MVP M2](mvp_roadmap.md#milestone-m2-operator-truth-plane), [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `M6-IDENTITY` / `DIAU-001` / `DIAU-003` / `NER-002` | in progress | Prove canonical hub/browser/member identity, deterministic display names, and immediate or explicitly governed detach/logout behavior across local and routed topologies. | [MVP M6](mvp_roadmap.md#milestone-m6-endpoint-and-device-reachability-matrix), [Device Access Roadmap](architecture/device-access-roadmap.md) |
| `BUILDER-FORGE` | open | Make Root scenario-draft publication return durable Forge commit acknowledgement instead of `504` plus stale metadata. | [Builder Roadmap](architecture/builder-roadmap.md#phase-4-validation-and-preview), [Builder SDK Boundary](architecture/builder-sdk-boundary.md) |
| `BUILDER-DEV-VERSION-CACHE` | open | Reject different DEV skill bytes under an existing semantic version, or invalidate and refetch every local A/B cache by content digest. Until fixed, Builder acceptance must advance a unique version before activation. | [Builder Roadmap](architecture/builder-roadmap.md#phase-6-runtime-activation-and-rollback), [Skill Runtime Lifecycle](skill_runtime.md) |
| `M7-NEURAL-INSTALL` / `SFH-006` | open | On a clean workspace, install the Neural NLU provider without tracked runtime models and pass provider diagnostics plus dependency-profile checks. | [MVP M7](mvp_roadmap.md#milestone-m7-model-and-nlu-provider-baseline), [NLU Roadmap](architecture/nlu-roadmap.md) |
| `MVP-STAND-001` | in progress | The executable observe/browser evidence runner is implemented. Provision permanent browser access and secrets, prove one direct target-stand run, then wrap the same deterministic runner in the Root-owned validation-campaign and leased test-node flow. | [MVP M9](mvp_roadmap.md#milestone-m9-mvp-release-candidate-acceptance), [Post-Deploy E2E](architecture/post-deploy-e2e-testing.md) |
| `MVP-SOAK-001` / `HMG-005` / `STATUS-008` | queued | Run the bounded two-browser/root-routed soak with process-tree memory, pressure, operation, reconnect, quarantine, and residual-risk evidence. | [MVP M9](mvp_roadmap.md#milestone-m9-mvp-release-candidate-acceptance), [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |

## Should

| ID | State | Work and next proof | Owner |
| --- | --- | --- | --- |
| `RT-FANOUT` / `SKILL-CHANNEL-001` / `LRLT-001` / `LRLT-002` / `HMG-001` / `HMG-002` / `HMG-006` | in progress | The `.30` watchdog attributed a real stall to `infrastate_skill -> skill_memory_get -> pathlib.read_text`; the 2026-08-15 cutover run added a second exact sample: `infrastate_skill 0.75.65 -> on_runtime_event(subnet.member.link.up)`, 5.443 s, with the watchdog frame in `importlib.get_data`. Core `a96c738d` now serializes post-boot migration behind handler import, keeps candidate migration deferred until promotion, limits each async skill handler to one active invocation, and opens an observable cooldown circuit after a severe over-budget call. Synchronous subscriptions remain bounded workers, and the registry audit reports zero known blocking paths. Redeploy the published skill releases, including `infrastate_skill 0.75.66`, prove the post-startup watchdog remains at zero stalls and no skill circuit opens, then prove a synthetic rejected skill and controlled Root outage cannot delay HTTP, `/ws`, `/yws`, or hub-root heartbeat recovery. Do not close from static proof alone. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md), [Runtime Guarding](architecture/runtime-guarding.md), [Rebuild Lag Hardening](architecture/realtime-rebuild-lag-hardening.md) |
| `LRLT-004` | open | Reconfirm y-py thread affinity and safe diagnostics after the patched native-store/runtime changes. | [Runtime Guarding](architecture/runtime-guarding.md) |
| `LRLT-006` | open | Move long-running NLU/Teacher inference and apply work behind bounded workers while retaining cheap resolver/regex fast paths. | [NLU Roadmap](architecture/nlu-roadmap.md) |
| `UILOG-001` / `LRLT-007` | in progress | Add typed UI diagnostic ABI, duplicate suppression, rate/size limits, and post-restart skill-log retrieval acceptance. | [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md) |
| `YJS-MATERIALIZATION-REPAIR` | ready for stand | The browser now separates transport sync from live-YDoc completeness and requests a bounded authoritative full-state repair without closing its provider. The Hub preserves the room, validates scenario authority, coalesces concurrent browser requests, reports update bytes and delivery counts, and leaves HTTP snapshots render-only. On `.30`, prove an intentionally incomplete client converges while the same YWS/WebRTC provider and page instance remain open, then hold a multi-browser soak without quadratic repair fanout. | [Hub-Browser Connectivity](architecture/hub-browser-connectivity.md) |
| `DIAU-002` | open | Add modal/browser regression coverage for settings controls and destructive detach confirmation. | [Device Access Roadmap](architecture/device-access-roadmap.md) |
| `DIAU-004` | open | Finish the product-copy audit for device, browser, member, node, hub, endpoint, and subnet terms. | [Product Terminology](architecture/product-terminology.md) |
| `MRI-002` | open | Make workspace skill publication verifiable from source edit through targeted tests, push/version evidence, and compatible root/client checks. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `RCMS-001` | open | Make git requirements explicit by role and deployment mode, including member no-git archive materialization and operator diagnostics. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `RCMS-002` | in progress | Finish stale-catalog classification, explicit workspace fallback, row-level diagnostics, and scenario source/runtime actions. | [Registry/Operations](architecture/registry-marketplace-operations-roadmap.md) |
| `RCMS-005` | open | Keep production CLI/control actions slot-bound and development commands explicitly rooted in `.adaos/dev`. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `HMG-007` | open | Preserve correlation/generation IDs and reject guard behavior that hides overload rather than reducing it. | [Runtime Guarding](architecture/runtime-guarding.md) |
| `ROOT-MCP-TARGET` / `F3M-006A` / `F3M-006C` | open | Keep selectors out of managed target IDs and separate direct remote MCP health from bearer validity with deployed-session evidence. | [Root MCP Roadmap](architecture/root-mcp-roadmap.md) |
| `ROOT-PUBLIC-SPLIT` / `F3M-006D` | open | Separate public bearer/bootstrap/Codex surfaces from mTLS API surfaces and add deploy smoke for both hosts. | [Security](architecture/security.md), [Root MCP Roadmap](architecture/root-mcp-roadmap.md) |
| `RT-DIAGNOSTICS` / `F3M-002` / `F3M-010` | ready for stand | The reproduced sidecar/direct-WSS incident now has identity-aware control-port readiness, protocol roundtrip liveness, bounded process/network-I/O lookback, and durable incident persistence. Prove on the target stand that readiness creates no NATS session and that an outbound-only direct WSS session is not recycled by raw-RX idleness. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `RT-POST-INCIDENT-001` / `LRLT-008` | in progress | `.800` on `.30` completed a controlled loss of all three sidecar-to-runtime sockets with 271/271 successful 50 ms pings (max 135.686 ms): WS resumed through a fresh handshake, YWS reconnected without replay, and NATS stayed connected. The deployment also exposed residual system defects that prevent closure: candidate installation caused 75-90% disk I/O wait; the active runtime later stalled for about 8 s; the external watchdog captured synchronous `skill_env` `os.replace` and SQLite durable-state frames; eventbus wall time falsely blamed an otherwise empty `voice_chat_skill` handler; the dedicated runtime-beacon executor had only 2.042 ms queue wait but callback execution reached 6996.744 ms; the healthy `.799` sidecar remained alive after `.800`, so new reconnect code required a manual restart; that restart then forced a redundant second hub-root reconnect after automatic recovery. The next checkpoint rejects sync skill-env I/O on an asyncio loop, separates queue/wall time from watchdog-confirmed blocker attribution, preserves multi-frame stall evidence, bounds beacon execution with explicit stale/unavailable behavior and candidate prewarm, automatically applies sidecar code only after the runtime transition is stable, and skips forced reconnect only when auto-recovery is stably ready through the sidecar owner. Deploy the complete checkpoint once, repeat correlated route-socket, disk/SQLite, executor, and evolved-skill pressure plus at least a six-minute soak, then align browser/node/sidecar/Root logs. Preserve clock offset, counters, skill/watchdog evidence, and CPU/RSS/disk/network lookback; explicitly assess subprocesses, media indexing, and large downloads. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `RT-POST-INCIDENT-REVIEW-002` | open | After the final post-`.800` hardening fault-and-soak evidence is captured, perform a separate later review of the preserved logs and counter deltas. Re-check for hidden reconnect loops, competing route owners, stale readiness/cache use, event-loop or executor starvation, persistence backlogs, skill circuit or SDK I/O-guard activity, downloads/media work, sidecar generation mismatch, and contradictions between browser, node, sidecar, and Root state. Compare the pre-failure process/I/O window with a quiet baseline and verify that beacon stale responses never outlive their bounded window. This review remains open even when the first final campaign passes. | [Realtime Reliability](architecture/realtime-reliability-roadmap.md) |
| `UI-RT-001` | in progress | Group repeated UI contract issues for diagnosis and prove the standard skill-log path can read the node-side UI runtime tail on stand. | [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md) |
| `NER-001` | in progress | Complete the canonical read model across device, node, workspace, scenario, skill, and manifest sources. | [Named Entities](architecture/named-entities.md), [NLU Roadmap](architecture/nlu-roadmap.md) |
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

## Evidence Rules

- Link local, stand, canary, and production evidence without promoting one
  maturity level to another.
- Store verbose logs, metrics, browser captures, and dated incident narratives
  in release-evidence artifacts, not in this file.
- Keep only the next falsifiable proof in each active row. Durable contracts
  and full acceptance criteria belong to the owning roadmap.
- Remove a row after closure; the owner roadmap, evidence artifact, commit, and
  Git history retain the completion record.
