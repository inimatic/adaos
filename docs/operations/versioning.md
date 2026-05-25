# Versioning and Public Build Checks

AdaOS publishes three independently versioned surfaces:

- **Core**: the Python package and slot runtime in this repository.
- **Root/backend**: the public API service at `api.inimatic.com` and zoned Root hosts.
- **Client**: the hosted web client at `inimatic.com` and `inimatic.web.app`.

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
curl -sS https://inimatic.web.app/version.json
```

Expected fields include:

- `name`
- `version`
- `build_id`
- `build_version`
- `build_time`

`build_version` combines the package version with the CI build id or commit
short SHA, for example `0.0.1+abc1234`.

## Release sanity checklist

Before announcing or debugging a release, compare:

```bash
adaos autostart update-status --json
curl -sS https://api.inimatic.com/healthz
curl -sS https://inimatic.com/version.json
```

For zoned deployments, also check the zone-specific Root:

```bash
curl -sS https://ru.api.inimatic.com/healthz
```
