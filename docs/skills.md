# Skills

## What a skill is

In the current AdaOS runtime, a skill is a managed unit that can be scaffolded, validated, installed, updated, activated, and in some cases supervised as a long-running service.

## Common commands

```bash
adaos skill list
adaos skill create my_skill
adaos skill validate my_skill
adaos skill install my_skill
adaos skill migrate
adaos skill activate my_skill
adaos skill rollback my_skill
```

## Runtime-oriented commands

```bash
adaos skill run my_skill --topic some.topic --payload '{}'
adaos skill setup my_skill
adaos skill status
adaos skill doctor my_skill
adaos skill gc
```

For runtime bucket layout, rollback semantics, and the reserved `migrations/data_migration.py` flow, see [Skill Runtime Lifecycle](skill_runtime.md).

`adaos skill list --local` and `adaos skill status <name>` include source
state markers for workspace skills:

- `dirty`: the skill source has uncommitted filesystem changes.
- `ahead`: commits touching the skill exist locally but are not in the
  workspace registry/upstream base yet.
- `behind`: the workspace registry/upstream base has newer commits touching
  the skill.
- `diff`: the source differs from the base, but Git could not classify the
  path-level divergence as ahead or behind.
- `git-error`: the CLI could not compute the Git comparison, usually because
  the base ref is not fetched or the workspace is not a Git repository.
- `version-drift`: the workspace skill version and active runtime slot version
  differ. This is a runtime/install/activation signal, not a Git dirty signal.

Use `adaos skill status <name> --fetch --diff` before publishing when you need
the exact comparison against the registry base.

For browser-facing or LLM-authored skills, follow
[LLM-Safe Skill Development Guide](guides/llm-skill-development.md). That guide
defines the current Yjs, stream, projection, details, and guard/quarantine
contracts for skills.

## Service-type skills

Some skills are exposed through the service supervisor:

```bash
adaos skill service list
adaos skill service status <name>
adaos skill service restart <name>
```

This path is backed by `/api/services/*`.

## Publishing split

The repository now distinguishes between:

- workspace git push commands such as `adaos skill push --message ...`
- Root-backed developer publishing through `adaos dev skill ...`

That split is important when reading older documentation.
