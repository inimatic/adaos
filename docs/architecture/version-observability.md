# Version Observability

AdaOS versioning is not one scalar. A dashboard is correct only when it shows
which plane a version came from and does not merge deployable artifacts with
running processes.

## Version Planes

Use these names consistently:

| Plane | Meaning | Owner |
| --- | --- | --- |
| Source | Version declared in the source tree, such as `pyproject.toml`, `package.json`, `gradle.properties`, `skill.yaml`, or `scenario.json`. | Repository authors and bump jobs |
| Served | Version advertised as available for rollout or download. This is a CI/release output, not live process state. | CI/CD for each subsystem |
| Target | Version a policy or update attempt wants a node or endpoint to run. | Supervisor, Root policy, rollout controller |
| Used | Version reported by a live runtime, browser session, endpoint agent, or active slot. | Runtime process or endpoint |
| Registry | Version selected in a local registry for an artifact without its own runtime. | AdaOS registry and workspace sync |

`served` and `used` can legitimately differ. The UI should show this as
`ok`, `drift`, or `unknown`, not overwrite one value with the other.

## Aggregate Manifest

AdaOS should publish one release manifest for source/served versions across
subsystems:

```json
{
  "schema_version": "adaos.version-manifest.v1",
  "generated_at": "2026-06-07T00:00:00+00:00",
  "components": {
    "adaos_core": {
      "component": "adaos_core",
      "served": {
        "version": "0.1.218",
        "build_version": "0.1.218+7d5e114",
        "commit": "7d5e114388a8a9a8df1a1b9a08a8ad99250c7e22",
        "source": "pyproject.toml",
        "updated_at": "2026-06-07T00:00:00+00:00"
      },
      "used": null
    }
  }
}
```

The expected public location is `https://inimatic.com/adaos-versions.json`.
Local/dev nodes may also read `adaos-versions.json`,
`version-manifest.json`, or `.adaos/version-manifest.json`.

The manifest is CI-owned. Runtime code must not write `used` into this file.
Runtime health, browser handshakes, endpoint reports, and slot manifests remain
the authoritative `used` sources.

`tools/write_version_manifest.py` is the local/CI helper for generating this
contract. CI jobs can run it for all components or update one component with
explicit `--component`, `--version`, `--build-version`, `--commit`, and
`--source` values.

## Component Contracts

| Component | Source | Served | Used | Operator surface |
| --- | --- | --- | --- | --- |
| AdaOS core / CLI | `pyproject.toml` | `adaos_core` in aggregate manifest or rollout target | Dev root metadata or active slot manifest repaired from local source when stale | `adaos autostart update-status`, Infra State |
| Root/backend | backend `package.json` | `root_backend` in aggregate manifest and deployment metadata | Exact zoned `/healthz` handling traffic | `https://api.inimatic.com/healthz`, `https://ru.api.inimatic.com/healthz` |
| Hosted client | client `package.json` | `hosted_client` in aggregate manifest and `https://inimatic.com/version.json` | Browser session `client_build_version` handshake | Browsers modal |
| ReDevice Agent | Android `gradle.properties` | `redevice_agent` in aggregate manifest or endpoint policy target | `endpoint_manifest.agent_version`, `diagnostic_report.agent_version`, or agent build payload | ReDevice List and ReDevice Settings |
| Skills | workspace `skill.yaml` | catalog JSON in the skill registry | active skill runtime slot/version | Infra State skills inventory |
| Scenarios | workspace `scenario.json` | catalog JSON in the scenario registry | no separate runtime today; use active registry only | Infra State scenario registry |

## UI Rules

Infra State and related modals must label version columns by plane:

- Skills may show `Catalog`, `Workspace`, and `Runtime` because a skill can have
  an installed/active runtime.
- Scenarios must show `Catalog`, `Local source`, and `Registry`. They do not
  have a separate installed runtime slot today.
- AdaOS core launched from a dev workspace must show `dev | <version> |
  <commit>`. Historical files under `state/core_slots` are diagnostics in that
  mode.
- Browser surfaces must compare hosted `version.json` with each session's
  `client_build_version`.
- ReDevice surfaces must compare `used` agent version reported by the endpoint
  with `served` version from policy or the aggregate manifest. If either side is
  missing, show `unknown`.

## ReDevice Payload Rules

ReDevice is endpoint-only, not a hub or member runtime. The endpoint itself owns
the `used` software version. The preferred fields are:

```json
{
  "endpoint_manifest": {
    "schema_version": "endpoint-manifest.v1",
    "agent_version": "0.1.1",
    "agent_version_code": 2,
    "agent_build": {
      "schema_version": "redevice-agent-build.v1",
      "version_name": "0.1.1",
      "version_code": 2
    }
  },
  "endpoint_policy": {
    "redevice_agent": {
      "version": "0.1.2",
      "version_code": 3
    }
  }
}
```

`endpoint_manifest` and `diagnostic_report` are used-side payloads. Policy and
aggregate manifest values are served/target-side payloads. Access-link registry
storage must preserve these fields; otherwise ReDevice Settings and ReDevice
List cannot provide reliable version observability.
