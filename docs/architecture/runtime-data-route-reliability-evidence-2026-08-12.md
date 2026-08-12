# Runtime Data Route Reliability: 2026-08-12 Evidence

Status: accepted locally on the reference Windows member.

This receipt records the proof for the runtime data-route hardening exercised by
Research Workbench. It is evidence for the bounded `tool/details` path, not a
claim that every AdaOS scenario, transport, or autonomous-research stage is
complete.

## Failure and root cause

The observed symptom was a slow Workbench first paint followed by a false
`No research directions yet` state. The research data was still durable and
could be read directly from the installed orchestrator. The failed path was the
shared runtime contract:

1. browser tool reads and mutations both used HTTP POST and were therefore
   lifecycle-gated as mutations;
2. a lifecycle rejection entered generic timer retries, multiplying requests
   while the authoritative capability had not changed;
3. after retries were exhausted, collection rendering treated transport
   failure as a valid empty domain result;
4. the scenario declared cache and invalidation intent, but the browser did not
   execute all of the declared last-value, rate, and identity semantics.

The Workbench did not introduce a private transport. It exposed gaps in the
core path that affected any declarative skill-backed data source. The repair is
therefore in the client, API/member execution contract, lifecycle service, and
scenario validator; the Workbench supplies a strict reference declaration.

## Accepted implementation boundary

- A validated skill data source sends `intent: read` while generic
  `callSkill` actions remain mutation-safe by default.
- The execution node resolves the active skill manifest and verifies that the
  addressed tool is read-only. A browser hint alone never grants read
  treatment.
- HTTP forwarding and hub/member RPC preserve the intent; the target member
  repeats manifest verification.
- Reads use `live_reads`; mutations use `accept_mutations`. Advertised
  capability is evaluated independently of a coarse aggregate lifecycle label.
- Lifecycle suspension waits for a newer authoritative event and produces no
  HTTP polling. Ordinary transient transport errors retain bounded retry.
- Semantic cache identity includes route, normalized arguments, webspace, and
  relevant state. Targeted invalidation and explicit retry do not refresh
  unrelated sources.
- `maxRequestHz` and `preserveLastValue` are executable. UI state distinguishes
  `loading`, `refreshing`, `ready`, `stale`, `unavailable`, and `error` from a
  valid empty result.
- Strict scenario validation compares exact invalidation tags, last-value
  policy, and request rate with the owning skill manifest.
- Client location is injectable at the system boundary. Test suites no longer
  mutate shared browser history until Chrome throttles `replaceState`; the
  production default still reads the real window location.

## Published reference composition

| Component | Accepted identity |
| --- | --- |
| Core branch | `rev2026`, commits `b621cb26` and `bb5c588a` plus this receipt/pointer commit |
| AdaOS client | `0.0.325`, `0a6c3c916411404db032aaa01dadc6014f49c3c2` |
| `research_orchestrator_skill` | `0.7.0`, registry commit `d604243b855bd48d926205108fd568001b99eaf0`, active slot B |
| `research_workbench` | `0.0.8`, registry commit `9605e554e20518b5e0c8481a05301baafecf30d9` |

The orchestrator migrated its durable owner-scoped data from runtime bucket
`v0.6` to `v0.7`, retained the previous runtime for rollback, completed
`ensure_schema` and `rehydrate`, and reports a healthy SQLite relational
binding. Both released workspace objects are clean relative to registry main.

## Machine verification

Verification ran on 2026-08-12 in `D:\git\inimatic\adaos`.

| Check | Result |
| --- | --- |
| Full Angular client suite | 1,020 passed |
| Core manifest, validation, bridge, lifecycle, API lifecycle, and member-routing suite | 174 passed |
| `adaos skill test research_orchestrator_skill --json` | `pytest.status=passed` |
| `adaos skill validate research_orchestrator_skill --json` | `ok=true`, zero issues |
| `adaos scenario validate research_workbench --json` | `ok=true`, zero errors/issues |
| Installed orchestrator lifecycle | `0.7.0`, active/ready, schema and rehydrate healthy |
| Installed scenario status | `0.0.8`, clean, no registry drift |
| Live `list_directions` API read | HTTP success, two durable directions, 294 ms |
| Live read intent against `create_direction` | HTTP 409 `tool_intent_mismatch`, no mutation |

The live portfolio returned `tlp_research_03` in `formulation` and
`tlp_research_direction` in `handoff_ready`. This directly disproves the prior
empty-domain presentation on the same machine.

The browser suite includes an idle-soak contract: after first paint a stable
zero-TTL semantic source advances through 60 seconds with zero additional tool
calls; an unrelated invalidation also produces zero calls; a matching targeted
invalidation produces one coalesced refresh. Fault tests cover lifecycle wait
and release, bounded transient retry, last-value preservation, unavailable vs
empty presentation, rate limiting, identity-scoped retry, and routed intent
re-verification.

## Residual risks and non-claims

- This is local member evidence. Deployed hubs and browsers must consume the
  referenced core/client releases before the fix is operational there.
- The standard API restart command launched a healthy replacement process, but
  its invoking command exceeded its readiness timeout. That CLI supervision
  issue is separate from the data route and remains visible rather than being
  treated as a green restart assertion.
- The catalog audit still contains two pre-existing scenarios whose dependency
  manifests omit referenced tools. They are not caused by this change and were
  not silently relaxed by the validator.
- Advisory legacy scenarios may still report route-policy warnings. New or
  migrated scenarios should opt into strict enforcement.
- The proof covers bounded causal snapshots. Collaborative state still belongs
  in Yjs; ordered or high-rate observations still belong in streams.

## Reproduction commands

```text
python -m pytest -q tests/test_manifest_abi.py tests/test_skill_validation.py tests/test_scenario_validation.py tests/test_tool_bridge.py tests/test_runtime_lifecycle.py tests/test_api_runtime_lifecycle.py tests/test_subnet_link_client.py
python -m adaos skill test research_orchestrator_skill --json
python -m adaos skill validate research_orchestrator_skill --json
python -m adaos scenario validate research_workbench --json
python -m adaos skill status research_orchestrator_skill --json
python -m adaos scenario status research_workbench --json
```

The live API checks use authenticated `POST /api/tools/call` requests. One
addresses `research_orchestrator_skill:list_directions` with `intent=read`; the
negative control addresses `create_direction` with the same intent and must
return `tool_intent_mismatch` before handler execution.
