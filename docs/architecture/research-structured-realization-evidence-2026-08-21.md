# Structured TLP Realization Evidence Receipt

Status: local end-to-end operability gate passed; comparative probability
claim remains open.

Verified: 2026-08-21 on the reference Windows AdaOS node.

This receipt records one exact path from an accepted staged formulation to a
published implementation, a real local CPU workflow, and independently
verified operational evidence. It supports the first gate of the
[TLP structured-realization proof protocol](research-compilation-and-sota-program.md#tlp-structured-realization-proof-protocol).
It is deliberately not a TLP efficacy result and not evidence that structured
formulation has a higher success probability than a raw Codex handoff.

## Scope and visibility

The selected unit is:

- ResearchDirection `tlp_structured_proof_02`;
- ResearchTask `tlp_structured_proof_02.task-006`;
- accepted ResearchPrototype
  `sha256:8cfd815af7ff271e98830fef4f6f25e7558ff6f219d9ba1eaef719de809ef6a3`;
- ResearchCompilation
  `sha256:f53ff1422702592349d87a91b983ae9702fe740ff53c8cdf22ae25a754983a6f`;
- AutomationBrief
  `sha256:f68cc6b9a98e3ba40c150d734e332e383f043dea2adad782adef0c6b3c724241`;
- implementation view containing only `TropicalMaxPoo1.ipynb`, digest
  `sha256:7c6c9aa6fe3335f5e2729df183f7bf91a4f9167c8c0f4a77c84847a0aa2299ab`.

The direction SourceBundle also retains `initial-review.md`, digest
`sha256:1450397b37a754d87575ed7c67b6c64ed1ead40166e5cdd31e3080f91139c02e`,
for formulation/evaluation provenance. It is absent from the AutomationBrief
implementation inventory and therefore was not a Codex source artifact. The
legacy `tlp_research` implementation and Workbench management code were not
implementation inputs.

## Autonomous realization and release

The active ImplementationTrack is
`implementation-track:tlp_structured_proof_02.task-006.track-71372d6bc7e7`.
Builder task `task.01M0HD28CX5E3ZHJ782Q50STJF` completed on
`devnode.local-codex` in one model attempt and one Builder iteration, without a
post-start operator directive. The task was a digest-bound successor/rebase
inside the existing task lineage, not a fresh empty-workspace C3 replicate;
this distinction prevents it from being reused in the paired probability
comparison.

The generated direction skill `tlp_structured_proof_02` version `0.1.7`
passed native package validation and consumer-owned acceptance. Its published
source revision is `0926500d4b2f3a2dbbc6814e9ac50bf94d2c3a07` and package
digest is
`sha256:0bd9779df5db0c358d1082be6cde0b13a09ec27039c73f6b29e68bedf3a65d6b`.
The ordinary Artifact Pipeline produced immutable ProjectRelease
`project-release:tlp_structured_proof_02_implementation:sha256:45f0d4ebb49d5db5ab55d2ec1aa4e93e05e7a3c05eb34c506551b078f660e59a`.

ResearchManager joined the release to:

- Study
  `study.55b89fb7c90ae61469996a88c38656ece9a4f128b1645d3e14a0f8c7ee73cc9e`;
- StudyRealization
  `study_realization.a8e9f8073ac5c016447f6836e1c835e97b6b28c7c269720ff3469fb0b32ccc02`,
  digest
  `sha256:3e56195412fd8f437bc20bc00894bdd7b4ce52e189376b3a8098f810e0d05ecd`;
- repeated execution campaign
  `experiment.dcf0ef2287c8543717b65418f56ccf7eea26045fcfcea9a6ced16f01205bd4ce`;
- immutable condition digest
  `sha256:e195f2df1cec05bb9c09cb99e8ae1bce46ccbc214b778131ec9a0f5146337ac4`.

The selected ProjectRelease, StudyRealization and condition digest did not
change during recovery.

## Failure retained and generic recovery

The first campaign,
`experiment.fde201ac73371a7cce1fd041fa7d0d7d663f00f8b0e52d4bfb4f2006ee2c87a2`,
completed its two CPU processes but failed independent verification for all 26
runner artifacts. ResearchManager owned the logical attempts, but the local
executor inherited the manager's skill-data environment and resolved the
candidate's content URIs against the wrong physical owner bucket.

The repair was made in the generic execution boundary rather than the TLP
skill:

- `ExecutionSpec.data_owner_ref` names the workload's physical data owner;
- only a caller with `execution.jobs.delegate_data_owner` may delegate that
  owner;
- the delegated package must be owned by the target skill;
- the working directory must remain below the delegated owner data/source
  root;
- protected skill-runtime variables cannot be forged in an ordinary spec;
- the executor removes ambient protected variables and applies the trusted
  owner environment after the user environment.

Core commit `1b4248d4` implements and tests this boundary. A separate generic
Builder recovery in `4b600c84` reconstructs a validated terminal automation
from its exact Forge checkpoint without repeating Codex. The orchestrator's
typed `repeat_study_experiment` then created a new campaign under the same
StudyRealization and retained the failed parent as lineage.

The successful attempts remained control-plane-owned by
`skill:research_manager_skill` while their provider bindings declared
`data_owner_ref: skill:tlp_structured_proof_02`. A result artifact with digest
`sha256:a20f66f60976e3f9fc4617d73d38ca8c6f4fa045e0d1032a8acc8bbd6f701d5d`
was present only in the TLP skill's runtime data bucket, proving that the fix
did not silently copy candidate data back into ResearchManager storage.

## CPU result and evidence

The local preflight ran two first-attempt, three-epoch CPU arms at seed 17:

- baseline Run
  `run.a85579e751687e9fcaedee78fa4fe4f7be81119b66cc9f9921b661c6c3ed8eb5`;
- intervention Run
  `run.c18c1413fa5952eccbcf5c30a34ad98b8d96528862b2cc29f559470027d64009`;
- shared initialization digest
  `sha256:2c51561c5521828ed7ef16b4ea18444019537414612dad4ac1c1fe5f8c6812fa`;
- observed fixture top-1 metric `100.0` for both arms and paired delta `0.0`.

These values validate workflow, pairing and evidence mechanics only. The
profile has `inference_allowed=false`; it cannot establish either superiority
or equivalence of TLP and MaxPool.

ExperimentResult
`experiment_result.161a95adfcf26b21aba736e87c8d57dc27f4103d149b874a0ee235a6a0ac6d98`
was independently verified from accepted tracker export
`sha256:3485a51d3a1016bf28f0ea973855e7829ce6ad07cee0810961055e7b7d1e631c`
and all 26 runner artifacts. Verification returned `ok=true` and zero errors.

Evidence bundle
`evidence.f1cd39f7fc451ee295e3c9a9460cf0e2b99fafbb892d594e30a1bbec7d89aee2`
has manifest digest
`sha256:a03ff2336a9e58bd864fa95f8fb6435e3e80087ffce878267d853a66862bf420`.
Its selector binds only the successful Experiment. Re-verification checked 56
typed research-record references, re-ran the 26 artifact checks, and returned
zero errors. No record from the failed parent Experiment is in the manifest.

ResearchManager now distinguishes:

- `workflow_validation`: campaign-scoped operational evidence allowed after a
  finalized preflight; it does not freeze the Study and cannot be supplied to
  a claim decision;
- `study_claim`: Study-scoped scientific evidence allowed only after analysis
  and required for a claim decision.

This avoids the invalid shortcut of advancing a smoke-only Study through
execution, unblinding and analysis merely to obtain an Evidence file.

## Durable projection

Direction activity sequence 297--313 contains exact Builder queue/completion,
release candidate, ProjectRelease, StudyRealization, smoke, reconciliation,
campaign recovery, tracker acceptance, result verification and Evidence
events. Each projection retains actor, origin, subject and idempotent source
event identity. The active track is `workflow_evidence_ready`, and its next
step is `review_workflow_evidence`.

Skill activation migrated ResearchManager from its preceding runtime to
`0.36.0` and Research Orchestrator to `0.74.0`; the Study, release, result,
bundle and activity identities remained readable afterward. This is
skill-process/version-migration recovery evidence. An authenticated
Workbench browser reconnect receipt is still required by the broader
ARF7.3-16 gate.

## Trusted consumer-executed provider sequence

The later `ARF7.3-06b` rail removes the remaining dependence on a candidate
interpreting how to perform its own conformance check. Core `e9270d05`
interprets a consumer-owned, declarative `operation_sequence` in an isolated
process. It validates every call against the admitted input/output schemas,
resolves only explicit prior-output and per-item bindings, binds a fresh owner
data root below `ADAOS_TASK_RUNTIME_DIR`, and permits a returned execution
command only when it uses the active Python interpreter, a script below the
candidate skill, and a working directory below that trusted data root. The
command is time-bounded and every expected output must exist at its exact
declared relative path.

ResearchManager runner ABI `1.14.0` materializes this sequence from the exact
accepted ExperimentPlan and target runner identity. The active contract for
task-006 has digest
`sha256:82396719f0e0d9326401dcebb7f5cc110df69d218369a5366c802caef6096404`.
The same plan-bound contract is requested by Workbench session creation and
refresh, independently reconstructed by ResearchManager acceptance, and frozen
by the evaluator for a C3 packet.

On the reference node the trusted worker invoked the task-006 provider in this
order:

1. `dataset_status`;
2. `prepare_attempt` for the accepted intervention arm and workflow-smoke
   profile;
3. the returned production command in its exact working directory;
4. `collect_attempt` through the returned `output_ref`;
5. `verify_artifact` for every collected artifact.

All five steps passed, all 13 collected ContentRefs verified, and
`run_log.json`, `evaluation_audit.json`,
`implementation_observation.json`, `result_record.json`, and
`artifacts_index.json` passed the consumer-owned schemas. A generic negative
test proves that a zero-exit provider which omits `result.json` is rejected.
After standard skill releases and A/B migration the active runtimes are
ResearchManager `0.37.0`, Research Orchestrator `0.75.0`, and Research
Evaluator `0.1.40`; the orchestrator still projected all seven durable
directions, including `tlp_structured_proof_02`.

Subsequent clean calibration showed that schema validation alone did not
execute every semantic invariant. In v37 a fresh C3 provider passed the
trusted sequence but the independent consumer rejected its collection because
`result.primary_metric` was not repeated by a `metric.name=primary_metric`
observation with the same evidence role. The negative result is immutable; it
was not repaired or relabelled.

Core `4bfa6d6a` extends the same domain-neutral sequence ABI with bounded
`contains` assertions and comparisons to other fields in one response. Core
`00afabe6` adds comparisons to prior operation outputs. ResearchManager runner
ABI 1.17 now executes baseline and intervention production paths and checks
their exact arm, seed, evidence class, primary observation, artifact identities
and shared pairing identity before Builder acceptance. Negative end-to-end
tests reject both a mismatched observation and a mismatched cross-step value.
V38 and v39 independently exposed Windows denial of the whole-directory rename
used to publish a freshly extracted SDK snapshot before model execution. The
bounded retry in `b6e9ac44` was insufficient because Windows scanners may hold
an extracted file for an unbounded interval. Core `787ab9d3` removes that
unnecessary shared-publication boundary: the snapshot is extracted directly
into its task-private runtime directory and `SDK_SNAPSHOT.json` is written last
as the readiness receipt. A regression test observes that SDK content exists
before the receipt, and failures now carry the exact snapshot destination.

Frozen v39 retained a fresh C0 result (2/7, EVC false) but excluded C3 before
its first token under the preregistered platform-outage rule. Its summary is
`sha256:690ced473f058da7e020806b2e92d2976b7dffad6b7c68752a37041c3e90e841`.
Fresh v40 is frozen at
`sha256:12fea861988b38b52448416aef54171583892b624d1ab94941caeaf1249b34b4`
on clean detached core `787ab9d3`; it remains in progress and supplies no
probability claim yet.

## Conclusion and remaining proof

This receipt proves that the structured AdaOS path can close end to end on the
reference node and that its gates detect a real platform ownership defect
without rewriting the accepted science or hiding the failed attempt. It also
shows why typed intermediate objects add value beyond a single prose prompt:
they make the unchanged compilation, release, realization, conditions, failed
campaign, replacement campaign, owner boundary and evidence class separately
checkable.

It does not prove the probability hypothesis. The retained v32 paired result
was negative/inconclusive for the primary endpoint, and this task-006 receipt
is one successor-path C3 operability success. The consumer-executed sequence
in `ARF7.3-06b` is now implemented and locally proven, but a new frozen
comparison must still create fresh C0/C3 units and follow the preregistered
repeated paired analysis. A
cross-domain or SOTA claim additionally requires ResearchCompilerBench/ARRB.
