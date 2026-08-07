# AdaOS

![AdaOS CI](https://github.com/inimatic/adaos/actions/workflows/ci.yml/badge.svg)

AdaOS is a local-first platform for building and operating distributed
assistant environments that connect people, AI agents, applications, and
devices. It connects hubs, member nodes, browsers, skills, scenarios, and
governed operational tooling while keeping the lower-level runtime machinery
available for development, diagnostics, and integration work.

This repository contains the shared developer-facing runtime foundation.
AdaOS Home, aResearcher, AdaOS Campus, and AdaOS Enterprise are solution
directions built on that foundation; they are not separate runtimes or codebases
and currently have different maturity levels.

[Documentation](https://inimatic.github.io/adaos/) |
[Product model](docs/product/index.md) |
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

## Product and solution directions

| Direction | Kind | Purpose |
| --- | --- | --- |
| **AdaOS Home** | Deployment profile | A private environment for a person, household, routines, and devices. |
| **aResearcher** | Solution agent and workbench | A governed, reproducible research loop over AdaOS Research Fabric. |
| **AdaOS Campus** | Institutional deployment profile | Course, teaching, learning, seminar, and laboratory environments. |
| **AdaOS Enterprise** | Organizational deployment profile | Team, process, policy, integration, and audit environments. |
| **reDevice** | Endpoint family | A reusable physical interaction, media, sensing, or control endpoint across domains. |

Home, Campus, and Enterprise describe deployment and governance profiles.
aResearcher is a cross-profile solution agent. reDevice is an endpoint family,
not a separate application domain. See [AdaOS Product Model](docs/product/index.md)
and [Solution Directions](docs/product/solution-directions.md) for the normative
boundaries, maturity labels, canonical scenarios, and non-goals.

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

Run a development runtime:

```bash
adaos dev serve --host 127.0.0.1 --port 8777
curl -i http://127.0.0.1:8777/health/live
curl -i http://127.0.0.1:8777/health/ready
```

Use `adaos api serve` only for lower-level foreground API debugging. It is a
development-only path and does not manage production or A/B slot lifecycle;
production runtimes use `adaos autostart ...`.

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

- **Development**: `tools/bootstrap.* --dev` plus `adaos dev serve`; use
  `adaos api serve` for lower-level HTTP API debugging.
- **Production**: init/bootstrap scripts plus `adaos autostart enable` or
  `--install-service auto`, with autostart-managed runtime slots.
- **Colab/lab**: repository bootstrap in a notebook, usually as a temporary
  member node with `--no-core-update`.

See [Deployment](docs/deployment.md) for production, development, and Colab
commands.

## Browser and member connection

Open the public client:

```text
https://inimatic.com/?intent=auth.login&zone=ru
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

Core version is stored in `pyproject.toml`. On `rev2026`, `AdaOS CI` runs the
complete SDK suite in balanced parallel shards plus a parallel skill-test job,
then bumps the patch version. A sequential full-suite run remains available as
a nightly or manually requested control run.

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

English documentation is authoritative. Maintained translations cover a
small, stable public-facing subset; detailed architecture, roadmaps, evidence,
CLI, and SDK documentation remain English-only and are linked directly from
translated navigation. See the
[Documentation Language and Translation Policy](docs/documentation-language-policy.md).

## License

MIT. See [LICENSE](LICENSE).
