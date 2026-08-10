# New Research Direction

This Builder template represents one research direction as one AdaOS skill.
The package owns domain code, experimental entrypoints, and primary data. It
does not own research governance and it does not create a direction-specific
scenario.

## Pre-Codex workflow

1. Attach notebooks, prose, code, or papers to the Builder project.
2. Discuss the direction with `research_orchestrator_skill`.
3. Inspect and accept an exact `ResearchPrototype` revision.
4. Export the digest-bound `AutomationBrief`.
5. Only then start Builder Automation/Codex to replace the explicit
   `pre_codex` placeholders with the experimental implementation and tests.

Notebook outputs are source material, never accepted evidence. Do not mark
`implementation.state: ready` until the runner contract and its conformance
tests pass.
