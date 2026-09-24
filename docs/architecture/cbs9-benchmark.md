# CBS9: matched benchmark

Status: bounded matched benchmark complete; primary claim not supported.

Last verified: 2026-09-23.

CBS9 freezes workload, environment, model identity, tool budget, requirement
count, authoring input, rubric, target labels, and evaluator recipe before
accepting an observation. Legacy and CBS observations are compared only when
all controls match. The held-out case exposes only its input to the authoring
path; rubric, labels, and recipe remain represented by exact digests.

This first program measures deterministic architecture-declaration cost, not
model quality or ecosystem-wide productivity. Both arms run the same domain
acceptance workload. The cost unit is canonical pretty-printed JSON lines;
domain runtime is shared and excluded from both declaration-cost arms. Metrics
without observations remain `null` and are never coerced to zero.

## Frozen program

The executable program in
`src/adaos/services/capability_binding_state/benchmark_program.py` contains
three matched cases:

| Sequence | Cohort | Case | Legacy marginal cost | CBS marginal cost | Legacy/CBS regressions |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | representative | Flowboard typed CRUD | 164 | 330 | 0 / 0 |
| 2 | representative | AdaOS Drive exact Trial acceptance | 9 | 11 | 1 / 0 |
| 3 | held-out | `booking.reserve` | 16 | 570 | 0 / 0 |

The AdaOS Drive legacy arm reproduces ambiguous selection by application and
release alone. The CBS arm includes Webspace and runtime-root identity, selects
exactly one reviewed Candidate, and passes. This is evidence for correctness
and substitution safety, not evidence that CBS is cheaper to author.

All seven CBS contracts counted by the program remain `application_local`.
There are no independently reusable contracts in this inventory, so
Application-local declarations are excluded from reuse. The predefined primary
claim was:

> Marginal cost declines as verified reusable inventory grows without
> increasing semantic or E2E regression rate.

Its result is `not_supported`: CBS cost did not decline across the frozen
sequence and did not beat the matched legacy arm on the final case. The result
is intentionally retained as a negative finding. It says the current system
has proved explicit identity and fail-closed selection, but has not yet proved
the evolutionary cost benefit expected from a mature reusable inventory.

Post-benchmark note, 2026-09-24: the Gmail CBS reuse proof added the first
independently reused local provider contract and a second consumer. This new
evidence does not retroactively change the frozen benchmark or its negative
result. It supplies the inventory needed for a new matched sample; that rerun
must preserve the original baseline and report both results.

## Historical CBS5 telemetry

The bundle also consumes the retained immutable CBS5 Flowboard telemetry
without rewriting it:

- run: `cbs5-flowboard-crud-v1`;
- telemetry digest:
  `sha256:50794ac197175fb0ea1b39ededed2cadf223f68bf4d265ad58099b3bdd966287`;
- requirements resolved: 1/1;
- E2E result: passed;
- architectural invariants: 5/5;
- resolution plus activation: 1,249 ms;
- manual interventions: 0;
- contract reuse: 2/2;
- package reuse: 0/3.

That historical observation has no legacy arm with the same frozen controls.
It remains explicitly marked as unmatched context and is not used in the new
paired comparison.

## Evidence and limits

The latest local evidence bundle is
`e2e/artifacts/cbs9-20260923/final/benchmark-bundle.json` with digest
`sha256:9971ff63189796050861cf7c8a498578a0119fa39fd6c5c7d57359db79d03211`.
It contains exact cases, controls, treatments, source/identity/evidence
digests, executions, raw historical telemetry, and the derived report.

`causal_claim_admissible=true` means only that every case in this bounded
declaration benchmark has matched arms under its frozen controls. It does not
turn missing invariant-rate observations into measurements, establish model
quality, or justify ecosystem-wide claims.

The next meaningful experiment is not another benchmark formulation. It is to
extract and admit genuinely reusable capability contracts with independent
consumers, then rerun the unchanged program or add a newly frozen sequence.
Only that can test the inventory-growth premise that this result currently
lacks.
