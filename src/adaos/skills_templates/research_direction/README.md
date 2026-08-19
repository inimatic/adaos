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

The template declares the universal `adaos.research.runner.v1` provider
surface (`prepare_attempt`, `collect_attempt`, `verify_artifact`, and
`dataset_status`) for every research direction. Its handlers deliberately fail
closed before Codex. This common scaffold removes AdaOS integration discovery
from comparative evaluations while leaving the scientific model, data path,
execution, evidence, and recovery implementation entirely task-specific.
