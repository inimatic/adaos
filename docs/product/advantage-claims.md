# AdaOS Advantage Claims And Evidence

Status: product positioning and evidence register. This page defines how AdaOS
advantage claims may be stated, scoped, tested, and promoted. It is not a
market availability claim for any named solution direction.

Last reviewed: 2026-08-11.

Read this page with the [AdaOS Product Model](index.md), the
[Solution Directions](solution-directions.md), and the
[Governed Evolution Roadmap](../architecture/governed-evolution-roadmap.md).
The product model defines maturity vocabulary; the governed-evolution roadmap
defines evidence levels and proof gates.

## Purpose

AdaOS should make advantage claims only when they are falsifiable. The useful
claim shape is not "AdaOS is better than every alternative", but:

```text
For this solution class and scenario,
AdaOS provides this property,
under these scope limits,
with this evidence and residual risk.
```

This keeps product language aligned with implementation maturity. It also gives
future work a stable place to attach measurements, pilot results, and
counter-evidence without rewriting the product story from scratch.

## Claim Rules

1. A claim must identify the compared solution class, not a vague market.
2. A claim must name the user-visible property being compared.
3. A claim must include a proof method before it is used as an external
   differentiator.
4. A claim must carry an evidence level and a maturity label.
5. A claim must stay scoped to the environment where it was proven.
6. A failed or weaker result updates the claim instead of being hidden.
7. Strategic and long-term directions may define testable hypotheses, but they
   must not be presented as implemented advantages.

## Evidence Levels

Use the governed-evolution maturity vocabulary where possible:

| Evidence level | Meaning |
| --- | --- |
| `hypothesis` | A plausible advantage has been identified, but proof is not specified. |
| `specified` | A repeatable proof method, fixture, or pilot boundary exists. |
| `validated-local` | The claim passed on a development machine or controlled local environment. |
| `validated-stand` | The claim passed on a representative stand or independent deployment. |
| `production-accepted` | The claim is backed by operational evidence and an explicit acceptance decision. |

Product maturity remains separate. For example, a shared runtime primitive may
be `Implemented` while AdaOS Campus remains a `Strategic direction`.

## Claim Register

| Claim | Compared solution class | Proof method | Current evidence level | External wording boundary |
| --- | --- | --- | --- | --- |
| AdaOS can make assistant capabilities governable and reversible through install, validation, activation, observation, and rollback rails. | Ad-hoc agent stacks, scripts, and no-code automation flows without a durable runtime lifecycle. | Run a representative skill and scenario through install, validate, prepare, test, activate, observe, failure, policy denial, and rollback; record revision, commands, logs, and residual risk. | `validated-stand` for a bounded artifact slice; broad rollout acceptance remains open. | Say "governed and reversible capability lifecycle" only for the proven slice and linked evidence. |
| AdaOS can keep a managed assistant environment local-first instead of making a remote service the sole authority for identity, policy, and long-lived state. | Cloud-first assistant products and SaaS automation systems. | Start a hub, attach browser/member or endpoint, execute a bounded scenario, inspect where state and authority live, and record required external services. | `specified`; individual runtime primitives are implemented, but the full comparative claim needs repeated deployment evidence. | Say "local-first runtime foundation"; avoid claiming complete offline operation or universal cloud independence. |
| AdaOS can separate people, roles, memberships, private scopes, shared scopes, devices, skills, and scenarios below the UI. | Shared chat assistants and workspace tools where access boundaries are mostly product-level UI conventions. | Execute role and consent matrices for owner, member, guest, student, instructor, device, and agent actors; prove allowed, denied, and audited paths. | `specified`; shared identity and access primitives exist, but solution-level matrices are not yet complete. | Say "explicit access and scope model"; avoid claiming broad compliance or privacy guarantees without a specific matrix and evidence. |
| AdaOS can reuse one runtime foundation across Home, Campus, Enterprise, Research, and endpoint directions without creating separate platform kernels. | Vertical products that fork runtime, identity, lifecycle, or policy per domain. | Run or simulate the same package lifecycle, policy boundary, and skill/scenario contracts under at least two deployment profiles with different configuration. | `hypothesis` to `specified`; the invariant is normative, but cross-profile proof is still emerging. | Say "one shared runtime foundation"; avoid claiming all named directions are product-ready. |
| AdaOS can treat physical devices and software endpoints as revocable participants rather than fixed-purpose app accessories. | Closed device ecosystems and single-purpose endpoint applications. | Pair an endpoint, assign capabilities, route events or commands, observe health, revoke access, and reuse the endpoint in another domain profile. | `specified`; reDevice has `Implemented` product maturity, but comparative evidence should remain attached to concrete endpoint runs. | Say "reusable endpoint family"; avoid claiming universal hardware compatibility. |
| AdaOS can make research assistance more inspectable and reproducible by recording protocols, artifacts, metrics, provider identities, and evidence bundles. | Chat-only research assistants, notebook-only workflows, and provider dashboards used as the sole evidence record. | Recompute a declared analysis from an evidence bundle on a clean environment and distinguish exploratory findings from confirmatory claims. | `specified`; Research Fabric is target architecture and aResearcher is strategic. | Say "research evidence architecture"; avoid claiming scientific validity or autonomous research authority. |
| AdaOS can keep inactive or on-demand capabilities cheaper than monolithic always-on assistant runtimes. | Monolithic runtimes and broad snapshot architectures. | Measure startup time, memory, CPU, background I/O, and refresh work with inactive lazy skills, then repeat under browser demand and activation. | `specified`; MVP M3 owns the activation runtime proof. | Say "designed for demand-aware activation"; avoid claiming performance superiority until benchmark evidence exists. |
| AdaOS can preserve operator truth when browser UI, normal skill traffic, or an operational skill is noisy, blocked, or quarantined. | Systems where status is owned by the same plugin or UI path that may be failing. | Induce skill noise, API restart, browser reconnect, operation retry, or quarantine; verify core-owned runtime, update, slot, route, Yjs, member, guard, and operation status remains readable. | `specified`; MVP M2 evidence is in progress. | Say "core-owned operator truth plane" only for proven status surfaces. |

## Positioning Language

Preferred short claims:

- AdaOS makes assistant capabilities governable and reversible.
- AdaOS is local-first by architecture, not only by UI preference.
- AdaOS separates roles, consent, private scopes, shared scopes, devices, and
  automation below the application layer.
- AdaOS keeps Home, Campus, Enterprise, Research, and endpoint work on one
  shared runtime foundation.
- AdaOS turns devices and endpoints into revocable participants in an assistant
  environment.

Avoid unsupported claims:

- "AdaOS is enterprise-ready."
- "AdaOS replaces LMS, Home Assistant, Zapier, or every assistant product."
- "AdaOS is the most private assistant platform."
- "AdaOS validates science."
- "AdaOS autonomously repairs itself in production."
- "AdaOS works offline for every scenario."
- "AdaOS supports all hardware."

## Claim Record Template

Use this shape when adding or promoting a claim:

```yaml
id: ADV-000
claim: ""
compared_solution_class: ""
scenario_scope: ""
user_visible_property: ""
proof_method: ""
evidence_level: hypothesis
product_maturity: Strategic direction
evidence_links: []
last_verified: null
revision: null
residual_risks: []
external_wording: ""
disallowed_wording: []
```

Evidence links should point to durable tests, release evidence, stand reports,
incident records, pilot notes, or acceptance decisions. A screenshot or plan may
support the narrative, but it is not enough to promote a claim by itself.

## Review Cadence

Review this register when:

- an MVP or governed-evolution proof gate changes maturity;
- a product direction receives new end-to-end solution evidence;
- a pilot produces counter-evidence or narrows the useful scope;
- external messaging, a demo, or a proposal wants to use one of these claims.

The review should update evidence level, scope, residual risk, and permitted
external wording together.
