# Contract compatibility checks (local beta)

AdaOS can compare SDK, Applications MCP, and WebUI contract snapshots and assess
the interfaces used by an Application. A newer Core version alone does not
mean the Application needs repair.

The local beta supports publisher comparison, usage evidence, consumer
assessments, linked Dev Tickets, migration receipts, and read-only status. It
does not automatically rewrite Application source or deploy consumer updates.

## Compare contracts

Capture a baseline before changing Core:

```powershell
adaos dev contracts snapshot --output .tmp/contracts-before.json
```

After the change, compare it with the running checkout:

```powershell
adaos dev contracts compare --baseline .tmp/contracts-before.json
```

Pass `--target <snapshot.json>` to compare two retained snapshots. A repeated
comparison reuses the same contract-change identities.

The SDK metadata build also writes `build/sdk-metadata/contract-changes.json`
alongside its compatibility report when a baseline is supplied. A local
operator can ingest that artifact:

```powershell
adaos dev contracts ingest build/sdk-metadata/contract-changes.json
```

## Assess consumers

New Candidate and Project builds include usage evidence tied to their exact
packages. Index admitted current Application releases and assess them:

```powershell
adaos dev contracts index-releases
adaos dev contracts reconcile
```

Older releases without usage evidence remain unknown. For a separate source
diagnostic, use:

```powershell
adaos dev contracts scan-source <source-directory> --application application:<id> --consumer scenario:<id>
```

The diagnostic parses source without importing Application handlers. Dynamic
or unresolved use remains visible and requires verification. Source-scan
results describe that exact source snapshot; they are not proof about a
different installed release.

Create proposed Dev Tickets only for actionable assessments:

```powershell
adaos dev contracts reconcile --materialize-tickets
```

Unused and compatible interfaces create no Ticket. A failed verification or a
used breaking interface can create one deduplicated Ticket linked to its
contract change. Existing Builder qualification, review, and publication
controls still apply.

## Inspect status

```powershell
adaos dev contracts show application:<id> --consumer-digest sha256:<release-digest>
adaos dev contracts waves
adaos dev contracts events --after 0
```

Use `--state-dir <directory>` on state commands for an isolated local test.
Read commands do not scan source, start Builder, or create missing state.

Applications API records expose `contract_evolution` separately from
`update_available`. Its posture can be aligned, compatible legacy,
verification due, migration ready, action required, or unknown. The
`ContractAligned`, `VerificationFresh`, and `CompatibilityWork` conditions are
independent of release currency and runtime integrity. Older Applications UI
releases may not yet display every condition or detail field.

Authenticated local read endpoints are:

- `GET /api/v1/contract-evolution/applications/<id>`;
- `GET /api/v1/contract-evolution/waves`;
- `GET /api/v1/contract-evolution/events?after=0&limit=100`.

The Application endpoint defaults to the installed release; pass
`?consumer_digest=sha256:<digest>` for an exact historical or source-scan
subject. An incomplete assessment population is not reported as aligned.
Record queries are bounded to 1,000 records by default (the wave listing to
100 waves). Exceeding a bound reports incomplete coverage instead of silently
truncating it. Large portfolios and unattended operation are not qualified in
this beta.

## Record an executed verification

A trusted local runner or operator can attach a completed check to the exact
consumer and target contract:

```powershell
adaos dev contracts record-verification --application application:<id> --consumer-digest sha256:<digest> --change <change-id> --kind contract_test --result passed --report <report-file> --runner operator:<id>
```

Supported check kinds are `contract_test`, `runtime_smoke`, and `conformance`.
The command records the report digest and the supplied runner identity. It
does not run a test or inspect whether a report's prose justifies the result.
Only attest checks that actually ran. Evidence expires after 24 hours by
default; `--valid-hours` accepts 1 through 168 hours.

A new consumer build requires its own evidence. Successful checks do not
rewrite old assessments or automatically close Dev Tickets.

## Declarative migration receipts

The existing interface-deprecation migrator adds a contract migration receipt
when it transforms a supported legacy WebUI input. The receipt identifies the
source, effective projection, migrator and target contract, validation result,
and unresolved findings. Reading or migrating in memory leaves immutable
source history intact. Source write-back remains an explicit Builder change.
