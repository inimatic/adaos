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
Frozen v40 retained a fresh C0 result (2/7, EVC false) under result digest
`sha256:f3c8e13989b275454a157930f23ee39874551b78f073351af56ccdc4ee2815de`.
Its C3 worker reached the durable ready handshake before a host power loss.
After restart, the idempotent Builder start retained the exact candidate,
DevelopmentSession, task, packet and attempt identities and relaunched only
the dead worker. The task then failed before its first model token because the
directory-form source snapshot no longer matched its frozen manifest. The
attempt is therefore a preregistered platform-outage exclusion, not a C3
score. The immutable incomplete summary is
`sha256:04b1acf74b0b0a1f2868b0123faef265ee2d227c7aa1c3d24a939ac786743ce6`.

Core `415c0357` removes mutable directory payloads from new Builder source
snapshots. It stores one deterministic content-addressed ZIP, verifies both
the archive digest and every manifest-bound logical tree, rejects unbound
archive entries, and extracts only safe admitted paths. Legacy directory
snapshots remain readable. Calibration runner `0.1.17` also reuses an exact
existing DevelopmentSession and binding without recreating the Project,
instructions, or source before relaunch. Fresh v41 is frozen at
`sha256:e994111297c7cdc82e6bacc232d3e37ab64fa933e2d6b0fb6543bc7f357d50bf`
on clean detached core `415c0357`; all 25 packets existed before execution and
the counterbalanced order started with C3. The first C3 implementation built a
real Torch runner, passed candidate tests and strict validation, but exhausted
its two-attempt budget while the trusted worker disclosed protected execution
environment overrides one at a time. Independent result
`sha256:ba9084d53dd8482e32aa840986f0a44ee7edf03babb797e650652ecf2d5112b2`
is EVC false and passes 1/7 mandatory checks. Matched C0 completed in one
attempt under result
`sha256:6828c6b96d6d4cc8bb5fcc79b057a59c3410a845901a5cb1dedc834d58fc58b9`,
is EVC false, and passes 2/7 checks. The pair is therefore a false/false tie.
Even four treatment-only wins in the remaining pairs could reach only the
preregistered one-sided `p=0.0625`; v41 stopped for futility and remains an
immutable `incomplete_no_claim` comparison under summary
`sha256:51418e60ab6aa3d00b0bb4a2fd8f56c24f05a3b14c2f2d842580b12a71e85bb3`.

The failed pair exposed two domain-neutral rail defects, not a basis for
rescoring v41. Core `e4c91d2f` reports every protected or invalid
`ExecutionSpec.environment` key in one deterministic diagnostic, so one repair
can remove the complete violation set. Manager `0.41.0` / runner ABI `1.18`
now declares that boundary explicitly. Evaluator `0.1.42` keeps its hidden
probe-request digest for provenance and scoring but removes that
evaluator-owned field before invoking the candidate's exact public input
schema. Both skills passed their normal `adaos skill test` gates and were
activated through A/B migration. These fixes apply only to a new frozen task.

Fresh v42, `tlp-structured-formalization-paired-v42`, was then frozen at
`sha256:0697567836ce2eafae7b9d624f7c280dc832cc028f464fd1f2027e87549f2a6e`
on a clean detached checkout of core `e4c91d2f` and workspace `d8633460`.
It bound `gpt-5.5` at high effort, five counterbalanced pairs, a matched
12,000,000-token/10,800-second downstream budget, zero human interventions,
and 25 packets materialized before execution. Its zero-result summary was
`sha256:e5fbf583c1c0bd7e00b5421abb492d6d660678ad27d7cd73a7254e48064bebd6`.
The first C0 used 4,400,983 model tokens and passed 2/7 mandatory checks;
independent result
`sha256:348060993ba6e4c23c2e644e68e0a7482f6f0c0a6e730cdfce63a10e6b930aaa`
is EVC false. The matched C3 generated and self-tested a real Torch direction
skill without intervention, but the trusted package-shaped pytest gate timed
out at its fixed 60-second lifecycle boundary before installation. Result
`sha256:34254d0adf3c54f212ceb0bfa1d74040512e3f39d1f963b331e3e15622827bed`
is therefore EVC false (1/7), with `engineering_compilation` as the failure
stage after 6,408,492 model tokens. The false/false tie again makes the best
possible remaining one-sided result `p=0.0625`; execution stopped under the
frozen futility rule and summary
`sha256:ddca458d835ca5fb09d49e644e8727c35563849b8f5061d00dd192e0f57a31d2`
remains `incomplete_no_claim`.

Post-outcome diagnosis did not rescore v42. The exact package-shaped tests
passed unchanged in 29.83 seconds when replayed after the host load subsided;
they use one-sample bounded fixtures and do not run the three-epoch scientific
smoke. Thus the v42 stop identifies a domain-neutral validation-budget
confound: a fixed 60-second wall limit can penalize dependency-heavy typed
implementations under transient CPU contention even when their bounded suite
is valid. The next task must bind the package-test allowance to the immutable
DevelopmentSession execution budget and expose the same exact allowance to
Codex and the trusted worker. V42 itself stays immutable.

Core `69597d27` implemented that generic allowance: the trusted package-test
budget is derived from the immutable DevelopmentSession wall budget, capped at
300 seconds, and the same value is present in the packet, prompt and worker.
Fresh v43 was frozen at
`sha256:171a180e2158f9913635ef82b1216d90753ffd6aad476438b010d6ce61d15228`
on that clean detached core and workspace `d8633460`, with five
counterbalanced pairs, 25 packets materialized before execution, matched
12,000,000-token/10,800-second budgets and no human intervention. Four pairs
completed in valid observed order. C0 was EVC false in all four realizations
and passed 2/7 mandatory checks each time. C3 was EVC true in the first three
realizations and passed 7/7 checks; the fourth passed Builder validation and
production-operation conformance but failed the installed consumer smoke,
producing three treatment wins and one false/false tie. Even a treatment win
in the fifth pair could have produced only four discordant wins and one-sided
`p=0.0625`; the fifth pair was therefore not started under the preregistered
futility rule. Frozen summary
`sha256:a0b07782df011a46ef782312c5d7c2035b2df2ab207fb7baf79e356df67b3805`
remains `incomplete_no_claim`. Descriptively, C3 completed 3/4 versus C0 0/4,
but that is not the preregistered probability claim.

Post-outcome diagnosis retained the failed result and found a domain-neutral
rail defect. The provider prepared content identities under canonical
`<runtime-bucket>/data`; `execute_dev_spec` alone rebound
`ADAOS_SKILL_INTERNAL_DATA_ROOT` to `<runtime-bucket>/data/internal`. The
candidate completed its CPU run and wrote four documents, then failed while
constructing the fifth ContentRef because the prepared path was no longer
relative to the substituted root. Builder's contract runner and the normal
execution service already preserve one canonical root. Core `a7dc187a` makes
DEV execution consistent and adds a subprocess regression test that observes
the same owner root through the SDK. The focused SDK/worker/execution suite
passes through `adaos tests run`. This diagnosis does not rescore v43. A fresh
six-pair comparison is required so one tie does not make significance
mathematically unreachable.

## Conclusion and remaining proof

This receipt proves that the structured AdaOS path can close end to end on the
reference node and that its gates detect a real platform ownership defect
without rewriting the accepted science or hiding the failed attempt. It also
shows why typed intermediate objects add value beyond a single prose prompt:
they make the unchanged compilation, release, realization, conditions, failed
campaign, replacement campaign, owner boundary and evidence class separately
checkable.

It does not prove the probability hypothesis. The retained v32 paired result
was negative/inconclusive for the primary endpoint, v41 and v42 stopped after
false/false ties, and v43 produced a promising 3/4 versus 0/4 descriptive
result but correctly stopped without a claim after its fourth-pair tie. The
consumer-executed sequence in `ARF7.3-06b` is implemented and locally proven,
but a new zero-result six-pair comparison on core `a7dc187a` or a declared
successor must still create fresh C0/C3 units and follow the preregistered
paired analysis. A
cross-domain or SOTA claim additionally requires ResearchCompilerBench/ARRB.
