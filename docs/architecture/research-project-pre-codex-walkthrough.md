# Research Project Pre-Codex Walkthrough

Status: `validated-local` on the reference Windows member, 2026-08-11.

This receipt closes ARF7.1: a shared Research Workbench can own a portfolio of
research Projects, attach direction-owned source artifacts, accept one typed
research formulation, and hand an exact least-write task to Builder. It does
not claim that Codex ran, an experiment ran, TLP is superior, or autonomous
research is complete.

## Reference composition

| Object | Exact identity |
| --- | --- |
| Project | `project:tlp_research_direction`, manifest `sha256:9972b43cc8a479360d4f41eecc9528676da2cb0c5d5496e3ff98343bece3b1f7` |
| Primary/write target | `skill:tlp_research_direction` |
| Presentation | `scenario:research_workbench`, binding `direction_ref=skill:tlp_research_direction` |
| Artifact group | `artifact://skill/tlp_research_direction/part0`, manifest `sha256:715091ac119c29a3b9d823b9c68c610bf7bc0b4b86201e3da97fdd4bd07fa73e` |
| SourceBundle | `sha256:637e32518f33ba75dd231acd794ff126bb39edda1f3034cf9410f8d17b6b83cf` |
| ResearchPrototype | `sha256:ad3a9f531edfe13143efc9626c959732dcda56b690f0a305778c621f9bbd7778` |
| AutomationBrief | `sha256:139cb04deda7569777c9ebac603fc4838fdeec6bacbd8cb1e6344452594c513b` |
| Development Session | `dev_tlp_research_direction_139cb04deda75697`, `status=ready` |
| Local code checkpoint | source tree `sha256:e5980181dbe9ac956274c325271c4bd11282ce9b008888a17fcbf0a585439158`; package `sha256:e8f6d91c2dfc11fda26e1b0cb685622cd49c845d8a77ec7cd61c1cad53c64258`; `bytes_uploaded=0` |

The artifact group contains the original `TropicalMaxPoo1.ipynb` (3,442,233
bytes, digest
`sha256:7c6c9aa6fe3335f5e2729df183f7bf91a4f9167c8c0f4a77c84847a0aa2299ab`)
and `initial-review.md` (35,660 bytes, digest
`sha256:1450397b37a754d87575ed7c67b6c64ed1ead40166e5cdd31e3080f91139c02e`).
Notebook extraction found 63 cells, 47 code cells, 16 Markdown cells, and
5,106 historical output records. Those outputs remain untrusted source
material and are not evidence.

## Accepted scientific and engineering boundary

The ResearchPrototype separates two stages:

1. `cpu-smoke`: three epochs, one paired seed, current CPU/member node,
   `evidence_class=workflow_smoke`, `inference_allowed=false`;
2. `locked-series`: a later separately admitted paired scientific series with
   its seed count, execution profile, estimand, uncertainty, and stopping rules
   locked before execution.

The primary future estimand is the mean paired validation-accuracy difference,
TLP minus MaxPool. The task also names shift sensitivity and learned spatial
selectivity as secondary/diagnostic outcomes. Negative, negligible, and
inconclusive results are valid outcomes. Historical notebook outputs cannot be
used as confirmatory trials.

The Development Session has exactly one read-write target:
`skill:tlp_research_direction`. `scenario:research_workbench`,
`skill:research_orchestrator_skill`, and the artifact group are read-only.
Builder's live scope review admitted the target handler, rejected the notebook
as `read_only_artifact_input`, and rejected an AdaOS repository file as
`outside_development_session_scope`. A scope-expansion tool only records an
unapproved request; it never grants access.

## Private checkpoint and runtime transition

The first acceptance attempt demonstrated that pushing the 3.4 MB private
direction tree to Forge was both unnecessary and vulnerable to transport size
limits. The accepted design hashes only direction code/config into local CTX
Builder state. Artifact groups are bound separately by manifest digest and
native read-only path. Publication is postponed until after Codex
implementation and review.

DEV and workspace skill data remain isolated by design. After publication,
the workspace orchestrator did not copy the DEV relational database. The same
accepted structured prototype was instead replayed through the published
public tools against the already manifested Project/artifacts. This produced
the workspace ResearchPrototype, AutomationBrief, and session identities in
the table above and verified that the release works without private DEV state.

## Projection and navigation evidence

Selecting the Project resolves its declared Workbench presentation, not a
scenario generated for the direction. `desktop-dev` was synchronously
rematerialized with both desired and observed scenario equal to
`research_workbench`; an unrelated prior scenario was not retained.

Builder opens at:

```text
https://inimatic.com/?intent=webspace.open&zone=ru&subnet_id=sn_6acf0c01&webspace_id=desktop-dev&space_kind=workspace&expected_scenario_id=builder&builder_object_type=skill&builder_object_id=tlp_research_direction&builder_object_ref=skill%3Atlp_research_direction&builder_object_title=TLP+paired+experiment+research+direction
```

The `builder_object_*` fields are a one-shot first-paint address. They prevent
an old Builder selection from flashing or remaining selected while the durable
Yjs projection catches up; the Development Session binding remains canonical.

Builder's return link, preview, and QR derive from the same Navigation SDK
destination:

```text
https://inimatic.com/?intent=webspace.open&zone=ru&subnet_id=sn_6acf0c01&webspace_id=desktop-dev&space_kind=development&expected_scenario_id=research_workbench
```

The workspace Desktop catalogue contains `research_workbench`; directions are
portfolio rows/focus bindings, not additional Desktop applications.

## Published framework releases

| Component | Release | Registry commit |
| --- | --- | --- |
| `research_orchestrator_skill` | `0.4.0` | `6076b07d871ea27231d59dfbc2fb0378eb10debb` |
| `builder_sdk_control_skill` | `0.1.59` | `c0a79590be010f3be2213869bf140f00b68664db` |
| `research_workbench` | `0.0.5` | `97afd9e08cdd0b5d5c5f975d404a3bec70a4b6d8` |
| `skill_preview` | `0.0.1` | `25b7324eacdf4c6573b030d198fdfc0f3cd01548` |
| `builder` | `0.2.61` | `1813fbdb774b2b28c59b19dbf8abd22082458f3e` |
| AdaOS client | `0.0.311` | `7ae0fd5c63f1f36655be46308af74a2c4637b420` |

Both skills were activated from workspace releases. The Desktop projection was
rebuilt and all three desktop scenarios were discoverable from workspace.

## Verification commands

The proof used ordinary AdaOS lifecycle and runtime commands, including:

```text
adaos dev skill test research_orchestrator_skill --json
adaos dev skill validate research_orchestrator_skill --probe-tools --json
adaos dev skill validate builder_sdk_control_skill --probe-tools --json
adaos dev scenario validate research_workbench --json
adaos dev scenario validate skill_preview --json
adaos dev scenario validate builder --json
adaos dev skill publish <skill> --bump patch --force
adaos dev scenario publish <scenario> --bump patch --force
adaos skill activate research_orchestrator_skill --version 0.1.0
adaos skill activate builder_sdk_control_skill --version 0.1.59
adaos skill run research_orchestrator_skill list_directions --json-file <payload>
adaos skill run builder_sdk_control_skill review_development_changes --json-file <payload>
```

Targeted core tests passed for Project composition, rollback, artifact
isolation/traversal, private checkpointing, Development Session idempotency and
chronological selection, change-scope enforcement, scope-expansion requests,
presentation fallback, and canonical preview navigation. The orchestrator's
package tests also cover contract admission and data migration; durable state
is copied between versions while transient upload staging is excluded.

Builder validation retains its pre-existing non-addressable invalidation-tag
warnings. They did not invalidate the scenario and are not specific to the
research path.

## 2026-08-11 Workbench and handoff hardening receipt

The TLP walkthrough exposed several integration failures that are now covered
by framework contracts rather than direction-specific workarounds:

- artifact intake declares `local_write` in the skill manifest, so copying an
  uploaded file into the owning direction skill is not misclassified as an
  arbitrary filesystem operation;
- genuinely governed actions publish Pending Actions through the asynchronous
  live-room owner path; the former sync owner-handoff failure is removed;
- `tlp_research_03` successfully consumed the exact failed staging upload as
  `artifact-7c6c9aa6fe3335f5e272`, bound it to `part0`, and removed the obsolete
  staging copy after the manifested copy and digest were durable;
- Discussion renders the accepted/draft AutomationBrief as a compact consensus
  beside the chat, while direction status moved into a details modal;
- manifested Markdown and text artifacts use existing native renderers; PDF
  content is streamed through an authenticated artifact endpoint into the
  reusable `item.documentViewer`, without exposing a native host path;
- artifact media types are resolved deterministically for Markdown, text,
  notebooks, JSON/YAML, and PDF instead of depending on the Windows MIME
  registry;
- Builder applies declared URL state once before live state, then yields to the
  durable projection. A checked live handoff resolved `webspace_id=desktop-dev`,
  `builder_object_type=skill`, and
  `builder_object_id=tlp_research_direction`, with `codex_started=false`;
- the obsolete hard-coded port `8788` was removed from route-tunnel fallback.
  The observed 58-second 502 was a stale routing candidate, not a crash in the
  research skill. Runtime endpoints now come from CTX/topology state.

The installed `research_orchestrator_skill 0.4.0` passed its package tests and
activated in slot B. Workbench `0.0.5` validated cleanly and executed through
the ordinary scenario command. A local API restart imported the active skill,
and live calls verified `get_direction`, `get_consensus`, Markdown preview,
artifact streaming, and the addressed Builder handoff. The relevant isolated
AdaOS SDK suite passed 61 tests; the broader tool-bridge/routing/Builder suite
had already passed 135 tests, and the client production build plus 27 focused
browser tests passed.

## Why this is stronger than attaching the original Markdown to Codex

A direct Markdown prompt remains useful for low-friction exploration. The
structured handoff earns its overhead when implementation must be repeatable,
reviewable, resumable, or autonomous:

| Raw notebook/review plus Codex | Accepted research handoff |
| --- | --- |
| Attachments and edits may be implicit | Every file, group, bundle, code base, prototype, and brief has an exact digest |
| Scientific intent is inferred from prose | Hypotheses, falsifiers, stages, evidence classes, estimands, uncertainty, stopping and negative-result policy are typed |
| A smoke result can be mistaken for evidence | The CPU smoke explicitly forbids inference; confirmatory work is a separate locked stage |
| Codex infers what it may edit | One exact write target, read-only dependencies/artifacts, a machine-enforced path gate, and explicit prohibited actions |
| “Looks good” lives only in chat history | Acceptance binds one observed generation and immutable prototype revision |
| Restart requires reconstructing intent | Project, artifacts, activity, brief, checkpoint, and Development Session survive conversation boundaries |
| Success is mostly a plausible patch | Acceptance checks and later evidence refs make implementation fidelity and outcomes measurable |

The structure cannot make a weak hypothesis true and does not replace human
scientific judgment. Its advantage is that ambiguity becomes visible and
authority becomes enforceable. The original Markdown remains valuable as
source material; it is no longer forced to serve simultaneously as provenance,
protocol, implementation scope, consent record, and execution state.

## Next gate

ARF7.2 may now start Codex against the exact unopened Development Session. Its
first responsibility is to implement and test the TLP direction skill under
the accepted scope. Experiment execution, even the CPU smoke, remains a later
explicit action after implementation review.
