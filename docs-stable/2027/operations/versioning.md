# Versioning And Health

Status: public current-state operations guide.

Core version information is stored in `pyproject.toml`. The `main` branch is
the active rollout branch for this documentation track.

## Continuous Integration And Releases

`dev2027` is the development branch. Its pushes receive bounded static checks;
complete regression runs daily from 04:17 Moscow time, with related component
checks following during the same nightly cycle. A previous successful result is
reused only while the exact committed source remains unchanged. Manual
qualification always executes the tests again.

`main` is the release source. Main changes run release qualification and then
automatically deliver the qualified components: the web client to hosting,
backend images to the zonal Roots, and the exact Core revision to the managed
node-update control plane. Native packages are built from main; signed updates
and device installation retain their existing product-specific controls.

CI does not create a patch-version commit after every development push.
Versions are selected deliberately. Root storage admission, readiness checks,
retained compatible rollback and node slot validation remain part of delivery.

## Hosted Health Checks

```bash
curl -sS https://api.inimatic.com/healthz
curl -sS https://ru.api.inimatic.com/healthz
curl -sS https://inimatic.com/version.json
```

## Local Runtime Status

```bash
adaos autostart update-status
adaos node status --json
```

## Public Documentation Versioning

Public documentation is versioned by major release under `docs-stable/`.
The 2027 track lives under `docs-stable/2027/` and is the source for GitHub
Pages.

## Application runtime lifecycle (local beta)

Applications, Management and Builder share the Core-owned **Runtime and Trials**
view. Core selects the running edition, admits execution, records deadlines and
keeps the operation history. Builder's authoring history is separate: an old or
missing workflow does not change which immutable release is running.

| State or action | Meaning and data handling |
| --- | --- |
| Temporary Trial | Has an execution deadline. Accepting its Candidate does not extend it. |
| Installed local Beta | An owner-reviewed admission of an exact healthy, accepted Candidate. No Stable release or publication is required; the original Trial deadline remains recorded. |
| Expired Trial | Cannot execute. Core observation and reviewed recovery remain available without opening that application. |
| Finalize | Adopt the exact accepted Beta as Stable and keep its working data. Source preparation runs inside this action; it does not require a replacement Beta. |
| Deactivate Beta | Restore the verified pinned previous source, or explicitly choose an available Stable baseline. Beta writes stay retained and are not silently copied backwards. |
| Delete unused Trial | Review references and active jobs first. Move the runtime to a recoverable archive; retain its data. This is not an immediate full disk-space reclaim. |
| Uninstall | Remove the application runtime through a reviewed plan. Retain data unless a separately supported, explicit deletion operation is reviewed. |

The view shows Candidate acceptance, admission, optional Stable baseline, retained
Trials, UI-refresh state and correlated operations independently. A Workspace
installation can be a published prerelease; the legacy field name
`stable_installation` alone is not proof of Stable status.

Finalize uses the reviewed source snapshot rather than current development files.
It stops before adopting data if that snapshot cannot be verified. Publishing the
application for installation in other subnets remains a separate reviewed action.

Plans require explicit confirmation of the exact digest and current revision.
Cancel saves a cancellation, not just a closed dialog. Recovery resumes only a
known Core intent. An unconfirmed external executor effect stays unknown until
its retained evidence can be verified; it is never blindly repeated. A committed
transition with a pending interface refresh is not reported as a failed migration.

If the management applications are unavailable, the local owner can inspect Core:

```bash
adaos applications show APPLICATION_ID
adaos applications trials APPLICATION_ID
adaos applications operations APPLICATION_ID
adaos applications --help
```

`plan`, `apply`, `cancel` and `recover` are explicit local-owner commands. Recovery
preserves the original authenticated actor; it does not impersonate another user.
Stable package deployment still uses the running Core executor. None of these
commands requires a running Builder or Applications UI.

These controls are beta behavior shipped with the corresponding Core and consumer
updates. Backward-schema migration of newer Beta writes, automatic destructive
purging, automatic Trial renewal and autonomous publication are not supported by
this lifecycle milestone.

