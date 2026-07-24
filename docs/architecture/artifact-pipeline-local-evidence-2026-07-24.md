# Artifact Pipeline Local Evidence — 2026-07-24

Status: `validated-local`; this record is not stand or production acceptance.

This evidence closes the bounded single-machine implementation proof for the
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).
It does not close the live Forge/backend deployment gate.

## Exact Scope

- AdaOS branch: `rev2026`.
- Architecture baseline: commit `a97a8860`, tag
  `architecture-artifact-pipeline-v1`.
- Local proof implementation: commits through `6c0c41e3` and their ancestors.
- Backend source-tree API: backend commits `1b6b1ac` and `fa91ded`; referenced
  by AdaOS commit `8bee7dbe` but not yet pushed or deployed.
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

Exact immutable identities from the proof:

```text
scenario package  sha256:072c6dfbae81032455cf05ed3e936f2ef3c7d04896214f3a3934f468c23b02c5
skill package     sha256:6e7baa840dadfcf6ace31f472caffedf176e6576edfe9d14bbdca2565c0eb361
project release   sha256:08703b09a44eb50410617dccb0297243100db79797cdbfcbaef36ec148cc426d
workspace lock    sha256:c2002d80c1596b0c6700f9c67ce2fc947aea2084f1fdb6471ab09634754d4165
scenario source   510e991ba7469b16cc283fef152105bc9ef07069
skill source      6487651b5fd5dab58d72f24161bcfb39834509d5
```

The local proof uses each immutable commit as its source-verification witness.
The deployed path must instead call the backend Forge tree endpoint and compare
the returned tree object id with the tree persisted at checkpoint time.

## Failed Experiments Retained

Three failures materially changed the implementation and remain part of the
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

The partially completed Forge writes from the first experiment were recovered
using exact change metadata and archive hashes. The resulting design now uses
a durable checkpoint intent and archive, explicit `uncertain` state, receipt
reconciliation, and no automatic repeat of the modifying command.

## Open Acceptance Gates

- GitHub CLI is not authenticated on this machine. Backend commits cannot yet
  be pushed through the required reviewed publication workflow.
- The production backend does not yet expose immutable package/release/channel
  APIs or Forge source-tree verification. A live Builder candidate must not be
  promoted until those routes are deployed.
- Delayed post-activation observation remains a `[should]` operational gate;
  synchronous health verification and rollback are locally validated.
- A clean stand/second-machine run is required before package-only activation
  becomes the default and before legacy sparse Workspace compatibility is
  retired.
- The pre-existing client submodule change was not modified or included.

These gates keep the maturity at `validated-local`; they are not failures of the
bounded local proof.
