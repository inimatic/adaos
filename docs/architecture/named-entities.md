# Named Entities and Canonical Naming

## Goal

AdaOS needs a shared named-entity layer so humans, UI, skills, NLU, and future
LLM tooling can refer to the same runtime objects without retraining the intent
model every time a device, browser, webspace, skill, or scenario is renamed.

The target architecture is:

- NLU models understand intent shape and entity classes.
- Runtime resolvers map registered names and aliases to canonical refs.
- UI displays the best human-facing name but keeps fallback labels available.
- Skills and automation receive canonical ids, not ambiguous display strings.
- Localization selects which labels and aliases are shown or resolved first,
  but it never changes canonical identity.
- LLM-assisted authoring can inspect and update names through governed
  descriptors instead of inventing ad hoc labels.

## Why this exists

Device and node names are operational vocabulary. A user will naturally say:

- "open weather on the kitchen display"
- "show logs for ZVERZVE-A1BNQF7"
- "send this to my Edge browser"
- "switch the living room node to morning"

Those labels are not stable training data. They are local runtime facts.
If changing "Kitchen display" requires retraining Rasa or the neural detector,
the architecture is too brittle.

The model should therefore separate:

- intent detection: "open weather", "show logs", "switch scenario"
- entity resolution: "kitchen display" -> `device:member:<node_id>`
- action dispatch: target skill/tool receives canonical ids

## Core principle

Registered names are resolved before or alongside NLU, not learned as permanent
model behavior.

The NLU model may use masked text such as:

```text
open weather on {device}
```

The runtime keeps the original text and span metadata:

```json
{
  "text": "open weather on kitchen display",
  "normalized_text": "open weather on {device}",
  "resolved_entities": [
    {
      "kind": "device",
      "canonical_ref": "device:member:member-01",
      "span": { "start": 16, "end": 31 },
      "matched_text": "kitchen display",
      "matched_alias": "Kitchen display",
      "confidence": 1.0
    }
  ]
}
```

## Entity vocabulary

### Canonical ref

A `canonical_ref` is the stable domain identity used for dispatch, storage,
trace, and LLM tooling.
The ref vocabulary is owned by [UI Addressing](ui-addressing.md) and related
domain architecture.
The named-entity layer resolves labels into those refs; it must not create a
parallel addressing namespace.

Initial target refs:

- `device:member:<node_id>`
- `device:browser:<device_id>`
- `node:<node_id>` as a compatibility or system-model view when the object is
  specifically a node rather than a device access endpoint
- `webspace:<webspace_id>`
- `scenario:<scenario_id>`
- `skill:<skill_name>`
- `modal:<modal_id>`
- `app:<app_id>`
- `profile:<user_id>`

### Name classes

AdaOS should not store only one string called "name". Names have different
authority and lifecycle:

- `display_name`: user-confirmed human-facing name.
- `observed_name`: system-discovered value such as hostname, browser + OS, or
  integration username.
- `draft_name`: suggested name for a new entity before the user confirms it.
- `aliases`: additional phrases accepted by NLU and search.
- `fallback_label`: deterministic UI fallback such as `Node 0`.
- `labels`: localized or source-qualified human-facing strings derived from
  the fields above. Labels are read-model entries, not routing ids.

Display priority should be:

```text
display_name
  > primary registered name
  > observed_name
  > fallback_label
```

For nodes, `Node 0` and `Node N` are fallback labels only. If a hostname such
as `ZVERZVE-A1BNQF7` is available and no user name exists, it is a better
observed label than `Node 0`.

For browsers, the first draft name should be derived from browser family,
operating system, and form factor when available:

```text
Chrome on Windows
Edge on Windows
Safari on iPhone
Firefox on Linux
Chrome on Android Tablet
```

Current implementation captures browser metadata from the Yjs/browser session
handshake as `browser_family`, `os_name`, `form_factor`, and `user_agent`.
Those fields are stored as observed facts in `access_links`; they may produce a
`draft_name`, but they do not overwrite a user-confirmed `display_name`.

If multiple devices would receive the same draft name, the resolver may append
a stable suffix such as `#2` until the user confirms a better name.

## Entity record

The target read model should be compact and safe to project into Yjs or expose
through SDK/MCP descriptors:

```json
{
  "canonical_ref": "device:member:member-01",
  "kind": "device.member",
  "technical_ids": {
    "node_id": "member-01",
    "device_ref": "member:member-01"
  },
  "display_name": "Kitchen display",
  "observed_name": "ZVERZVE-A1BNQF7",
  "draft_name": null,
  "fallback_label": "Node 1",
  "aliases": ["kitchen screen", "display"],
  "labels": [
    {
      "text": "Kitchen display",
      "locale": "en",
      "role": "display",
      "status": "confirmed",
      "source": "user"
    },
    {
      "text": "кухонный экран",
      "locale": "ru",
      "role": "alias",
      "status": "confirmed",
      "source": "user"
    },
    {
      "text": "ZVERZVE-A1BNQF7",
      "locale": "und",
      "role": "observed",
      "status": "observed",
      "source": "runtime.hostname"
    }
  ],
  "status": "confirmed",
  "scope": {
    "subnet_id": "sn_...",
    "webspace_id": null,
    "owner_profile_id": null
  },
  "source": {
    "display_name": "user",
    "observed_name": "runtime.hostname",
    "aliases": "user"
  },
  "updated_at": 1778640000.0
}
```

Recommended statuses:

- `draft`: suggested but not confirmed.
- `confirmed`: user or policy confirmed.
- `observed`: runtime-only, usable as a display fallback but lower authority.
- `conflicted`: name or alias collides with another entity in the same scope.
- `deprecated`: old alias retained for compatibility but not suggested.

## Localization model

Localization is part of the named-entity layer because users address the same
entity through different natural-language labels.
It is not part of domain identity.

Core rules:

- `canonical_ref` is locale-neutral and must not be translated.
- User-confirmed device names such as `Kitchen display` or `ZVERZVE-A1BNQF7`
  are names, not UI strings. They should be displayed exactly as registered
  unless the user adds a localized alias.
- System-generated fallbacks such as `Node 0`, `Unnamed browser`, or
  `Unknown scenario` may be localized by UI i18n, but the localized fallback is
  still lower authority than `display_name`, registered names, and
  `observed_name`.
- Aliases may be locale-specific. A Russian alias and an English alias can
  point to the same `canonical_ref` without forcing NLU retraining.
- The resolver should prefer the request locale, then profile/subnet preferred
  locales when available, then language-neutral labels such as hostnames, then
  cross-locale aliases at lower confidence.
- Conflict detection must run per effective scope and per locale. A phrase may
  be unambiguous in `en` but conflicted in `ru`.

Suggested label shape:

```json
{
  "text": "кухонный экран",
  "locale": "ru",
  "role": "alias",
  "status": "confirmed",
  "source": "user",
  "confidence": 1.0
}
```

Recommended locale values:

- BCP-47 language tags such as `ru`, `en`, or `en-US`.
- `und` for language-neutral observed labels such as hostnames, device ids, and
  browser-generated technical names.
- `zxx` only for labels that are intentionally non-linguistic.

Until user profiles are implemented, the runtime may use the browser/login
locale as a request hint.
After profile support lands, resolver input should include:

```json
{
  "request_locale": "ru",
  "preferred_locales": ["ru", "en"],
  "profile_id": "profile:..."
}
```

This keeps localization compatible with the current local-first model and with
future per-user or per-subnet language preferences.

## Resolution pipeline

The target NLU request path should run a deterministic entity resolver before
model-dependent interpretation is treated as final.

1. Preserve the raw utterance and request metadata.
2. Build an entity snapshot from system-model objects, device inventory,
   access links, workspace manifests, desktop registry, and user-approved
   aliases.
3. Select candidate labels by request locale, profile/subnet preferred
   locales, language-neutral labels, and only then lower-confidence
   cross-locale aliases.
4. Normalize candidate labels with locale-aware case folding, punctuation
   cleanup, and safe transliteration where configured.
5. Resolve exact display-name and alias matches first.
6. Resolve observed names next, with lower confidence.
7. Use fuzzy matching only above a high threshold and only when the scope has no
   close ambiguity.
8. Replace matched spans in the model-facing text with entity masks such as
   `{device}`, `{webspace}`, `{scenario}`.
9. Emit `resolved_entities`, `unresolved_entity_spans`, and ambiguity records
   into NLU trace.
10. Let regex, neural, and Rasa stages classify intent from the normalized text.
11. Dispatch actions with canonical refs and original spans.

If multiple entities match, the resolver must not silently pick one. It should
emit an ambiguity result so the assistant can ask a focused clarification.

## Relationship to lookup tables

Static lookup tables remain useful for reproducible training snapshots:

- `modal_id`
- `node_ref`
- `app_id`
- `scenario_id`
- `webspace_id`

The named-entity registry is different. It is runtime-owned and changes as
devices are paired, nodes reconnect, browsers register, and users add aliases.

Target behavior:

- Stable manifest lookups may continue to participate in the Rasa training
  fingerprint.
- Runtime entity aliases should not force model retraining by default.
- Rasa export can include a stable snapshot for reproducibility, but the live
  preprocessor should still resolve the current registry at request time.
- Teacher/probe APIs should show both static lookup matches and live entity
  resolver matches.

## Event model

Named entities must participate in the shared
[Operational Event Model](operational-event-model.md), not hide inside UI
fallback helpers or NLU prompt text.

Target event families:

- `entity.observed`: a runtime source reported an observed label such as a
  hostname, browser family, OS, or transport-local identifier.
- `entity.draft_name.suggested`: the platform generated a human-friendly draft
  name for an observed entity that has not yet been confirmed.
- `entity.display_name.changed`: a user, policy, or governed tool changed the
  primary display name.
- `entity.alias.added`: an alias became valid for resolution and search.
- `entity.alias.removed`: an alias is no longer accepted.
- `entity.alias.deprecated`: an old alias remains accepted for compatibility
  but should no longer be suggested.
- `entity.alias.conflict.detected`: one phrase can resolve to more than one
  entity in the same effective scope.
- `entity.registry.changed`: the compact read model changed and resolver caches
  should be invalidated.
- `entity.resolution.ambiguous`: an utterance referenced a known phrase but the
  resolver could not safely choose one canonical ref.
- `entity.resolution.failed`: an utterance contained a likely entity reference
  that could not be resolved.

Recommended event payload fields:

```json
{
  "entity_ref": "device:member:8db40740-b3ff-44bf-baf5-9fb013b35b01",
  "entity_kind": "device.member",
  "scope": {
    "webspace_id": "desktop",
    "node_id": "8db40740-b3ff-44bf-baf5-9fb013b35b01"
  },
  "source": "device_inventory",
  "locale": "ru",
  "preferred_locales": ["ru", "en"],
  "actor": "user",
  "previous": { "display_name": "Node 0" },
  "current": { "display_name": "ZVERZVE-A1BNQF7" },
  "reason": "user_confirmed",
  "request_id": "optional-request-or-trace-id"
}
```

Operational rules:

- Rename and alias changes are domain facts first; UI projections are downstream
  materializations.
- `entity.registry.changed` invalidates `EntityResolver` snapshots and any
  demanded projections that render affected names.
- Authoritative sources should emit `entity.registry.changed` only when fields
  that affect the compact entity read model change. Reconnects and transient
  online/offline state should not force a registry projection refresh by
  themselves.
- Successful high-volume resolutions should normally stay in NLU trace, not the
  global event log.
- Ambiguity, failed resolution, alias conflict, and dev-mode resolver details
  should be eligible for Notifications and node skill logs.
- Events that change labels or aliases should include `locale` for linguistic
  labels, or `locale: "und"` for language-neutral observed labels.
- LLM/MCP tools should write through governed alias/display-name commands rather
  than mutating entity projections directly.

## Storage and projection boundaries

Named entities are a read model over authoritative sources, not a replacement
for them.

Initial source owners:

- `access_links`: durable browser/member access policy and user-confirmed
  device display names, plus observed browser metadata from Yjs handshakes.
- `subnet_directory`: remembered member node metadata and runtime snapshots.
- live browser/member transports: transient presence and observed names.
- workspace/scenario/skill manifests: stable software object ids and labels.
- profile state: future user-owned aliases and language preferences.

Target service:

- `NamedEntityService` builds and caches canonical entity records.
- `EntityResolver` performs text-to-ref matching.
- `EntityResolver` accepts `request_locale` and `preferred_locales` hints, but
  its output remains canonical refs and spans rather than localized dispatch
  ids.
- SDK exposes `sdk.data.entities.list_entities`,
  `sdk.data.entities.resolve_text`, and alias-management helpers.
- Yjs may project a read-only compact registry under a path such as
  `registry.named_entities` for UI and diagnostics.
- Root MCP should expose named-entity descriptors through governed read
  capability before allowing alias writes.

Current read surfaces:

- SDK: `sdk.data.entities.list_entities` and
  `sdk.data.entities.resolve_text`.
- Yjs: compact read-only projection under `registry.named_entities`.
- Root MCP / AdaOSDevPlane: `adaos_dev.get_named_entity_registry`, exposed to
  Codex as `get_named_entity_registry`, returns the same compact registry as a
  descriptor payload and accepts optional `webspace_id` and `kind` filters.
- Root MCP / NLUAuthoringPlane: `nlu_authoring.get_context`, exposed to Codex
  as `get_nlu_authoring_context`, returns a read-only authoring context with
  named entities, locale hints, canonicalization rules, and explicit
  no-write/no-training-mutation boundaries.

## UI behavior

The UI should call a shared name resolver instead of duplicating fallback rules.

For node/device labels:

- Use `display_name` when present.
- Otherwise use `primary_node_name` or registered `node_names[0]`.
- Otherwise use `observed_name` such as hostname.
- Only then use `Node N`.
- When several labels are valid, choose the best label for the active locale
  without translating user-confirmed names or changing refs.

For settings:

- Editing a name should update `display_name`, not overwrite observed facts.
- If the entity is observed-only, the UI should offer an explicit
  `Adopt device` flow or perform `adopt + rename` as a deliberate combined
  action.
- Aliases should be managed separately from the primary display name.

## LLM and MCP behavior

LLM-assisted development and operations should consume named entities as
canonical descriptors.

The model should see compact facts such as:

```json
{
  "canonical_ref": "device:browser:abc",
  "kind": "device.browser",
  "display_name": "Edge on Windows",
  "aliases": ["work browser"],
  "labels": [
    { "text": "Edge on Windows", "locale": "und", "role": "display" },
    { "text": "рабочий браузер", "locale": "ru", "role": "alias" }
  ],
  "scope": { "webspace_id": "desktop" }
}
```

LLM-authored patches should reference `canonical_ref` and `base_fingerprint`
when changing names or aliases. That prevents stale-write bugs and makes alias
changes auditable.

## Coarse implementation plan

The implementation should be delivered as vertical slices.
The important constraint is to make the named-entity layer observable and
read-only first, then add governed writes after source authority and conflict
rules are proven.

### Slice A - Contract-only baseline

Goal: freeze the data and event vocabulary without changing runtime behavior.

Deliverables:

- `NamedEntityRecord` schema or dataclass.
- `EntityResolutionResult` shape for NLU, Teacher, diagnostics, and MCP.
- Localized `labels` and request-locale metadata in the read/result contracts,
  without making localization affect canonical refs.
- Shared topic constants for `entity.*` events.
- Golden fixtures for nodes, browsers, webspaces, scenarios, skills, aliases,
  and ambiguous names.
- Documentation that states which service owns each source field.

Exit criteria:

- No UI labels or NLU dispatch behavior change yet.
- Tests can build entity records and resolution results from fixtures.

### Slice B - Read-only registry and display adapter

Goal: create the first useful read model while keeping existing write paths
unchanged.

Deliverables:

- `NamedEntityService` over device inventory, node display, browser sessions,
  workspace/scenario/skill manifests, and desktop registry entries.
- Shared display-name helper that implements the priority:
  `display_name > registered name > observed_name > fallback_label`.
- Browser/node draft-name generator that suggests names without silently
  overwriting observed facts.
- Locale-aware display selection that can prefer profile/browser language while
  preserving exact user-confirmed names.
- Optional compact read-only Yjs projection for diagnostics and UI inspection.

Exit criteria:

- `Node N` is used only as a real fallback.
- Existing settings and browser modals can render names through one helper.
- The registry can be inspected without enabling alias writes.

### Slice C - Event integration and invalidation

Goal: make naming changes part of the operational event model.

Deliverables:

- Emit `entity.observed` from node/browser/workspace discovery sources.
- Emit `entity.draft_name.suggested` for generated device/browser names.
- Emit `entity.display_name.changed` and alias lifecycle events from
  authoritative write paths.
- Emit `entity.registry.changed` when resolver snapshots and name-rendering
  projections must refresh.
- Route conflicts and ambiguous references to Notifications and node skill logs.

Exit criteria:

- Resolver caches are invalidated by events, not polling or page reloads.
- Ambiguous names become visible operator facts, not silent dispatch choices.

### Slice D - NLU preprocessor and trace

Goal: resolve names before model-specific interpretation becomes final.

Deliverables:

- `EntityResolver` preprocessing for `nlp.intent.detect.request`.
- `normalized_text`, `resolved_entities`, `unresolved_entity_spans`, and
  ambiguity records in NLU trace.
- `request_locale`, `preferred_locales`, and per-locale conflict evidence in
  resolver trace when available.
- Masked model-facing text such as `show logs for {device}`.
- Teacher/probe output that compares static lookup matches with live entity
  matches.
- Golden tests proving aliases do not require Rasa or neural retraining.

Exit criteria:

- Renaming or aliasing a device does not require model retraining.
- Ambiguous references produce clarification or a safe no-dispatch result.

### Slice E - Governed writes and UI flows

Goal: allow humans and tools to change names safely.

Deliverables:

- Adopt, rename, add-alias, remove-alias, and deprecate-alias commands.
- `base_fingerprint`, actor, source, reason, and audit metadata on writes.
- Device/browser settings flows that separate observed facts from user names.
- Alias management UI for devices first, then webspaces, scenarios, skills, and
  apps.
- Localized alias management that lets a user add language-specific aliases
  without translating canonical refs or observed hostnames.
- Dev-mode diagnostics that explain why a name was accepted, rejected, or
  marked ambiguous.

Exit criteria:

- Stale writes are rejected or require reconciliation.
- Alias conflicts are shown before they can break NLU dispatch.

### Slice F - MCP, LLM authoring, and migration cleanup

Goal: make named entities part of autonomous development and operations.

Deliverables:

- Root MCP read capability for named-entity descriptors.
- Governed MCP proposal/apply commands for display names and aliases.
- NLUAuthoringPlane context that includes canonical refs and aliases.
- Skill templates that consume canonical refs instead of raw display strings.
- Migration of `browsers_skill`, `infrastate_skill`, `infrascope_skill`, and
  other operator-facing skills away from ad hoc name fallback logic.

Exit criteria:

- LLM-authored changes can reference canonical refs and explain intended alias
  changes.
- Duplicate name logic is removed from client widgets and migrated skills.

Recommended first vertical MVP:

- Add record/result schemas and fixtures.
- Build read-only `NamedEntityService` for nodes, browsers, scenarios, skills,
  apps, and webspaces.
- Include locale metadata in the registry read model while keeping
  `display_label` backward-compatible for existing UI consumers.
- Emit `entity.observed`, `entity.draft_name.suggested`, and
  `entity.registry.changed`.
- Use the shared display helper in node/browser labels.
- Add NLU resolver dry-run trace without changing dispatch.

This MVP gives us evidence and operator diagnostics before we let names affect
action routing.

## Roadmap checklist

### Phase 0 - Contract and documentation

- [x] Document named-entity target architecture.
- [x] Link NLU, device access, UI addressing, SDK control-plane, and issue
  tracker docs to this contract.
- [x] Add the initial named-entity event contract to the Operational Event
  Model.
- [x] Define the coarse vertical implementation slices.
- [x] Add a JSON schema or dataclass for `NamedEntityRecord`.
- [x] Add a JSON schema or dataclass for `EntityResolutionResult`.
- [x] Decide the first public Yjs projection path and privacy constraints.
- [x] Document localization as label/alias metadata, not identity.

### Phase 1 - Read model and display consistency

- [x] Add initial `NamedEntityService` coverage for device inventory and
  manifest-backed lookup tables.
- [x] Add golden tests for node, browser, webspace, scenario, skill, app,
  alias, and ambiguity examples.
- [ ] Extend `NamedEntityService` coverage to the full system model and
  workspace registry.
- [ ] Emit `entity.observed`, `entity.draft_name.suggested`,
  `entity.display_name.changed`, alias lifecycle, and conflict events from the
  authoritative source services.
- [x] Emit `entity.registry.changed` from `access_links` when browser/member
  naming fields change.
- [ ] Add shared name-resolution helpers for node/device display labels.
- [x] Make core node display labels prefer node names, observed hostname, then
  `Node N`.
- [x] Make the client node-display helper treat legacy `Node N` labels as
  fallback when registered or observed names are available.
- [x] Enrich client catalog and modal-title node labels from
  `registry.named_entities` when the local label is only fallback-like.
- [ ] Extend UI/device consumers to prefer user-confirmed display names before
  registered/observed names everywhere.
- [x] Add locale metadata to compact registry labels while keeping legacy
  `display_label` compatibility.
- [x] Generate browser draft names from browser family, OS, and form factor at
  registration time.
- [x] Report read-only duplicate display-name/alias conflicts in the compact
  registry payload for diagnostics and MCP clients.
- [ ] Emit conflict events or notifications for duplicate display names and
  aliases inside the same scope.
- [x] Add a registry projection invalidation path driven by
  `entity.registry.changed`.

### Phase 2 - NLU canonicalization

- [x] Add an NLU resolver dry-run mode that records trace without changing
  dispatch.
- [ ] Add `EntityResolver` preprocessing for `nlp.intent.detect.request`.
- [ ] Add `resolved_entities`, `normalized_text`, and ambiguity records to NLU
  trace.
- [x] Add request-locale and preferred-locale hints to resolver input and trace.
- [x] Add per-locale conflict diagnostics for aliases and display names.
- [ ] Make Teacher probe responses show live entity matches and canonical refs.
- [ ] Keep runtime aliases out of the Rasa stale-training fingerprint by
  default.
- [ ] Add golden phrase tests proving renaming a device does not require model
  retraining.

### Phase 3 - UI and device settings

- [ ] Update device settings so observed-only devices can be adopted and named
  intentionally.
- [ ] Add alias-management UI for devices, browsers, webspaces, scenarios, and
  skills.
- [ ] Add localized alias-management UI after profile/subnet language
  preferences are available.
- [ ] Show ambiguity/conflict notifications in the Notifications surface.
- [ ] Show why a displayed name was chosen: user name, observed hostname,
  browser draft, or fallback.
- [x] Use `registry.named_entities` as a read-only UI enrichment source for the
  first catalog/modal node-label consumers.

### Phase 4 - SDK and skill migration

- [x] Add `sdk.data.entities` read helpers.
- [ ] Add alias-management commands with policy and audit metadata.
- [ ] Update skill templates so LLM-authored skills consume canonical refs
  rather than raw labels.
- [ ] Update `browsers_skill`, `infrastate_skill`, and `infrascope_skill` to
  read entity display names through the shared helper.

### Phase 5 - MCP and LLM authoring

- [x] Expose named-entity descriptors through Root MCP read capabilities.
- [ ] Add governed alias proposal/apply flows for LLM-assisted correction.
- [x] Include named entities in NLUAuthoringPlane context.
- [ ] Add audit records for alias changes and conflict resolution.

### Acceptance criteria

- [ ] A node with hostname `ZVERZVE-A1BNQF7` displays that name when no user
  name exists, and falls back to `Node 0` only when no meaningful name is
  available.
- [ ] A newly registered browser receives a useful draft name such as
  `Edge on Windows`.
- [ ] A phrase using a newly added device alias resolves to the correct
  canonical ref without retraining Rasa or the neural model.
- [ ] Ambiguous aliases produce a clarification path instead of silent wrong
  dispatch.
- [ ] NLU trace, Teacher probe, Notifications, and skill logs expose enough
  evidence to debug name resolution decisions.
