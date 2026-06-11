# Versioning and Public Build Checks

AdaOS publishes three independently versioned surfaces:

- **Core**: the Python package and slot runtime in this repository.
- **Root/backend**: the public API service at `api.inimatic.com` and zoned Root hosts.
- **Client**: the hosted web client at `inimatic.com`.

Version checks must keep two states separate:

- **Served**: the version a public endpoint, catalog, or repository advertises
  as deployable.
- **Used**: the version a running process, browser session, node, skill runtime,
  or active scenario registry entry is actually using.

These values can legitimately diverge while rollouts, browser refreshes, or
workspace syncs are in progress. UI and CLI surfaces should label which side
they are showing instead of merging them into one generic version.

The architecture contract is described in
[Version Observability](../architecture/version-observability.md).

## Observability matrix

| Subsystem | Served source | Used source | Primary operator surface |
| --- | --- | --- | --- |
| AdaOS core / CLI | `adaos_core` in `adaos-versions.json`, Git branch, or update target commit | Active core runtime: dev workspace build metadata or active slot manifest repaired by local `pyproject.toml` / Git subject when a stale default manifest says `0.1.0` | `adaos autostart update-status`, Infra State summary |
| Root/backend | Backend package and deployed container build | `/healthz` on the exact Root zone handling traffic | `https://api.inimatic.com/healthz`, `https://ru.api.inimatic.com/healthz` |
| Hosted client | Hosting build `version.json` | Browser session `client_build_version` reported during the YJS/client handshake | `https://inimatic.com/version.json`, Browsers modal |
| ReDevice/member nodes | Target core update report, Root rollout intent, or `redevice_agent` in `adaos-versions.json` | Member snapshot build/runtime payload or endpoint `agent_version` report | Infra State node selector, ReDevice List, ReDevice Settings |
| Skills | Registry catalog JSON / workspace source manifest | Active skill runtime version and slot | Infra State skills inventory |
| Scenarios | Registry catalog JSON / workspace source manifest | Scenario registry/capacity entry; no separate runtime slot today | Infra State scenario registry, labeled `Registry` |

## Aggregate served manifest

The release contract is `adaos-versions.json`, updated by subsystem CI jobs and
served publicly as `https://inimatic.com/adaos-versions.json` when deployment
publishing is enabled. Generate or update it locally with:

```bash
python tools/write_version_manifest.py --out adaos-versions.json
python tools/write_version_manifest.py --component redevice_agent --version 0.1.2 --build-version 0.1.2+abcd123 --commit abcd123 --source android-ci
```

This file publishes only `served` versions. Runtime endpoints still own `used`
versions:

- Root/backend: `/healthz`
- Hosted client: browser `client_build_version`
- AdaOS core: active slot/dev metadata
- ReDevice Agent: `endpoint_manifest.agent_version` or
  `diagnostic_report.agent_version`

## Core version

The core base version lives in `pyproject.toml` under `[project].version`.

On pushes to `rev2026`, the `AdaOS CI` workflow runs the Python test matrix on
Linux and Windows. After the matrix succeeds, the workflow bumps the patch
version and pushes a commit named `chore: bump adaos version to <version>`.

Runtime slots record the human-readable build version in their slot manifest.
Use these commands on a node:

```bash
adaos autostart update-status
adaos autostart update-status --json
adaos node status --json
```

When AdaOS is launched from a dev workspace without core slots, Infra State
must show `dev | <core version> | <commit>` and read the version from the
project root. Historical files under `state/core_slots` are diagnostics in that
mode, not the active runtime source.

`/healthz.version` on Root is the backend container version, not the local core
slot version.

## Backend version

The backend base version lives in
`src/adaos/integrations/adaos-backend/package.json`.

Deployed Root services expose build metadata through health endpoints:

```bash
curl -sS https://api.inimatic.com/healthz
curl -sS https://api.inimatic.com/v1/health
curl -sS https://ru.api.inimatic.com/healthz
curl -sS https://ru.api.inimatic.com/v1/health
```

Expected fields include:

- `version`
- `build_date`
- `commit`

Use the zoned endpoint that matches the hub zone. For example, RU hubs should be
checked against `https://ru.api.inimatic.com`.

## Client version

The client package version lives in
`src/adaos/integrations/adaos-client/package.json`.

On pushes to `main` or `rev2026`, the client `Firebase Hosting` workflow bumps
the package patch version and deploys the hosting bundle from the updated branch
head. The bump commit is named `chore: bump client version to <version>`.

The hosting build generates Angular build constants and writes a public
`version.json` file:

```bash
cd src/adaos/integrations/adaos-client
npm run build:hosting
```

After deployment, check the hosted client version:

```bash
curl -sS https://inimatic.com/version.json
```

Expected fields include:

- `name`
- `version`
- `build_id`
- `build_version`
- `build_time`

`build_version` combines the package version with the CI build id or commit
short SHA, for example `0.0.1+abc1234`.

Browsers report the client build they are actually running as
`client_build_version` during the client connection handshake. Use that value to
diagnose stale tabs, service-worker lag, or browsers that have not reloaded to
the latest served `version.json`.

## ReDevice Agent version

The ReDevice Agent source version lives in
`src/adaos/integrations/redevice-agent/android/gradle.properties`.

Android diagnostics and endpoint manifests must report the live agent version
using `agent_version`, `agent_version_code`, and, when available,
`agent_build.version_name` / `agent_build.version_code`.

ReDevice operator surfaces compare:

- `Used`: endpoint `endpoint_manifest`, `diagnostic_report`, health, or service
  state payloads.
- `Served`: endpoint policy `redevice_agent.version`, aggregate
  `adaos-versions.json`, or local Gradle source fallback in dev.

If both values are present and differ, ReDevice List and ReDevice Settings show
`drift`. If either side is missing, they show `unknown`.

## Scenario versions

Scenarios do not have a separate runtime slot today. Infra State must not label
their third version plane as `Installed`. Use:

- `Catalog`: registry/catalog version available remotely.
- `Local source`: local source version from the materialized workspace.
- `Registry`: version selected in the scenario registry or member
  capacity entry.

## Release sanity checklist

Before announcing or debugging a release, compare:

```bash
adaos autostart update-status --json
curl -sS https://api.inimatic.com/healthz
curl -sS https://inimatic.com/version.json
python tools/write_version_manifest.py --out .adaos/version-manifest.json
```

For zoned deployments, also check the zone-specific Root:

```bash
curl -sS https://ru.api.inimatic.com/healthz
```
