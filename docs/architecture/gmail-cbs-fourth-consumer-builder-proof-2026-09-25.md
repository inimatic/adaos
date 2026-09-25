# Gmail CBS Fourth-Consumer Matched Builder Proof

Status: validated local Prototype proof, 2026-09-25.

This checkpoint follows the third-consumer proof with a clean matched
`mail_followup_queue_matched_v2` consumer. Its purpose is to test the optimized
Builder orchestration and CBS authoring rail, not Gmail feature breadth or a
new provider implementation.

## Result

The consumer reached semantic revision `003` through one initial Builder turn
and one browser-review chat correction. No semantic/runtime artifact was edited
by hand. Both failed compiler passes were recovered from their retained exact
model checkpoints after Core fixes; each replay consumed zero incremental
tokens.

The accepted revision has:

- exactly two product views: `Message_Collection` and the supporting inline
  `Message_InlineEditor`;
- exactly three commands: read/unread, star/unstar and archive;
- nine explicit state proofs for loading, empty, authorization-required,
  reusable-account-available, connected, offline, permission-denied,
  provider-error and success;
- a human-explicit `capability:mail.messages.manage` requirement with range
  `^1.0.0`;
- four synthetic source records, of which three are visible in the permanent
  queued scope;
- no provider call, credential, account, package, skill or endpoint identity in
  the semantic Application.

The final semantic digest is
`sha256:45ca2faaae857d9e80cb55641e08e4ab0fd9dff8ccc6559a25aa04746fb31803`.
The compiled WebUI digest is
`sha256:ec9c560ef3cf9a999893a4a81a942f4eb6934c8eb8326c00bee5ce7960242d26`.

## Browser Evidence

The real client and development runtime on `127.0.0.1:8777` passed the matched
wide and compact journey. The retained report is
`e2e/artifacts/mail-followup-queue-matched-v2-20260925/report.json`.

| Check | Wide 1440x1000 | Compact 390x844 |
| --- | ---: | ---: |
| measured first paint | 7,708 ms | 6,351 ms |
| message rows | 3 | 3 |
| exact selected-message hydration | pass | pass |
| collection retained / compact sheet opened | pass | pass |
| Back/close restores collection | n/a | pass |
| read, star and archive controls present | pass | pass |
| horizontal overflow | none | none |
| page or console errors | none | none |

The compact proof specifically uses table-row activation. This exposed and
closed a client-component defect where lists emitted the common
`adaos:collectionRowActivated` event but tables did not.

## Orchestration Evidence

The first generation produced revision `002` after one primary response, one
bounded state repair and an exact zero-token replay. Browser review found that
the model had used three views and a side-sheet editor. Before the only chat
correction, Core was strengthened to reject an exact product-view cardinality
mismatch and to require direct state-proof bindings rather than accepting a
view/field reference as state evidence.

The correction's primary response already produced the requested two-view
shape and nine states. Its bounded repair then exposed merged-Brief debt:
`success states` and `success` were equivalent revision aliases, while an old
imperative sentence had been misclassified as a representative state. Core now
coalesces state wording aliases in compact compiler views and does not demand
state-proof uniqueness for authoring-sentence false positives. The retained
repair candidate then compiled and applied as revision `003` without another
model or chat turn.

| Phase | Input | Cached input | Output | Total |
| --- | ---: | ---: | ---: | ---: |
| initial primary + bounded repair | 19,641 | 0 | 2,540 | 22,181 |
| one chat correction primary + bounded repair | 30,550 | 8,448 | 8,036 | 38,586 |
| two exact checkpoint replays | 0 | 0 | 0 | 0 |
| complete matched run | 50,191 | 8,448 | 10,576 | 60,767 |

The user-facing target is met: one creation turn plus one chat correction. The
stricter provider-call target is not yet met: the two user-visible turns each
needed one automatic repair, for four upstream model executions in total. The
run is therefore a successful orchestration/recovery proof, but not yet a
one-model-call proof.

## System Corrections Proven By This Run

1. Platform/compiler failures resume from a digest-verified exact checkpoint;
   they do not automatically restart product reasoning.
2. Semantic revision context is digest-addressed and compact rather than a
   repeated canonical document.
3. Exact product-view budgets and independent state evidence are fail-closed
   Core contracts.
4. Merged state wording aliases are deterministically coalesced before model
   authoring and restored as canonical proof aliases afterward.
5. Table and list collections now share compact detail activation semantics.
6. First-paint telemetry keeps materialization, admission, skill startup and
   provider IO attributable as separate phases.

## Verification

- `tests/test_builder_semantic_prototype.py`: all new cardinality, state-proof,
  alias and false-positive cases pass; the full file has only the four
  pre-existing resource-evaluator postcondition failures.
- public ingress, application runtime, materialization identity and post-ready
  prewarm regression set: 23 passed.
- platform checkpoint classification and compact compiler-facet tests: 4
  passed.
- client table widget tests: 18 passed.
- wide/compact matched browser report: passed.

## Remaining Boundary

This Prototype intentionally stops before Automation, Beta, Trial and real
Gmail attachment. The third-consumer proof already covers the real reusable
provider, Core-owned credential, Applications permission projection, Trial,
publication and Stable workspace path. A later CBS9 benchmark should combine
that lifecycle proof with the matched authoring telemetry above; it must not
compare this synthetic warm Prototype first paint directly with the earlier
live-provider Stable measurements as if they were the same workload.

The callback gateway repository slice covers EIG0, EIG1 and the routed EIG2
implementation. Live public-connected completion still requires external DNS,
certificate/WAF deployment and Google OAuth registration/evidence for the open
EIG2/EIG3 roadmap items; repository tests are not a substitute for that
operational proof.
