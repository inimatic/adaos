# Versioning And Health

Status: public current-state operations guide.

Core version information is stored in `pyproject.toml`. The `rev2026` branch is
the active rollout branch for this documentation track.

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

