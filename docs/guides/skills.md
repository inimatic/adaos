# Skills Guide

This page intentionally stays short.

The old copy of this guide was outdated and contained legacy CLI advice. Use the current references instead:

- [Skills](../skills.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [LLM-Safe Skill Development Guide](llm-skill-development.md)
- [Web IO](../io/webio.md)
- [Semantic State Plane](../architecture/semantic-state-plane.md)

Key current rules:

- Use `adaos skill migrate` to refresh installed skills; `adaos skill sync` is legacy/deprecated.
- Use `adaos dev skill ...` for developer publishing flows.
- Browser-visible state should use declared `data_projections` and governed SDK helpers.
- High-churn or append-heavy data should use stream receivers.
- Do not write directly to the primary Yjs document from normal skill code.
