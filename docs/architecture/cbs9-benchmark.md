# CBS9: matched benchmark

Status: benchmark harness complete; historical comparison incomplete.

CBS9 freezes workload, environment, model identity, tool budget, and
requirement count before accepting an observation. Legacy and CBS observations
are compared only when all controls match. Representative and held-out cohorts
are reported separately.

Metrics with no source observation are emitted as `missing`/`null`; they are
never replaced with zero. A report permits a causal comparison only when every
frozen case has both a legacy and CBS arm.

## Accumulated telemetry result

The retained CBS5 Flowboard CRUD artifact contains one valid
`adaos.cbs.benchmark_telemetry.v1` record:

- run: `cbs5-flowboard-crud-v1`;
- requirements resolved: 1/1;
- E2E result: passed;
- architectural invariants: 5/5;
- resolution + activation: 1,249 ms;
- manual interventions: 0;
- contract reuse: 2/2;
- package reuse: 0/3.

No matched legacy observation was recorded with the same frozen workload,
environment, model, and tool budget. The current benchmark therefore reports
the legacy arm as missing and sets `causal_claim_admissible=false`. The data
supports the CBS5 executable proof, but not yet a claim that CBS is cheaper or
faster than legacy implementation.

## Next measurements

1. Capture a legacy Flowboard CRUD arm with the frozen CBS5 controls.
2. Emit benchmark-compatible telemetry from the booking proof and run a
   separately frozen legacy arm.
3. Instrument Builder Automation/Trial for Applications, Flowboard, and AdaOS
   Drive with identical model/tool budgets.
4. Reserve at least one application as held-out before tuning resolver or
   Builder behavior.

Historical UI E2E JSON and screenshots remain useful product evidence, but are
not retroactively treated as benchmark telemetry because they do not contain
the frozen controls.
