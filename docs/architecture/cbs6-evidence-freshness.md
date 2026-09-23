# CBS6 Evidence Freshness and External Change

Status: implemented and validated locally.

CBS6 keeps an `EvidenceClaim` as an immutable historical statement and derives
current trust as an `EvidenceAssessment`. An external dependency change never
rewrites the claim or marks an unchanged package broken.

## Assessment order

The current evaluator applies the following precedence:

1. explicit revocation;
2. an incompatible verification result or untrusted issuer;
3. a newer claim for the same subjects and environment;
4. changed subject digests;
5. age, environment, policy, dependency version/fingerprint, observation age,
   and dependency trust-domain checks;
6. admissible when no invalidator applies.

The resulting states are `admissible`, `stale`, `superseded`, `incompatible`,
and `revoked`. Explanations distinguish a historically verified but stale
claim from a newly proven incompatible claim.

The Application CBS semantic-viability API accepts the exact claim and
assessment records and returns this derived explanation with every evidence
obligation. Builder can therefore render the distinction without reimplementing
freshness policy or treating the explanation as authority.

Stale evidence may remain visible under an explicit semantic or simulation
policy. It cannot admit a production resolution, even when a requirement asks
to see stale evidence. Activation accepts only `admissible` evidence and can
rebuild status immediately before the `WorkspaceLock` authority switch; drift
at that boundary leaves the previous lock active.

## External observations

`adaos.external_dependency.observation.v1` records are local, immutable, and
content addressed. They carry dependency identity, observed version and/or
fingerprint, timestamp, trust domain, source, and observer provenance. The
bounded monitor store computes `latest` from immutable history and emits a
change event; the latest pointer is not architectural truth.

Reverification creates a new `EvidenceClaim` whose dependency observation is
current and whose result is independently `verified` or `incompatible`.
Dependency impact is a rebuildable projection over claims, resolutions, plans,
and Applications.

## Executable proof

`tests/test_capability_binding_state_evidence.py` uses a synthetic Jira API
fingerprint transition:

```text
Jira v3 / fingerprint A -> admissible
Jira v4 / fingerprint B -> stale -> production inadmissible
reverify(v4)             -> admissible or incompatible new claim
```

The activation test additionally changes the observed status between planning
and `switch-lock` and proves that no new `WorkspaceLock` is committed.

## Deferred generalization

- production provider adapters beyond the bounded external monitor interface;
- remote revocation feeds and trust-root distribution;
- fleet-wide queue scheduling and prioritization;
- operator UI beyond the stable explanation projection.

These remain outside the CRUD/Jira-sized proof and do not weaken the
fail-closed production boundary.
