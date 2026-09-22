# CBS8: derived architecture intelligence

Status: executable derived projection.

CBS8 materializes four disposable views over portable and local CBS records:

- Semantic Graph: typed identities and relations with source-digest provenance;
- Impact Projection: reverse dependency reachability from changed refs/digests;
- Viability Projection: current resolution blockers, especially evidence status;
- Evolver Observations: advisory overlap, freshness, and migration signals.

None of these views owns architecture authority. They contain no credentials,
physical state locators, or mutation commands. `DerivedGraphStore.delete()` may
remove the entire projection; `rebuild()` over the same canonical inputs is
order-independent and restores the same projection digest.

## Evolver maturity

An Evolver proposal begins `application_local` and may advance one stage at a
time:

```text
application_local -> candidate -> reusable -> platform
```

`candidate` requires review evidence. `reusable` requires an extracted package,
conformance evidence, and at least two independent consumers. `platform`
requires an explicit governance decision and platform evidence. Promotion
changes an advisory record only; it never changes a WorkspaceLock, activates a
binding, or mutates a StateSpace.

## Failure boundary

Missing external graph targets are represented as external reference nodes.
They do not become fabricated canonical records. A viability result of
`not_viable` is an explanation for resolver/activation callers, not an
activation veto by itself; the canonical resolver and activation coordinator
retain enforcement authority.
