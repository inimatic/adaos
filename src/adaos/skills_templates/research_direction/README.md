# New Research Direction

This Builder template represents one research direction as one AdaOS skill.
The package owns direction-local validation hooks, domain code, experimental
entrypoints, and primary data. It does not own research governance and it does
not create a direction-specific scenario.

## Conceptual Phase A workflow

1. Attach notebooks, prose, code, or papers to the Builder project.
2. Discuss the direction with `research_orchestrator_skill`.
3. Let the shared Research Fabric LLM produce a `ResearchSynthesisRevision`
   from explicit sources, a bounded scoping review, and recorded author
   decisions.
4. Inspect and accept the exact synthesis to create
   `AcceptedResearchSynthesis`.
5. Project the accepted synthesis into a read-only `DraftCandidate`.
6. Freeze Gate A1 as `accepted_for_comparison`.

Authoring and adversarial review are separate LLM jobs. Their provider job ids,
digests, and token usage are retained in receipts; failures retain usage when
the provider reports it. Recovery of the same response is deduplicated by job
id. Builder Codex usage, when separately authorized, has its own accounting
scope, and the interactive Codex session is not charged to either scope.

Phase A stops there. It does not create `ResearchRelease`, it does not authorize
Phase B, and it does not invent experiments, datasets, metrics, trials, token
rules, balances, transfers, payouts, or ownership semantics.

## Empirical runner workflow

1. Inspect and accept an exact `ResearchPrototype` revision.
2. Export the digest-bound `AutomationBrief`.
3. Only then start Builder Automation/Codex to replace the explicit
   `pre_codex` placeholders with the experimental implementation and tests.

Notebook outputs are source material, never accepted evidence. Do not mark
`implementation.state: ready` until the runner contract and its conformance
tests pass.

The template declares two provider surfaces. `adaos.research.synthesis.v1`
admits conceptual `ResearchSynthesisRevision` candidates without accepting or
releasing them. `adaos.research.runner.v1` exposes `prepare_attempt`,
`collect_attempt`, `verify_artifact`, and `dataset_status` for later empirical
work; those handlers deliberately fail closed before Codex.
