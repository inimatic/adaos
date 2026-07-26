# Artifact Pipeline Local Evidence — 2026-07-24

Status: `validated-local`, with the production backend route deployed and
verified; this record is not stand or marketplace acceptance.

This evidence closes the bounded single-machine implementation proof for the
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).
It also records the subsequent live Builder publication proof and closes the
bounded Forge/backend deployment gate. A clean stand remains a separate gate.

## Exact Scope

- AdaOS branch: `rev2026`.
- Architecture baseline: commit `a97a8860`, tag
  `architecture-artifact-pipeline-v1`.
- Local proof implementation: commits through `7a0596b6` and their ancestors.
- Backend package/source-tree API: merged by
  [inimatic/adaos-backend#1](https://github.com/inimatic/adaos-backend/pull/1)
  at `1329ecb3371b25869ad78acf51814704d2862b04` and deployed as backend
  `0.1.137`.
- Representative scenario: `streaming_recipe_book_eval`.
- Representative companion skill: `streaming_recipe_book_eval_skill`.
- Builder change/checkpoint:
  `builder-checkpoint-369c8a1cd8454112aee41592c221822b`.
- Prototype revision: `015`, produced by the built-in interactive LLM.
- Automation task: isolated Codex task
  `task.01KYAF6Q0F4ZRNDPN5E8118ECC`, source commit
  `fde3e2854d2f...`.

The representative implementation was not hand-programmed as proof setup.
The built-in LLM produced the UI revision and the isolated Codex process
implemented and tested the automation. The pipeline work in AdaOS then
checkpointed, packaged, trialed, accepted, promoted, and activated that result.

The live Builder regression used Builder itself as a second representative
project. Prototype `041` was handed to two explicit Automation iterations. The
built-in Codex tasks were `task.01KYANX1C0B6ZECBMWYA2PT15N` and
`task.01KYAQAD3F5QAQFTWKQ4EF7DEG`; the second result was reconciled at the
checkpoint boundary without executing Codex again. Candidate
`builder-0-2-20-de119269b9d4` was then trialed, accepted, promoted, and
materialized into Workspace.

## Reproduction

Run from the AdaOS repository root:

```powershell
.\.venv\Scripts\python.exe tools\verify_artifact_pipeline.py `
  --dev-root .adaos\dev\sn_6acf0c01 `
  --pipeline-state .adaos\state\artifact_pipeline `
  --scenario streaming_recipe_book_eval `
  --skill streaming_recipe_book_eval_skill `
  --change-id builder-checkpoint-369c8a1cd8454112aee41592c221822b
```

The latest durable local record for this run is:

```text
.adaos/state/artifact_pipeline/proofs/20260724T175229863192Z/evidence.json
```

After the live Builder publication, the same verifier was run against Builder's
exact checkpoint. Its isolated durable record is:

```text
.adaos/state/artifact_pipeline/proofs/20260724T191213720879Z/evidence.json
```

That run passed 30 project tests and 21 bounded resilience tests and produced a
package-only WorkspaceLock for Builder `0.2.20` plus
`builder_sdk_control_skill` `0.1.28`. Proof candidates, channels, and Workspace
state live entirely below the run directory; the command reads only immutable
checkpoint records from the working pipeline state.

The record is intentionally machine-local because it contains resolved local
paths. The command is committed and reproducible; this page retains the
redacted identities and conclusions required for review.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Representative scenario and skill contracts | passed | 19 tests |
| Exact checkpoint belongs to one bounded change | passed | both pushed-source records contain the checkpoint id |
| Immutable component packaging | passed | two verified package digests |
| Companion skill dependency lock | passed | scenario and skill present in one ProjectRelease |
| Isolated candidate trial | passed | accepted trial with its own WorkspaceLock |
| Stable promotion and package-only activation | passed | both artifacts materialized; registry has one scenario and one skill |
| Moved-base migration | passed | stale record, exact rebase plan, reapplication, renewed trial and promotion |
| Dependency rejection | passed | missing, ambiguous, incompatible, and cyclic cases |
| Exact Builder task base | passed | content-addressed source snapshot; concurrent DEV edit blocks result activation without data loss |
| Activation interruption | passed | all 13 phases leave no partial first install |
| Permission admission | passed | introduced permissions fail closed and require a durable explicit approval |
| Reversible migration | passed | one execution; data, files, lock, and runtime reload roll back after health failure |
| Unknown migration outcome | passed | no replay; explicit one-shot reconciliation required before recovery |
| Unknown Forge outcome | passed | simulated commit plus timeout creates one remote write and reconciles it |
| Subscription update and rollback | passed | failed health check preserves old lock/subscription; explicit new attempt succeeds |
| Focused pipeline regression | passed | 161 tests |
| Bounded resilience proof | passed | 21 tests, including the 13 parametrized activation phases |
| Backend TypeScript build and package smoke | passed | `npm run build:api`; `npm run test:artifact-packages` |
| Production Forge source-tree lookup | passed | backend `0.1.137`; live mTLS lookup returned the exact scenario and skill trees |
| Live Builder DEV → trial → Workspace publication | passed | Builder scenario `0.2.20`, companion skill `0.1.28`, accepted candidate `builder-0-2-20-de119269b9d4` |
| DEV/trial/Workspace content equality | passed | SHA-256 equality for canonical manifests, `webui.json`, and skill handler |
| Builder checkpoint identity per iteration | passed | 113 focused regressions; explicit checkpoint-only reconciliation, no Codex replay |
| Builder/schema/migration/evidence lock admission | passed | 95 artifact-pipeline regressions plus backend TypeScript build/package smoke; partial and content-mismatched lock sets rejected |
| Operator activation diff and review binding | passed | read-only component/dependency/permission/schema/migration/rollback plan; canonical plan digest and WorkspaceLock CAS guard activation |
| Update entrypoint cutover | passed | 131 focused regressions; subscribed scenario/skill REST and WebSocket paths share one coordinator, require a reviewed package plan and transactional runtime evidence, reject deferred projection, and expose activation/retry identity; DEV update and LLM pull are retired; non-subscribed fallback is labelled legacy |

Exact immutable identities from the proof:

```text
scenario package  sha256:072c6dfbae81032455cf05ed3e936f2ef3c7d04896214f3a3934f468c23b02c5
skill package     sha256:6e7baa840dadfcf6ace31f472caffedf176e6576edfe9d14bbdca2565c0eb361
project release   sha256:08703b09a44eb50410617dccb0297243100db79797cdbfcbaef36ec148cc426d
workspace lock    sha256:c2002d80c1596b0c6700f9c67ce2fc947aea2084f1fdb6471ab09634754d4165
scenario source   510e991ba7469b16cc283fef152105bc9ef07069
skill source      6487651b5fd5dab58d72f24161bcfb39834509d5
```

Live Builder publication identities:

```text
change            builder_change_automation_8b7d45d3adc69058
scenario version  0.2.20
scenario package  sha256:f729d36c419fb46197c51e93ad3569a4a32be5ee61944b2d0993de119269b9d4
scenario source   8720b8f5b74dbac73f0b7b0558149b01200433e8
scenario tree     2336ab08f3b9dd5de5690f071d5c7c8e80963254
skill version     0.1.28
skill package     sha256:edc9bb16359aa0a0bccefdac1f64399f191abd0fd0ab202fe604384da5606a48
skill source      57542a1d84779e8a71edb6f049856b96706bd51a
skill tree        d6cc947733074119fb3acac42470279d368cd9e9
release digest    sha256:feee37b221a12c6d6ba4e12c1cdd00fdd8320df4b5d4ca9a9ee13747f01a450b
```

The local proof uses each immutable commit as its source-verification witness.
The deployed Forge tree endpoint was then called over the production mTLS path;
it returned scenario tree `26a3f784740c61178b4ab26ee8260781f2d36b77` and skill tree
`da3614dc5ec067c505fec4efce57220832ce8743`, matching their persisted
checkpoint records.

## Failed Experiments Retained

Five failures materially changed the implementation and remain part of the
evidence:

1. The first checkpoint sequence rejected a valid zero-byte file because the
   verifier interpreted size `0` as missing. The verifier now distinguishes
   zero from absence and every built package self-verifies before a remote
   write.
2. The first package boundary included authoring histories and prompt state, so
   routine workflow transitions changed the package after checkpoint. Release
   packages now exclude LLM jobs, prompt state, UI revision history, tests,
   preparation files, caches, and other authoring-only content. Canonical YAML,
   runtime code/assets, `webui.json`, and the derived `scenario.json` remain.
3. Builder realization originally retained only `base_branch` and copied the
   mutable DEV tree when the worker happened to start. Builder now captures a
   content-addressed task input before queueing Codex. Result activation uses a
   compare-and-switch guard plus transactional backup/rollback; a concurrent
   DEV edit preserves both the user's tree and the isolated Codex result.
4. The first live Builder candidate carried version `0.2.18` while the installed
   Workspace was already `0.2.19`. It was rejected. Candidate preparation now
   fails closed unless the canonical DEV `scenario.yaml` version advances the
   remote stable channel, or the installed Workspace version when no channel
   exists. Codex cannot modify checkpoint-owned `version` or `updated_at`.
5. A follow-up Automation iteration inherited the preceding `change_id`, so
   Forge correctly rejected different content under the old identity. Builder
   now allocates one identity per iteration. The validated result was recovered
   by an explicit checkpoint-only reconciliation; no state-changing command and
   no Codex task was automatically repeated. A partially committed pair is not
   eligible for this recovery path.

The partially completed Forge writes from the first experiment were recovered
using exact change metadata and archive hashes. The resulting design now uses
a durable checkpoint intent and archive, explicit `uncertain` state, receipt
reconciliation, and no automatic repeat of the modifying command.

## Open Acceptance Gates

- Delayed post-activation observation remains a `[should]` operational gate;
  synchronous health verification and rollback are locally validated.
- A clean stand/second-machine run is required before package-only activation
  becomes the default and before legacy sparse Workspace compatibility is
  retired.
- The production backend routes are deployed, but external package storage is
  still a transport boundary rather than the default Workspace activation
  source; the stand proof must exercise that path end to end.
- The pre-existing client submodule change was not modified or included.
- The backend builder/lock admission hardening is validated on branch
  `codex/artifact-contract-hardening`; merge, deployment, and clean-stand proof
  remain separate gates.

These gates keep the maturity at `validated-local`; they are not failures of the
bounded local proof.
