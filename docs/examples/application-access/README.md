# Application Access V1 Example

[`project.yaml`](project.yaml) is a complete Builder-facing declaration for an
Application with owner, member, child, and guest participation. The structured
`permission_profile` is authoritative. `permissions` is its compatibility
projection and must contain the same permission ids.

Run the permission profiler before producing a candidate:

```powershell
adaos builder application-permissions family_tasks `
  --release sha256:<release-digest> `
  --inferred workspace.read `
  --inferred workspace.write `
  --observed workspace.read `
  --json
```

Produce a Trial-scoped report and a redacted CI evidence bundle:

```powershell
adaos builder application-verify family_tasks `
  --release sha256:<release-digest> `
  --scope trial `
  --source-commit <commit> `
  --regression-evidence pytest:tests/test_family_tasks.py `
  --access-matrix-evidence e2e:owner-member-child-guest `
  --pending-action-evidence e2e:application-fallback-handoff `
  --audit-evidence audit:application-access `
  --disclosure-evidence browser:install-update-review `
  --redaction-evidence test:connected-account-redaction `
  --output e2e/artifacts/application-access/family_tasks-trial.json `
  --json
```

For `--scope publication`, regression evidence must begin with `release:`,
`suite:release`, or `skip:bounded:`. The command exits non-zero when a hard gate
fails or is inconclusive. The output binds the release, permission profile,
observed capabilities, verification report, evidence refs, and in-toto
statement by digest; it never contains credential values.

Guest grants use a TTL and are normalized to `session_bound=true`,
`profile_binding=false`, and `durable_approvals=false`. Child grants that cover
sensitive permissions or off-device data require a guardian approval id.
