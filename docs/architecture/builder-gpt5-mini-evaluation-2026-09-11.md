# Builder GPT-5 Mini Evaluation

Date: 2026-09-11. Status: one exploratory run completed; no model default changed.

## Protocol

Run `sdk-semantic-v2-gpt5-mini-20260911-01` executes the unchanged
`volunteer-roster-en` case through DEV Builder and `sdk.v1`. The suite permits
one automatic semantic repair. One E2E repetition therefore produced two
generation requests, not two independent experiments. Neither the case nor
the model was rerun after inspecting the failure.

The direct historical reference is `sdk-semantic-v2-20260911-13`:

- suite digest: `sha256:e22d5fc33809fd96790c7b73dea94000a02fda2335fc05fe41e83b39b069806c`;
- case digest: `sha256:2aac668e8ebe4fd17741d86a41590fa1381ca5f629207c49132a246ba7704271`;
- identical system message, stable context message, and strict semantic-v2 schema;
- identical Client catalog and inventory digests;
- 8,000-token output allowance, streaming, and development profile retained;
- configured grader remains `gpt-4.1`, version 9, threshold 0.85;
- browser execution is off in both runs.

The earlier request selected the Root default, which telemetry resolves to
`gpt-5-2025-08-07`. The new request explicitly selected `gpt-5-mini` and
telemetry confirms `gpt-5-mini-2025-08-07`. Its request sets minimal reasoning
and omits temperature. The historical request left reasoning unspecified and
sent temperature 0.2; the inspected Root adapter defaults GPT-5 to minimal
reasoning and removes unsupported temperature. These adapter defaults are
code evidence, not a retained copy of the historical effective provider request.
Both responses report zero reasoning tokens.

The Core commits differ only in documentation. The checked-out Client moved
from `20f0aca` to `203aeb9`, whose only changes are package version metadata.
The isolated application's name, IDs, Brief digest, and request IDs differ as
expected. The first mini request had a cold prompt cache, while the reference
reused 2,304 input tokens. This is an exploratory comparison, not a matched
warm-cache benchmark or an accepted baseline.

## Measurements

| Measurement | GPT-5 reference | GPT-5 mini |
| --- | ---: | ---: |
| Complete case | 39.238 s | 88.633 s |
| Primary provider execution | 21.023 s | 39.325 s |
| Primary time to first token | 0.592 s | 1.405 s |
| Primary queue | 3 ms | 3 ms |
| Repair provider execution | none | 38.003 s |
| Generation calls, including repair | 1 | 2 |
| Primary input tokens | 2,911 | 2,918 |
| Primary cached input tokens | 2,304 | 0 |
| Primary output tokens | 3,192 | 2,848 |
| All generation fresh / cached input tokens | 607 / 2,304 | 7,212 / 1,792 |
| All generation output tokens | 3,192 | 5,592 |
| Generation and semantic validation | passed | failed after repair |
| Independent outcome grade | 0.925; hard gate failed | not reached |

The reference spent another 6.456 s in the grading step. Mini stopped before
scenario validation and grading because no admissible generated candidate
was available. Its missing outcome score must not be reported as zero or
compared directly with 0.925. The reference also fails overall: it does not
provide an executable overlap guard.

Earlier GPT-5 runs `-11` and `-12` used one generation call each, with primary
execution of 26.794 s and 30.084 s, and complete cases of 46.987 s and
49.624 s. They provide historical context only because their prompt or grading
conditions differ. In this mini run the first answer was already slower and
the repair added substantial latency. Root queueing was 3 ms / 7 ms, tools and
MCP were unused, and both provider responses completed. No output truncation
or generation timeout occurred. A single cold-cache observation cannot
establish a model-wide latency distribution.

## Input And Output Review

The complete primary request and both complete responses were retained and
inspected, together with the repair packet. The first answer correctly
separated volunteers, shifts, and assignments, supplied EN/RU labels, and kept
contact fields in selected details. It failed deterministic validation on:

- no collection view for the assignment resource;
- a conflict proof referring to a field absent from its view;
- missing coverage for the accepted inspect operation.

The repair input includes the complete candidate, full model Brief, original
instruction, all three findings, and a preservation instruction. The repair
exposed the conflict field and added the inspect binding, but left the missing
collection unresolved and dropped all four existing state requirement
bindings. It also removed a synthetic "No Volunteers Present" record.

Both answers contain additional semantic defects: foreign-key fixtures contain
record IDs but the declared target fields contain names or dates; assignment
references are also duplicated in an attachments field. Overlap avoidance is
bound to a text field rather than an executable guard. These are findings from
manual candidate inspection; the reported validator findings are incomplete
because compilation stops before later relationship checks.

Context defects contribute to this result. The Brief leaves actors and
entities unknown despite their presence in the original request. The initial
system message does not state the compiler's mandatory per-resource collection
view invariant; the model receives that rule only on repair. The repair then
requires a complete rewrite to fix a few fields and relies on prose to preserve
unaffected requirement bindings. Conversely, the relationship rule and the
instruction against placeholder records are present in the original input
and were not followed. This evidence supports improving both context
sufficiency and repair preservation before evaluating a smaller-model route
for general use. The 8,000-token allowance was not reduced.

## Reproduction And Evidence

Use a process-scoped model override; it does not change the running AdaOS
service or user defaults:

```powershell
$env:ADAOS_BUILDER_LLM_MODEL = 'gpt-5-mini'
adaos builder e2e e2e/builder/development/archetypes/suite.yaml --case volunteer-roster-en --repetitions 1 --browser off --run-id UNIQUE_RUN_ID
```

Restore the previous environment value when using a reusable shell. The
recorded run used a separate process and restored that value in `finally`.

Local bundles are under `e2e/artifacts/builder/<run_id>/`. Each contains the
run manifest, case report, input attribution, terminal diagnostic, and complete
model I/O with content digests. The mini primary Root job is
`llm_job_eda567378b4e4c6cbb57872d`; its repair is
`llm_job_9144fdf3acbc4f05b63bdd45`. No manually corrected candidate was admitted.
The mini `report.json` SHA-256 is
`529a4c904337f10e066af275d62d7334a5fb3fee344a1aca8a6724bb85e0cd6b`.

Official model capability reference: [GPT-5 Mini](https://developers.openai.com/api/docs/models/gpt-5-mini).
The documentation describes its intended low-latency use; measured task results
remain the basis for routing decisions.
