# CLI Guide

This page intentionally stays short.

The old CLI cheat sheet was outdated and contained legacy commands. Use the current references instead:

- [Skills](../skills.md)
- [Scenarios](../scenarios.md)
- [LLM-Safe Skill Development Guide](llm-skill-development.md)

Current notes:

- Prefer `adaos skill migrate` for installed skill refresh.
- `adaos skill sync` is legacy/deprecated.
- Use `adaos dev skill ...` and `adaos dev scenario ...` for developer authoring and publishing flows.
- Runtime/workspace sync in non-dev mode treats git as source of truth and may stash local workspace changes.
- Dev flows protect local edits and should not silently discard work.
