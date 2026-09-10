# Builder Legacy Forensic Snapshot

Status: frozen pre-decontamination characterization evidence.

Captured: 2026-09-10.

Machine-readable record:
[`legacy_recipe_guided_20260910.json`](../../e2e/builder/forensics/legacy_recipe_guided_20260910.json).

## Scope And Claim

This snapshot freezes the Builder path immediately before domain
decontamination. It is labelled `legacy_recipe_guided`. It protects existing
behavior and supports migration review, but it is not evidence of generic
prompt autonomy.

The snapshot records the AdaOS and Client revisions independently because the
Client worktree is ahead of the submodule commit recorded by AdaOS. Every
selected Core, DEV Builder, Client, Project, and Scenario surface has a
content or tree digest. Raw prompts, model responses, credentials, and user
conversation content are deliberately excluded.

Reproduce the report from the same source state with:

```powershell
.\.venv\Scripts\python.exe scripts/capture_builder_forensic_snapshot.py `
  --captured-at 2026-09-10T00:00:00Z
```

## Findings

- `recipe.application_manager` serializes to 53,983 bytes. It is much larger
  than every other recipe combined and contains finished Applications
  information architecture, MCP operations, fixtures, localization,
  lifecycle defaults, implementation phases, and postconditions.
- The generic capability service contains Applications qualification,
  selection, and evaluation branches. The generic prompt capsule catalog also
  contains Application and Research domain policy.
- The DEV Builder source contains Applications selection and hundreds of
  shopping-list, recipe-book, and todo example references. Those examples can
  influence ordinary generation and therefore cannot remain generic context.
- Client generic runtime/renderer code contains Marketplace-specific action
  and collection-grid branches. These are Client ownership leaks even when
  they do not affect the current semantic adapter.
- Five Applications-prefixed DEV Projects and five matching Scenarios are
  present. Their exact trees, revision counts, job status counts, repair
  counts, provider/model labels, and canonical revision usage are retained in
  the JSON report.

Across those retained Scenarios, 25 canonical `llm.telemetry.usage` records
account for 420,485 input tokens, 50,048 cached input tokens, and 60,233 output
tokens. Sixty LLM job results and 48 repair attempts are present. These totals
describe retained records, not one statistically controlled cohort, and must
not be used as a clean baseline.

The 19 retained upstream processing observations have a median of 557 ms and
a maximum of 1,396 ms. They measure only the provider field exposed in those
revisions. They do not explain end-to-end multi-minute waits; queueing, relay,
polling, context compilation, validation, persistence, and runtime refresh
were not uniformly timed and remain an R0 instrumentation gap.

## Ownership And Disposition

| Current source | Current owner | Disposition |
| --- | --- | --- |
| `recipe.application_manager` and Applications postconditions | Generic Core capability catalog/evaluator | Move to an explicit versioned Applications compatibility pack |
| `adaos.application.lifecycle.v1` | Generic Builder prompt capsule catalog | Move to an Applications policy pack selected only by declared context |
| Research policy capsules | Generic Builder prompt capsule catalog | Move to an explicit Research pack |
| shopping-list, recipe-book, and todo examples/heuristics | DEV Builder generic path | Keep only in visible development/evaluation fixtures |
| Marketplace action and collection renderer branches | Client generic runtime | Replace with typed generic operations or an explicit product adapter |
| Provider calls, writes, validation, and workflow transitions | DEV Builder handler plus Core services | Move behind public SDK/Core ownership in R3 |

Atomic widget contracts and generic composition patterns such as split,
master-detail, board, form, and dashboard remain eligible for the generic
catalog after a vocabulary and example review. A reusable shape does not
justify retaining a finished product recipe.

## Evidence Labels

The existing Applications dogfood evidence is now explicitly
`recipe-guided` and `renderer-qualified`. Applications-prefixed experiments
remain recipe-guided unless an individual immutable run proves a different
input-attribution set. Terms such as `clean` or `minimum` in an artifact ID do
not change that status.

Future status projection must preserve these separate claims:

- `renderer-qualified`: the Client rendered and operated the declared UI;
- `recipe-guided`: subject-specific recipe or evaluator input was available;
- `brief-compiled`: a typed intent/brief compiler produced the candidate;
- `prompt-autonomous`: a sealed case passed with a clean generic attribution
  receipt.

## Remaining R0 Gaps

- Add uniform stage spans from request admission through Client readiness and
  browser render.
- Capture compact and wide Client traces under an immutable build digest.
- Complete dynamic reachability tests so the static inventory can be called
  exhaustive rather than bounded to selected ownership surfaces.
- Preserve an executable legacy characterization suite before deleting any
  compatibility implementation.
