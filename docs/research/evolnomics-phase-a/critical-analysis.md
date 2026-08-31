# Evolnomics Phase A calibration analysis

Status: `revise`. This package contains Research Fabric calibration evidence,
not an accepted synthesis, Draft 0, Gate A1 freeze, ResearchRelease, economic
mechanism, or Builder handoff.

## Current candidate

The latest materialized candidate is `ResearchSynthesisRevision` 2 at digest
`sha256:219c763b3eb26a1ada9d685c2a4eaebe50bd5d365a8f6a503962d70755bc36c2`.
Its independent review digest is
`sha256:8a1d85a1110e20158da4fe625dc516e637a8107bda013c8d79b28a6e10860bda`.
The review verdict is `revise` with eight major findings.

The defensible position is narrower than the initial abstract. Artifacts,
artifact-mediated cognition, provenance, artifact-inclusive human-AI
co-creation, and human-AI coevolution all have admitted predecessors. The
present contribution is therefore, at most, a candidate synthesis connecting
governed behavior-defining artifact lineage, contribution observability, and a
non-operational research question about later resource feedback. The bounded
corpus does not prove that even this integration is novel.

The strongest current proposition is C17: explicit lineage and acceptance
annotations may improve visibility of non-code contributions relative to a
commit-only baseline. It now has variables, a baseline, a test method, and a
falsification condition, but still needs a preregistered visibility rubric and
effect-size decision rule. C18 remains compound and mechanism-adjacent: it
mixes resource-feedback exposure, identity assurance, and anti-gaming controls.
It should be split or retained only as a non-operational question.

The structured threat model now covers Sybil behavior, collusion, identity
borrowing, usage manipulation, strategic under-reporting, selective disclosure,
lineage tampering, idea squatting, persistent micro-entitlements, governance
capture, false authority, and reward hacking. Coverage is taxonomic rather than
validated; no mitigation or mechanism is authorized.

## Fabric findings

The Evolnomics run exposed and corrected these general Research Fabric defects:

1. Synchronous LLM calls lost long jobs. Authoring now uses Root async jobs.
2. Failed and incomplete jobs could lose provider usage. They now emit durable
   failure receipts; unavailable usage remains null rather than zero.
3. Recovery could double-count a provider response. Usage aggregation now
   deduplicates by `provider_job_id`.
4. The LLM output ABI was ambiguous. The prompt now defines exact component
   shapes, and Fabric owns source metadata and digest binding.
5. Omitted literature was either rejected or silently absent. It is now placed
   in an explicit `RW_UNMAPPED` cluster that proves visibility but not synthesis.
6. The synthesis contract lacked a threat model, nearest-neighbor deltas, and
   hypothesis operationalization. ABI 1.1 adds them.
7. A bounded corpus still permitted `apparently_new` claims. ABI 1.2 adds a
   preregistered novelty ceiling and nearest-neighbor minimum. The current case
   requires at least five deltas and permits only known-combination,
   known-but-extended, or unresolved novelty states.
8. A tracked conceptual case could remain invisible to Research Workbench.
   `workbench.json` now declares the canonical direction, task, artifact
   visibility, and lifecycle projection; `register_conceptual_case.py`
   reconciles that declaration through `research_orchestrator_skill` without
   starting Builder. Exact matches are skipped before mutation so repeated
   registration does not append misleading `source_reused` activity.

The remaining source-grounding weakness is not repaired in prose: several
records expose curated abstracts or summaries rather than exact passage-level
anchors. Draft 0 should remain blocked until the key claims have direct source
locators or admitted fragments.

## Token accounting

`token-ledger.json` aggregates unique Researcher provider jobs. The known lower
bound is 82,437 input, 54,211 output, and 136,648 total tokens; 5,376 input
tokens were cached and 9,624 output tokens were reported as reasoning details.
Reasoning and cached tokens are details of input/output and are not added again
to total. Two provider jobs have unavailable usage, so an exact grand total is
not claimed.

Builder was not required or invoked, so Builder Codex usage for this case is
exactly zero by lifecycle evidence, not by absence of a journal. The interactive
Codex session used to modify Research Fabric is explicitly excluded. For future
Builder runs, terminal Codex journals are now reported once to Root
`codex.api.tokens`; failed tasks with positive provider usage are counted.

## Required human decision

No acceptance should be recorded yet. A human author should decide whether the
paper claims only a scoped synthesis, whether K5 remains in the conceptual model,
and whether C18 is split or removed. After source anchors and those decisions are
admitted, the next candidate should use ABI 1.2 and receive a fresh independent
review before any DraftCandidate is projected.
