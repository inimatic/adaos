# AdaOS

![AdaOS CI](https://github.com/inimatic/adaos/actions/workflows/ci.yml/badge.svg)

AdaOS is a developer platform for personal assistant runtimes. It connects hubs,
member nodes, browsers, skills, scenarios, and operational tooling into one
assistant environment while keeping the lower-level runtime machinery available
for diagnostics and integration work.

[Documentation](https://inimatic.github.io/adaos/) |
[Quickstart](docs/quickstart.md) |
[Deployment](docs/deployment.md) |
[Versioning](docs/operations/versioning.md)

## What is in this repository

- Python 3.11.9+ AdaOS core package and CLI (`adaos`)
- Local HTTP API, SDK modules, and runtime services
- Skill and scenario development workflows
- Hub/member node support and join-code onboarding
- Browser/device access architecture and client integration contracts
- Bootstrap scripts for Linux, macOS, Windows, Codespaces, and Colab-style labs
- MkDocs documentation and test suite
- Optional integration trees for the hosted client, backend, and infrastructure

## Core ideas

- An **Assistant** is the persistent user-facing environment, backed internally by
  a subnet.
- A **Hub** owns a subnet and connects to Root.
- A **Member** is another runtime node that joins a hub-managed subnet.
- A **Browser** is a web endpoint connected through the Inimatic/AdaOS client.
- **Skills** implement focused capabilities such as integrations, automations,
  assistant behavior, or UI logic.
- **Scenarios** coordinate multi-step flows across services, skills, and nodes.
- **Webspaces** define web access and projection contexts such as Main, Owner,
  Guests, or Developer.

## Quick start

Clone and bootstrap:

```bash
git clone -b rev2026 https://github.com/inimatic/adaos.git
cd adaos
bash tools/bootstrap.sh --zone ru --dev
source .venv/bin/activate
adaos --help
```

Windows PowerShell:

```powershell
git clone -b rev2026 https://github.com/inimatic/adaos.git
cd adaos
powershell -ExecutionPolicy Bypass -File tools/bootstrap.ps1 -ZoneId ru -Dev
.\.venv\Scripts\Activate.ps1
adaos --help
```

Run a development API:

```bash
adaos api serve --host 127.0.0.1 --port 8777
curl -i http://127.0.0.1:8777/health/live
curl -i http://127.0.0.1:8777/health/ready
```

Use port `8777` or `8778` when you want the browser client to auto-discover a
local runtime. Use a different port, such as `8779`, when the hosted client
should stay routed through Root.

More setup paths are documented in [Quickstart](docs/quickstart.md).

## One-line bootstrap

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --zone ru
```

Windows PowerShell:

```powershell
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -ZoneId ru
```

Useful options:

```bash
--join-code CODE
--node-name "Kitchen Member"
--role hub
--install-service auto
--no-core-update
--use-git-from https://github.com/<you>/adaos.git --rev my-branch
```

Windows uses the corresponding PowerShell names, for example `-JoinCode`,
`-NodeName`, `-Role`, `-InstallService`, and `-NoCoreUpdate`.

Details: [bootstrap variants and checkout maintenance](docs/operations/common-commands.md#one-line-bootstrap-variants).

## Deployment modes

- **Development**: `tools/bootstrap.* --dev` plus direct `adaos api serve`.
- **Production**: init/bootstrap scripts plus `adaos autostart enable` or
  `--install-service auto`, with supervisor-managed runtime slots.
- **Colab/lab**: repository bootstrap in a notebook, usually as a temporary
  member node with `--no-core-update`.

See [Deployment](docs/deployment.md) for production, development, and Colab
commands.

## Browser and member connection

Open the public client:

```text
https://inimatic.com/?zone=ru&mode=login
```

Create a member join-code on the hub:

```bash
adaos hub join-code create
```

Join from the member:

```bash
bash tools/bootstrap.sh --join-code CODE --zone ru --node-name "Kitchen Member"
```

See [Browser and Member Connection](docs/onboarding/browser-and-member.md) and
[Member node onboarding](docs/onboarding/member-node-phase1.md).

## Versions and health

Core version is stored in `pyproject.toml`. The `AdaOS CI` workflow bumps the
patch version after the full test matrix passes on `rev2026`.

Check deployed backend and client versions:

```bash
curl -sS https://api.inimatic.com/healthz
curl -sS https://ru.api.inimatic.com/healthz
curl -sS https://inimatic.com/version.json
```

Local runtime slot version:

```bash
adaos autostart update-status
adaos node status --json
```

Details are in [Versioning and Public Build Checks](docs/operations/versioning.md).

## Common commands

```bash
adaos --help
adaos where
adaos install
adaos update
adaos skill list
adaos scenario list
adaos node status
adaos node reliability
adaos autostart status
```

Details:
[full command cookbook](docs/operations/common-commands.md),
[runtime operations](docs/cli/runtime.md), and
[CLI reference](docs/reference/cli.md).

When a production CLI command reports `slot_shell_required`, switch into the
active runtime slot first:

```bash
source tools/slot-shell.sh --cd
```

PowerShell:

```powershell
. .\tools\slot-shell.ps1 -Cd
```

## Documentation

- [Quickstart](docs/quickstart.md)
- [Deployment](docs/deployment.md)
- [Versioning](docs/operations/versioning.md)
- [CLI reference](docs/reference/cli.md)
- [Runtime and operations](docs/cli/runtime.md)
- [Architecture overview](docs/architecture/overview.md)
- [Device Access and Browsers](docs/architecture/device-access-and-browsers.md)
- [Member-Hub Connectivity](docs/architecture/member-hub-connectivity.md)
- [Client integration README](src/adaos/integrations/adaos-client/README.md)

## Development

Run tests:

```bash
pytest
```

Build documentation locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve
```

Project layout:

```text
src/adaos/        Core package, apps, services, SDK, templates
tests/            Test suite
docs/             Documentation source
tools/            Bootstrap and diagnostic scripts
```

## Status

AdaOS is an evolving platform. This repository is the open developer-facing
runtime foundation for building, testing, and operating skills, scenarios, and
node services. Hosted infrastructure, publication workflows, and broader
operator tooling may evolve in adjacent integration repositories.

## License

MIT. See [LICENSE](LICENSE).
