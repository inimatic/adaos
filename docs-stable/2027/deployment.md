# Deployment

Status: public current-state deployment guide.

AdaOS supports development, production-style local runtime slots, and temporary
lab/member-node setups.

## Modes

| Mode | Use when | Entry point |
| --- | --- | --- |
| Development | You are editing or testing AdaOS locally. | `tools/bootstrap.* --dev`, then `adaos dev serve` |
| Production-style local node | You want an autostart-managed node. | `adaos autostart enable` or bootstrap `--install-service auto` |
| Lab or Colab member | You need a temporary joined node. | bootstrap with `--join-code` and `--no-core-update` when appropriate |

## Runtime Slots

Production commands may require the active runtime slot shell:

```bash
source tools/slot-shell.sh --cd
```

PowerShell:

```powershell
. .\tools\slot-shell.ps1 -Cd
```

## Public Scope

This page intentionally avoids internal rollout plans and hosting architecture.
It documents the public operator surface that can be run from this repository.

