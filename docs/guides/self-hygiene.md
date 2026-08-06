# AdaOS Self-Hygiene

AdaOS keeps a small retention policy in the core so installed nodes do not depend
on a one-off administrator cleanup checklist.

The policy is intentionally conservative:

- AdaOS-owned runtime directories are cleaned directly.
- OS-level retention is installed only when the platform supports it.
- External backup roots are never deleted unless they are explicitly marked as
  AdaOS-managed.
- Core update uses hygiene as a short preflight and post-prepare step, not as a
  long-running cleanup phase.

## CLI

```bash
adaos maintenance status --json
adaos maintenance apply-retention --json
adaos maintenance apply-retention --dry-run
adaos maintenance run --dry-run --json
adaos maintenance run --pressure-only --json
adaos maintenance artifact-retention --json
adaos maintenance artifact-retention --apply --json
```

`artifact-retention` governs the package activation store separately from OS
and log hygiene. It is dry-run by default. The explicit `--apply` mode can
remove only expired unreferenced packages, expired terminal records, and
staging/backup directories proven orphaned. Active WorkspaceLock content,
retained lock histories, pending observations, nonterminal work, and uncertain
recovery records are always protected.

`apply-retention` records the active policy under:

```text
<ADAOS_BASE_DIR>/state/self_hygiene/retention-policy.json
```

On Linux, when the command runs as root, it also installs managed OS policy
files:

```text
/etc/systemd/journald.conf.d/adaos-retention.conf
/etc/tmpfiles.d/adaos.conf
/etc/logrotate.d/adaos
/etc/systemd/system/adaos-hygiene.service
/etc/systemd/system/adaos-hygiene.timer
/etc/systemd/system/adaos-hygiene-full.service
/etc/systemd/system/adaos-hygiene-full.timer
```

The timer invokes the managed `/usr/local/bin/adaos` CLI shim, which resolves
the current verified A/B slot before importing AdaOS. It does not pin the raw
Python executable that happened to install the timer; hygiene therefore remains
available when the stable root checkout is temporarily unimportable.

The daily timer runs pressure-only cleanup. A separate weekly full timer purges
pip/uv caches and old safe tmp patterns even before disk pressure is reached.

On Windows, the command does not try to write Linux-specific policy files. It
records the policy state and leaves local cleanup available through
`adaos maintenance run`.

## Default Policy

- Warn when `/` is above `85%` used or has less than `2 GiB` free.
- Treat the disk as under pressure when `/` is above `92%` used or has less than
  `1.5 GiB` free.
- Keep journald to `512M`, with `2G` reserved free space and `7day` retention.
- Rotate AdaOS `*.log` and `*.jsonl` files at `100M`, keeping `7` compressed
  rotations.
- Run full cache hygiene weekly on Sunday at `05:20 UTC` with a `30min`
  randomized delay.
- Clean AdaOS tmp files and selected pip/AdaOS leftovers after `3d` through
  tmpfiles. The selected `/tmp` patterns include `pip-unpack-*`,
  `pip-install-*`, `pip-metadata-*`, `pip-ephem-wheel-cache-*`,
  `pip-build-tracker-*`, `pip-target-*`, `pip-build-env-*`,
  `pip-modern-metadata-*`, `adaos-rasa-nlu-*`, `model*.crfsuite`, and
  `tmp*.wav`.
- During pressure cleanup, remove old AdaOS tmp entries and selected old
  top-level `/tmp` leftovers. Large `/tmp/tmp*` files are considered only when
  they are old and at least `100 MiB`.

## Install And Core Update

`adaos install` runs `apply_retention_policy` by default. Use
`--no-retention-policy` to skip it:

```bash
adaos install --no-retention-policy
```

Core update calls self-hygiene in two places:

- `core_update.preflight`: pressure-only, before the expensive slot preparation.
- `core_update.post_prepare`: short cleanup after a slot is prepared.

Both calls skip pip/uv cache purge so core update does not stall on a large
cache. Set `ADAOS_CORE_UPDATE_HYGIENE=0` to disable these hooks.

## Managed Backups

AdaOS does not delete arbitrary `/opt/bak`, `/home/bak`, or other external
backup roots by default. A backup root must be passed through
`ADAOS_HYGIENE_BACKUP_ROOTS` and contain one of these markers:

```text
.adaos-managed-backup
.adaos-retention.json
```

Unmarked roots are reported as skipped with
`missing_adaos_managed_backup_marker`.
