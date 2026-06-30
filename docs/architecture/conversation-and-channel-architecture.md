# Conversation and Channel Architecture

Status: target architecture, current-state delta, and implementation roadmap.

AdaOS needs a first-class conversation model for user-facing and
skill-owned dialogs. Voice, Telegram, browser chat, and future messenger
integrations must not each invent their own chat state, context policy, or
SDK contract.

This document defines the target model without preserving the current
`voice_chat` compatibility shape. Compatibility bridges can be added during
migration, but they are not part of the clean architecture.

Related documents:

- [Channel Semantics](channel-semantics.md)
- [Endpoint Audio Service](endpoint-audio-service.md)
- [AdaOS Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Pending Actions](pending-actions.md)
- [SDK IO](../sdk/io.md)

## Current Implemented Slice

The current runtime now has the first node-local conversation slice, but not
yet the full retrieval-oriented conversation service.

Implemented today:

- `voice_chat_skill` declares the browser Voice app and a `voice_chat.messages`
  WebIO stream.
- `RouterService` receives neutral `dialog.user_message` input and legacy
  `voice.chat.user` input, appends the user message to the node-local
  conversation ledger, projects a compact Voice tail, and normally emits
  `nlp.intent.detect.request`.
  When a process-local dialog channel is active, Router delegates the turn to
  that channel owner before NLU/Teacher fallback.
- `RouterService` receives `io.out.chat.append` as a compatibility event,
  stores the message in the same ledger when conversation metadata is present,
  and projects the browser-visible Voice tail from the active conversation.
- `adaos.sdk.chat.send(...)`, `chat.ask(...)`, and response-envelope
  materialization default to the neutral `dialog` route; generated skills
  should use these APIs instead of direct Voice/chat projection writes.
- The NLU pipeline can load skill-declared regex rules and dispatch matching
  intents to skill tools.
- The NLU dispatcher has a Voice compatibility bridge: when a skill tool returns
  `{ok: true, message: "..."}` for `_meta.route_id=voice_chat`, it publishes
  `io.out.chat.append` unless the skill already emitted a matching chat append.
- `conversation_companions.start` returns a `dialog` contract that activates a
  process-local `conversational` channel. While that channel is active,
  Voice turns route directly to `conversation_companions.talk`; explicit exit
  phrases deactivate the channel and return to the general Voice path.
- `RouterService` projects the active dialog-channel snapshot into
  `data/dialog` for the browser shell. The current projection is a compact UI
  state with `active_channel_id`, known channels, owner, default tool, active
  agent, agent icon, memory scope summary, and update metadata; the durable
  state lives in the node SQLite conversation tables.
- The selected dialog channel is persisted in the node SQLite conversation
  store. After an `adaos api serve` restart, Router restores the active
  process-local dialog runtime from that durable pointer before publishing
  `data/dialog` or the Voice chat projection. If the pointer does not exist yet
  on an upgraded node, Router can bootstrap the active projection from the
  latest ledger message.
- The `general` channel has a core-owned default agent identity:
  `agent:core:general`, displayed as `Ada`/`Ада` unless configured otherwise.
  Addressing that agent by name while another channel is active deactivates the
  active process-local channel and routes the remaining text through the
  general Voice/NLU path.
- Router seeds a persisted pilot agent/channel registry from the core general
  agent and the `conversation_companions` manifest declarations. Addressing one
  of these companion agents by name from the Voice shell routes directly to the
  owning skill tool before NLU/Teacher fallback and activates the
  `conversational` channel.
- Pilot agent projections and emitted chat messages carry `gender`, `voice`,
  `voice_profile`, and `icon` hints. The browser chat uses those hints to label
  assistant messages and pick a suitable installed speech-synthesis voice when
  auto-speak is enabled; this is a compatibility hint, not the final
  response-planning contract.
- The Voice modal can show and switch the current channel between `general` and
  `conversational` by sending `dialog.channel.select`; it also shows the active
  agent from `data/dialog`. Selecting `conversational` delegates activation to
  `conversation_companions.start`; selecting `general` deactivates the
  process-local channel and leaves a short marker in the current Voice tail.
- The global app header uses a compact Voice control: active agent chip first,
  then the listen button. Channel selection stays inside the Voice dialog
  surface so the header remains a low-friction entry point rather than the
  semantic owner of the conversation.
- The browser chat widget has an `Еще истории` affordance for Voice chat. It is
  backed by the node-local conversation ledger and keeps Yjs/WebIO as a compact
  projection only.
- Voice input has a conservative pre-NLU text-correction stage for common local
  recognition/typing errors. Corrections are stored in request metadata so
  diagnostics can show both the original and normalized text.
- The Voice toolbox has a read-only `Memory` inspector that shows the current
  channel, active agent, conversation id, projected memory scopes, and a small
  node-store memory preview. It is an observability surface, not a memory
  editor.
- Each Voice turn now carries a durable `turn_trace_id`. Router records the
  selected channel, conversation, owner, agent, action target, routing reason,
  and renderer/materialization status in the node conversation store, and
  projects the latest trace through `data/dialog.last_turn_trace` for the Voice
  debug panel.
- `adaos.sdk.conversation` and `adaos.sdk.memory` expose the first low-level
  facades for generated skills and Builder experiments. They are intentionally
  thin over the node store until response envelopes and context packets are
  frozen.
- Canonical Phase 0 conversation contracts are now represented in
  `adaos.domain.conversation`: record schemas, actor id grammar, initiator
  shape, policy defaults, response envelopes, turn traces, and compact
  `data.dialog` projection rules. Runtime tables and compatibility bridges may
  still lag the full contract, but generated/runtime code has a stable import
  target.
- Telegram input is normalized enough to preserve transport reply metadata and
  enter the NLU pipeline.

Important gaps:

- The node-local conversation/memory store is an MVP: append-only messages,
  channels, agents, memory items, and turn traces exist, but FTS, summaries,
  deletion/redaction workflows, delivery attempts, and budgeted retrieval are
  still missing.
- The dialog-channel and agent registry is persisted for the current pilot, but
  full manifest schema validation, dynamic skill-owned channels, and Builder /
  Teacher channel registration are still missing.
- `route_id=voice_chat` remains a UI/transport compatibility route for the
  current Voice surface.
- The canonical conversation output contract exists for the SDK and response
  envelope path, but remaining browser Voice code still needs compatibility
  projection cleanup.
- LLM Builder and skill runtimes do not yet receive budgeted context packets
  assembled from recent turns, summaries, memory items, and evidence refs.
- `voice_chat_skill.handle_text` still owns legacy semantic fallback behavior.
  In the target design Voice becomes a dialog shell, not the semantic owner.
- NLU Teacher clarification sessions and Builder dialogs are still partly
  chat-local instead of conversation-owned.

Implemented companion pilot scenarios:

- Start: user says "pogovorim"; NLU resolves `conversation.start`,
  `conversation_companions.start` answers, and the core activates
  `dialog_channel_id=conversational`.
- Follow-up: while `conversational` is active, a free Voice turn is not sent to
  NLU/Teacher first; Router routes it to `conversation_companions.talk` with
  `dialog_channel_id`, `conversation_id`, `conversation_owner`, and
  `active_agent_id` metadata.
- Agent switch/profile correction: addressing `Арсений`, `Ника`, or `Мира`
  from the Voice shell is resolved by the core pilot registry, delegated to
  `conversation_companions`, switches to `conversational` when needed, and
  updates `active_agent_id`. In-channel style corrections such as "говори
  короче и теплее" are handled by the skill owner policy through
  `conversation_companions.talk` delegating to `update_profile`.
- Exit: explicit "general"/"back to general" style commands deactivate the
  pilot channel and return unmatched turns to the general Voice fallback.
  Addressing the general agent by name, for example "Ада, ...", performs the
  same channel exit and continues with the remaining text in the general path.
- Manual channel switch: the Voice selector can switch `general` ->
  `conversational` through the same skill-owned start contract and
  `conversational` -> `general` through core-owned deactivation.
- History: the pilot pages older messages from the node-local conversation
  ledger through the `Еще истории` control. The browser sends the current
  `conversation_id` / `dialog_channel_id`, so `general` and `conversational`
  histories stay isolated while Yjs/WebIO remains a compact projection.
- Observability: the Voice debug panel can show the last turn policy decision,
  selected tool, selected channel/agent, and renderer/materialization path from
  the durable turn trace.

## Design Rules

1. A conversation is not a transport.
2. A transport is not a context boundary.
3. A skill can own a conversation without owning the physical delivery
   channel.
4. The SDK should expose conversations as the primary chat API; transport
   specific IO is an escape hatch.
5. LLM-generated skills should be able to create and use a private skill chat
   through stable SDK calls and manifest hints.
6. Context belongs to the conversation, not to Telegram, voice, or browser
   state.
7. Conversation state is durable enough to resume across transports, but each
   transport can carry its own external references and delivery status.
8. A dialog channel is a user-interface binding to a conversation, not a
   transport channel and not the canonical history id.
9. Conversation storage is node-local and core-governed. A skill may be the
   logical owner of a conversation or memory namespace, but it should not own
   arbitrary private transcript files as the primary storage model.
10. Yjs and WebIO streams are browser projection layers. They may carry compact
    active tails, but must not become the unbounded transcript store.
11. Long-context retrieval is a first-class runtime service. LLM Builder and
    skill runtimes must receive budgeted context packets, not raw unbounded
    transcripts.
12. NLU is not the dialog manager. Rasa, regex, neural, or Teacher stages may
    classify text, but AdaOS owns turn tracking, repair state, forms, response
    planning, memory policy, and action dispatch.

## Vocabulary

### Transport

The physical or integration path used to receive or deliver a message.

Examples:

- `voice`
- `telegram`
- `web`
- `slack`
- `endpoint_audio`

Transport state includes external ids such as Telegram `chat_id`,
`message_id`, bot id, endpoint id, or browser session id. It does not define
the logical dialog context.

### Conversation

The logical dialog container with its own identity, participants, history,
context policy, routing policy, and owner.

Examples:

- the default assistant conversation
- a Builder work conversation
- an NLU Teacher clarification conversation
- a skill-specific support conversation

### Thread

A branch inside a conversation. Threads are optional. Use them when one
conversation needs multiple independently resumable subtopics without creating
a new owner boundary.

Examples:

- Builder task thread for one draft
- repair thread for one failed activation
- long-running troubleshooting thread

### Surface

The application or agent surface currently handling the conversation.

Examples:

- `general_assistant`
- `builder`
- `nlu_teacher`
- `skill:<skill_id>`

Surface is used for policy, context selection, UI routing, and tool access. It
is not a delivery target.

### Dialog Channel

A user-facing slot in a global dialog surface that points to a conversation.

Examples:

- `general`
- `conversational`
- `builder`
- `teacher`

A dialog channel exists so the browser Voice/chat UI can switch the visible
context without creating separate apps for every dialog owner. It is not a
transport channel from [Channel Semantics](channel-semantics.md), and it is not
the durable history key. The durable key is `conversation_id`.

Channel rules:

- A channel entry maps to one active `conversation_id` at a time.
- Switching a channel switches the visible history by conversation id.
- A channel may be created by core, by a user command, or by an authorized
  skill request, but the core owns the channel registry.
- Channel names are product-level affordances. They should be stable enough
  for UI and voice commands, but they should not be used as permission keys.
- The same conversation may be reachable from more than one transport, but it
  should normally be reachable from one active dialog channel per webspace.

### Owner

The actor accountable for interpreting future turns in a conversation.

Examples:

- `core:general_assistant`
- `core:nlu_teacher`
- `skill:conversation_companions`
- `skill:builder`

The owner controls context policy, fallback behavior, and default tool access.
Ownership is not the same as initiation: a skill can request a conversation
that core creates, and core can initiate a conversation whose owner is a skill
or core surface.

### Initiator

The actor or event that caused a conversation, thread, or message turn to
start.

Examples:

- user sent a message or switched a dialog channel
- skill opened a private support chat
- core started an NLU Teacher clarification after a failed intent match
- runtime recovery started an operator conversation after a service failure
- Telegram deep link selected an existing conversation

The initiator is evidence, not authority. It is recorded for audit,
idempotency, and proactive-message policy. The owner and routing policy still
decide whether the conversation may continue and where it may be delivered.

### Node Conversation/Memory Store

The physical storage service on one AdaOS node.

The store is local to the node for performance, durability, indexing, and
operational simplicity. The first implementation should use the node's SQLite
database with WAL enabled and FTS5 where available. Later implementations may
add a vector index or separate search sidecar, but the API contract should stay
node-local and owner-scoped.

The store is not the same as "core owns every dialog". Core governs schema,
policy, routing, idempotency, projection, and access checks. The logical owner
of a conversation or memory item may still be `core:*`, `skill:*`, or a
skill-scoped agent.

### Logical Memory Owner

The actor whose policy controls interpretation and access to a memory item or
conversation history segment.

Examples:

- `core:general_assistant`
- `core:nlu_teacher`
- `skill:conversation_companions`
- `agent:conversation_companions:nika`

Logical ownership is enforced by the conversation/memory service. Skills use
SDK calls scoped to their owner id; they do not query the node database
directly.

### Agent

A skill-scoped participant that can speak or maintain behavior/personality
state inside one conversation.

Agents are not dialog channels. One `conversational` channel can host multiple
agents such as `arseny`, `nika`, and `mira`. Messages and memory items should
carry `agent_id` when the turn or fact belongs to a specific agent.

### Memory Item

A normalized fact, preference, agreement, profile trait, summary, or retrieval
fragment derived from conversation turns or explicit user configuration.

Memory items are separate from message history. They have source references,
scope, consent, owner, confidence, timestamps, and optional expiry. This allows
the runtime to retrieve "what matters" without rereading the entire transcript.

## Target Data Model

Conversation records and memory live in a node-local conversation/memory
service. The target model separates physical storage from logical ownership:

1. Node-local durable store.
   This is the physical source of truth on one node for conversation metadata,
   append-only message ledger, thread records, dialog-channel bindings,
   transport bindings, memory items, segment summaries, search indexes,
   idempotency keys, delivery status, and retention/redaction metadata.
   SQLite with WAL and FTS5 is the preferred first implementation because it is
   local, transactional, easy to back up, and fast enough for the current node
   runtime.
2. Core-governed registry and policy.
   Core owns ids, schema, owner-scoped access checks, projection refresh,
   retention, redaction, routing, and federation. Core does not necessarily own
   the semantics of every conversation.
3. Logical owner namespace.
   A conversation or memory item can be owned by `core:*`, `skill:*`, or a
   skill-scoped agent. The logical owner controls prompt assembly and meaning,
   but accesses storage through scoped SDK/runtime APIs.
4. Browser projection.
   Yjs contains only compact, demand-aware browser state: active dialog channel,
   conversation summaries, and bounded message tails for active/visible
   conversations. It is a projection cache, not the transcript ledger.
5. WebIO streams.
   Streams provide low-latency replace-mode tails for open chat panels. They are
   bounded and recoverable from the node-local store or Yjs projection, but they
   are not durable history.
6. Background indexes and summaries.
   FTS, segment summaries, memory extraction, and optional vector embeddings
   are built asynchronously from the append-only ledger. User turns must not
   block on heavy summarization or embedding jobs.

Legacy `skill_memory` remains valid for simple private runtime state and for
compatibility. New LLM-facing skills should prefer the node conversation/memory
service for dialog history, profiles, long-term preferences, and retrieval
fragments, using owner-scoped APIs instead of private transcript files.

Canonical conversation record:

```json
{
  "id": "conv.builder.default",
  "node_id": "node.local",
  "kind": "builder",
  "owner": "skill:builder_skill",
  "logical_owner": "skill:builder_skill",
  "surface": "builder",
  "webspace_id": "default",
  "title": "Builder",
  "state": "active",
  "created_by": {
    "type": "user",
    "id": "user.local",
    "source": "web",
    "reason": "opened_builder_channel"
  },
  "participants": [
    {"type": "user", "id": "user.local"},
    {"type": "skill", "id": "builder_skill"}
  ],
  "context_policy": {
    "strategy": "isolated",
    "memory_scope": "skill_user",
    "include_general_history": false,
    "max_history_messages": 60,
    "summary_policy": "rolling_summary"
  },
  "history_policy": {
    "mode": "node_store",
    "searchable": true,
    "cross_skill_use": "deny_by_default",
    "raw_window_messages": 500,
    "segment_size_messages": 40,
    "summarization": "async"
  },
  "retrieval_policy": {
    "recent_turns": 20,
    "fts_top_k": 8,
    "semantic_top_k": 0,
    "memory_top_k": 12,
    "max_context_tokens": 12000
  },
  "routing_policy": {
    "default_transports": ["web", "telegram"],
    "allow_voice": false,
    "allow_proactive": false
  },
  "retention_policy": {
    "class": "normal",
    "max_raw_messages": 500,
    "redaction": "policy_controlled"
  },
  "created_at": 1730000000.0,
  "updated_at": 1730000000.0
}
```

Skill conversation with several agents:

```json
{
  "id": "conv.skill.conversation_companions.default",
  "node_id": "node.local",
  "kind": "skill",
  "owner": "skill:conversation_companions",
  "logical_owner": "skill:conversation_companions",
  "surface": "skill:conversation_companions",
  "webspace_id": "default",
  "title": "Companions",
  "state": "active",
  "agents": [
    {"id": "arseny", "display_name": "Arseny", "role": "advisor"},
    {"id": "nika", "display_name": "Nika", "role": "skeptic"},
    {"id": "mira", "display_name": "Mira", "role": "storyteller"}
  ],
  "active_agent_id": "arseny",
  "context_policy": {
    "strategy": "isolated",
    "memory_scope": "skill_user",
    "include_general_history": false,
    "summary_policy": "rolling_summary"
  },
  "history_policy": {
    "mode": "node_store",
    "logical_owner": "skill:conversation_companions",
    "searchable": true,
    "cross_skill_use": "deny_by_default",
    "raw_window_messages": 300,
    "segment_size_messages": 40,
    "summarization": "async"
  },
  "personalization_policy": {
    "global_user_profile": "read_with_consent",
    "skill_user_profile": "read_write",
    "agent_user_profile": "read_write",
    "conversation_profile": "read_write"
  }
}
```

Canonical message record:

```json
{
  "id": "msg.123",
  "node_id": "node.local",
  "conversation_id": "conv.builder.default",
  "seq": 42,
  "thread_id": null,
  "role": "assistant",
  "from": {"type": "skill", "id": "builder"},
  "agent_id": null,
  "initiator": {"type": "skill", "id": "builder", "reason": "draft_ready"},
  "content": [{"type": "text", "text": "What should we change?"}],
  "transport": "web",
  "external_ref": null,
  "meta": {
    "trace_id": "trace.123",
    "route_id": "voice_chat",
    "dialog_channel_id": "builder"
  },
  "index_state": {"fts": "pending", "summary": "pending", "embedding": "not_configured"},
  "created_at": 1730000001.0
}
```

Memory item record:

```json
{
  "id": "mem.123",
  "node_id": "node.local",
  "owner": "skill:conversation_companions",
  "scope": "agent_user",
  "user_id": "user.local",
  "conversation_id": "conv.skill.conversation_companions.default",
  "agent_id": "nika",
  "kind": "preference",
  "text": "User prefers short, direct replies from Nika.",
  "source_refs": [{"message_id": "msg.120", "seq": 17}],
  "confidence": 0.82,
  "consent": "skill_scoped",
  "visibility": "owner_only",
  "expires_at": null,
  "created_at": 1730000001.0,
  "updated_at": 1730000001.0
}
```

Browser-visible dialog projection:

```json
{
  "data": {
    "dialog": {
      "active_channel_id": "conversational",
      "channels": {
        "general": {
          "conversation_id": "conv.general.default",
          "title": "General",
          "surface": "general_assistant"
        },
        "conversational": {
          "conversation_id": "conv.skill.conversation_companions.default",
          "title": "Companions",
          "surface": "skill:conversation_companions"
        },
        "builder": {
          "conversation_id": "conv.builder.default",
          "title": "Builder",
          "surface": "builder"
        }
      },
      "visible_tail": {
        "conversation_id": "conv.skill.conversation_companions.default",
        "messages": [
          {
            "id": "msg.123",
            "role": "assistant",
            "from": {"type": "skill", "id": "conversation_companions"},
            "text": "Choose a character or just start talking.",
            "created_at": 1730000001.0
          }
        ],
        "message_count": 1,
        "updated_at": 1730000001.0
      }
    }
  }
}
```

Rules:

- `conversation.id`, `kind`, `owner`, `surface`, `webspace_id`, `state`,
  `context_policy`, and `routing_policy` are required.
- `node_id` identifies the physical authority for storage and indexing.
- `owner` is the actor accountable for the conversation context.
- `logical_owner` controls memory interpretation and owner-scoped retrieval.
- `created_by` records who or what initiated the conversation. It is required
  for user-visible conversations created after the conversation service lands.
- `surface` is the runtime that should interpret incoming user messages by
  default.
- `kind` is a product category, not an authorization key.
- `participants` describes who may see or write into the conversation.
- `context_policy` controls memory and LLM prompt assembly.
- `history_policy` controls physical history storage, indexing, summarization,
  cross-owner access, and raw-window limits.
- `retrieval_policy` controls the budgeted context packet assembled for LLM
  Builder and skill runtime calls.
- `routing_policy` controls where outbound messages may be delivered.
- `retention_policy` controls raw-history limits, summaries, export/delete
  behavior, and redaction. The defaults must be conservative for personal
  dialog.
- `agents` are skill-scoped participants inside a conversation, not separate
  dialog channels.
- Message `content` is typed; plain text is only one content part.
- Message `seq` is monotonically increasing per conversation and is preferred
  for range queries, segmentation, and replay.
- Message `initiator` is evidence for why the turn exists. It does not override
  owner or participant policy.
- Message `agent_id` points to a skill-scoped agent when the turn is from or to
  a specific character/agent.
- Memory `scope` starts with `global_user`, `skill_user`, `agent_user`, and
  `conversation`.
- Memory `consent` and `visibility` are required for anything that may be
  reused outside the immediate prompt.
- Message `transport` records where the message actually moved. It does not
  decide which context the message belongs to.
- `external_ref` stores platform-specific ids and must never be used as the
  canonical conversation id.
- `data.dialog` is a browser projection. It is allowed to be stale, compact, or
  absent during recovery. The canonical store remains authoritative.

## Node Storage Layout

The first implementation should prefer a single node-local database over
per-skill transcript files. This is the best tradeoff for performance,
operations, search, and future federation.

Initial logical tables:

```text
conversations(
  id, node_id, kind, owner, logical_owner, surface, webspace_id,
  title, state, policy_json, created_at, updated_at
)

dialog_channels(
  webspace_id, channel_id, active_conversation_id, title,
  owner, surface, updated_at
)

conversation_messages(
  id, node_id, conversation_id, seq, thread_id, role,
  owner, agent_id, text, content_json, source_json,
  index_state_json, created_at
)

conversation_segments(
  id, conversation_id, seq_from, seq_to, summary,
  token_estimate, index_state_json, updated_at
)

memory_items(
  id, node_id, owner, scope, user_id, conversation_id, agent_id,
  kind, text, source_refs_json, policy_json,
  confidence, consent, visibility, expires_at, created_at, updated_at
)

delivery_attempts(
  id, message_id, transport, external_ref_json, status,
  error, created_at, updated_at
)

conversation_idempotency(
  key, conversation_id, message_id, action_target, status, created_at
)
```

Recommended implementation details:

- use append-only writes for message turns; update only derived state and
  delivery/index status
- use per-conversation `seq` for stable ranges and fast pagination
- use FTS over `conversation_messages.text`, `conversation_segments.summary`,
  and `memory_items.text`
- build segment summaries and optional embeddings asynchronously
- keep hot writes independent from LLM calls, summarization, and vector indexing
- keep Yjs projection writes bounded and fingerprinted
- expose all storage through conversation/memory services and SDKs, not direct
  SQL from skills

## Retrieval and Performance Model

LLM Builder and skill runtime should receive a compact context packet assembled
by the node conversation/memory service.

Default retrieval pipeline:

1. Resolve current `conversation_id`, `thread_id`, `owner`, `agent_id`, user,
   and policy.
2. Fetch recent turns by `(conversation_id, seq desc)` within a strict message
   or token budget.
3. Fetch relevant segment summaries through FTS and optional vector search.
4. Fetch memory items by owner/scope/agent/user with policy checks.
5. Attach evidence refs for Pending Actions, NLU traces, Builder validation,
   and tool outcomes instead of copying large blobs.
6. Return a deterministic context packet with budget accounting.

Context packet shape:

```json
{
  "conversation_id": "conv.skill.conversation_companions.default",
  "owner": "skill:conversation_companions",
  "agent_id": "nika",
  "budget": {"max_tokens": 12000, "estimated_tokens": 5300},
  "recent_messages": [],
  "relevant_segments": [],
  "memory_items": [],
  "profiles": {
    "global_user": null,
    "skill_user": {},
    "agent_user": {},
    "conversation": {}
  },
  "evidence_refs": []
}
```

Runtime rules:

- never assemble prompts by reading the full transcript
- use recent turns + selected segments + selected memory items
- make cross-skill memory reuse deny-by-default
- make global user memory consent-aware and auditable
- degrade gracefully when FTS/vector/summarization is unavailable
- keep federation optional and timeout-bound

## Subnet Federation

AdaOS should not implement a strongly consistent distributed conversation
database as the first target. Each node owns its local conversation/memory
store.

Cross-node access is a federated query:

1. The requesting node sends a policy-checked memory/search request to target
   node(s).
2. Each target node runs local retrieval under local owner, user, and consent
   policy.
3. Each target returns fragments, summaries, scores, and refs, not raw database
   access.
4. The requester aggregates partial results with timeouts and records evidence.

This model supports subnet-wide search and LLM Builder context discovery
without coupling all nodes to one distributed SQL or vector index.

## Canonical Conversation Kinds

Initial kinds:

- `general`: default assistant conversation for normal user requests.
- `builder`: governed creation and modification workflow.
- `skill`: skill-owned dialog with isolated or scoped context.
- `teacher`: NLU Teacher, authoring, correction, and clarification workflows.
- `support`: operational support, repair, onboarding, or troubleshooting.

New kinds require a reason that cannot be expressed by owner, surface,
participants, or context policy.

## Agents and Personalization

A skill-owned conversation may host multiple agents/personas. This is required
for `conversation_companions` and for future multi-agent Builder or support
workflows.

Rules:

- A dialog channel selects a conversation, not one agent.
- A conversation may declare several agents and one active agent.
- A message may carry `agent_id` when a specific agent speaks, is addressed, or
  owns the memory update.
- Agent profiles are logical skill-owned records stored in the node
  conversation/memory service or referenced through the skill's profile API.
- Agent memory defaults to `scope=agent_user` or `scope=conversation`.
- Switching active agent is a conversation state change, not a channel switch.

Personalization layers:

- `global_user_profile`: core-governed, explicit consent, reusable only through
  policy-checked retrieval
- `skill_user_profile`: owned by one skill for one user
- `agent_user_profile`: owned by one skill agent for one user
- `conversation_profile`: temporary agreements and preferences inside one
  conversation
- `memory_items`: extracted facts, preferences, and summaries with source refs
  and confidence

LLM Builder and generated skills should treat personalization as typed memory
with source refs and consent, not as arbitrary prompt text hidden in skill
files.

## Dialog Initiation

AdaOS has more than one legitimate initiator. The architecture must preserve
that without letting every skill invent its own delivery rules.

### User-Initiated

A user initiates a conversation by sending a message, selecting a dialog
channel, opening a dedicated surface, following a deep link, or replying through
a transport such as Telegram.

Rules:

- If an explicit `conversation_id` is present and policy allows it, append the
  turn there.
- If a `dialog_channel_id` is present, resolve it to the active conversation in
  that channel.
- If neither is present, resolve to the active channel for the current surface
  or fall back to `general`.
- The user message is appended before semantic dispatch so failures remain
  visible and auditable.

### Skill-Initiated

A skill may request a conversation through the SDK or manifest. The core
creates or opens the conversation, records the skill as initiator, and applies
policy before any visible message is delivered.

Allowed examples:

- a support skill opens its declared private chat after the user invokes it
- `conversation_companions` opens a `kind=skill` companion conversation after
  the user says "let's talk"
- a skill asks a bounded question through `chat.ask(...)`

Risk controls:

- Skill-initiated visible conversations require an allowed owner/surface and
  participants.
- Proactive skill messages require `routing_policy.allow_proactive=true` or an
  explicit user action/Pending Action that authorizes the prompt.
- A skill may be the owner of conversation semantics, but the core owns
  conversation creation, id allocation, history append, and delivery routing.
- If the skill runtime is unavailable, core records the failed turn and applies
  the conversation fallback policy instead of silently dropping the message.

### Core-Initiated

The core may initiate a conversation when the runtime itself needs a governed
human interaction.

Examples:

- NLU Teacher clarification after an intent miss or unsafe candidate
- Builder repair/approval flow after generated artifact validation
- runtime recovery conversation after repeated service failures
- onboarding, pairing, access elevation, or policy review

Rules:

- Core-initiated conversations still have an owner, such as
  `core:nlu_teacher`, `skill:builder`, or `core:runtime_recovery`.
- Durable decisions should use Pending Actions. Conversation messages may
  explain and collect context, but the decision source of truth is the Pending
  Action response.
- Core must not hide a new conversation behind a transient notification if the
  user is expected to respond later.

### Transport-Initiated

Some integrations provide entry points that look like initiation, such as a
Telegram command, callback button, QR/deep link, or endpoint audio wake event.
These are transport facts. They must be normalized into a user-, skill-, or
core-initiated conversation according to routing policy.

Transport ids are recorded in bindings and `external_ref`, never as the
canonical conversation id.

## Global Dialog Surface

The browser Voice app should evolve into the default global dialog shell.

Target behavior:

- It shows a compact channel selector near the listening control.
- Initial channels are `general`, `conversational`, and `builder`.
- Selecting a channel changes `data.dialog.active_channel_id` and switches the
  visible history to that channel's active conversation.
- Speech and typed input both send the same neutral dialog input event with
  `dialog_channel_id`.
- `voice.chat.user` remains a compatibility command that forwards to the same
  dialog input path.
- Voice-specific controls remain in the shell: STT, TTS, push-to-talk, endpoint
  audio status, and browser recovery.
- Semantic work moves out of `voice_chat_skill` into conversation owners,
  surfaces, NLU actions, and channel fallback policies.
- The same dialog shell is available as an embeddable Voice Chat widget for
  workbench scenarios. Prompt IDE embeds this widget instead of implementing a
  private chat; it configures the widget with `dialog_channel_id=builder` and
  the source Builder conversation while the dev webspace renders the mockup.

Example channel policy:

```json
{
  "general": {
    "surface": "general_assistant",
    "default_fallback": "nlu_teacher_or_voice_legacy_compat"
  },
  "conversational": {
    "surface": "skill:conversation_companions",
    "entry_intents": ["conversation.start"],
    "default_tool": "conversation_companions.talk",
    "exit_intents": ["conversation.exit", "dialog.general"]
  },
  "builder": {
    "surface": "builder",
    "default_tool": "builder_skill.chat",
    "decision_source": "pending_actions"
  }
}
```

For "let's talk" / Russian "pogovorim":

1. Resolve the utterance as `conversation.start`.
2. Ensure the `conversational` channel exists.
3. Create or reuse the `conversation_companions` conversation.
4. Set `active_channel_id=conversational`.
5. Run `conversation_companions.start`.
6. Append the returned message through the conversation service.
7. Route subsequent free turns in that channel directly to
   `conversation_companions.talk` until an exit/general-channel command is
   received.

## Builder Conversation

Builder should own a dedicated conversation by default.

Why:

- Builder drafts, tool traces, preview evidence, and repair notes should not
  pollute the general assistant history.
- Builder needs a stronger context policy than a normal chat: current
  webspace, target artifact refs, descriptors, validation evidence, and
  approval state.
- Builder interactions often lead to Pending Actions and runtime mutations.
  They need clear audit boundaries.

Target behavior:

- A user can enter Builder through browser, Telegram, or another transport.
- Addressed messages to `builder` / `Builder` / `Строитель` / `строитель`
  resolve to the Builder channel. The runtime strips the agent address before
  sending the turn to `builder_skill`, so the initial Builder chat starts with
  the actual user request.
- The Builder conversation owner is `skill:builder_skill`; Prompt IDE is a
  workbench surface, not the conversation owner.
- Prompt IDE uses the shared Voice Chat widget for Builder turns. The widget is
  a reusable dialog transport/view, while `builder_skill` remains the semantic
  owner of the conversation.
- Builder reads and writes through the conversation SDK.
- Builder context includes the source webspace id, the paired dev webspace id,
  and the active draft id. The paired dev webspace is derived as
  `dev_webspace_id = f"{safe_source_webspace_id}-dev"` and reused for all
  drafts opened from that source webspace.
- Approval and apply decisions use Pending Actions, not plain chat messages as
  the durable decision source.

## SDK Contract

Generated skills should use conversation APIs for ordinary dialog.

Target SDK shape:

```python
from adaos.sdk import conversation

chat = conversation.current()
chat.send("Done.")
```

Skill-owned conversation:

```python
from adaos.sdk import conversation

chat = conversation.open(
    kind="skill",
    owner="skill:my_skill",
    surface="skill:my_skill",
    title="My Skill",
    context_policy={"strategy": "isolated", "memory_scope": "skill"},
)

reply = chat.ask("What should I configure?", timeout="10m")
context = chat.context(max_tokens=8000)
facts = chat.memory.search("user preferences", scope="skill_user", top_k=5)
```

Builder conversation:

```python
from adaos.sdk import conversation

builder_chat = conversation.open(
    kind="builder",
    owner="skill:builder",
    surface="builder",
    title="Builder",
)

builder_chat.send("I prepared a draft. Review the preview before apply.")
```

Recommended public helpers:

- `conversation.current()`
- `conversation.open(...)`
- `conversation.get(conversation_id)`
- `conversation.for_skill(skill_id, ...)`
- `conversation.for_user(user_id, kind="general")`
- `chat.send(content, ...)`
- `chat.ask(prompt, timeout=...)`
- `chat.history(limit=..., thread_id=None)`
- `chat.context(max_tokens=..., purpose="reply|builder|diagnostics")`
- `chat.memory.search(query, scope=..., agent_id=None, top_k=...)`
- `chat.memory.remember(text, scope=..., source_refs=..., consent=...)`
- `chat.start_thread(title=..., context_policy=None)`

Transport-specific APIs remain available only for transport features:

- Telegram keyboard, photo, document, and callback features.
- Voice-only TTS/STT controls.
- Endpoint audio session controls.

Plain text replies should not use `io.telegram.send`,
`io.voice.tts.speak`, or low-level `io.out.chat.append` directly.

## Manifest Contract

Skills that need their own conversation declare it explicitly:

```yaml
conversations:
  main:
    kind: skill
    title: My Skill
    dialog_channel:
      preferred_id: my_skill
      user_visible: true
    context:
      strategy: isolated
      memory_scope: skill_user
    history:
      mode: node_store
      searchable: true
      cross_skill_use: deny_by_default
      summarization: async
    retrieval:
      recent_turns: 20
      fts_top_k: 8
      memory_top_k: 12
    routing:
      default_transports: ["web", "telegram"]
      allow_voice: false
```

Builder-like skills use:

```yaml
conversations:
  builder:
    kind: builder
    owner: skill:builder_skill
    title: Builder
    context:
      strategy: isolated
      memory_scope: skill_user
      include_general_history: false
      include_workspace_binding: true
    routing:
      default_transports: ["web", "telegram"]
      allow_voice: true
      addressed_agents:
        - builder
        - Builder
        - Строитель
        - строитель
```

Manifest rules:

- `conversations.<name>.kind` is required.
- `context.strategy` is required and starts with `isolated`, `shared`, or
  `ephemeral`.
- `dialog_channel` is optional. It requests a browser/global-dialog affordance;
  the core may deny, rename, hide, or merge it according to product policy.
- `history.mode` starts with `node_store`, `ephemeral`, or `external_ref`.
  `node_store` is the default for professional LLM-facing skills.
- `history.cross_skill_use` must be explicit and defaults to
  `deny_by_default`.
- `retrieval` is advisory. The runtime may lower budgets under memory, latency,
  or model-token pressure.
- `routing.default_transports` is advisory. Runtime policy may remove
  transports.
- Skills must not hard-code external chat ids in manifest declarations.
- LLM skill-generation guidance must prefer this manifest surface when a skill
  needs a private chat.

## Runtime Routing

Inbound routing stages:

1. Normalize transport input into a platform-neutral input envelope.
2. Resolve user, webspace, endpoint, and external transport references.
3. Resolve or create `conversation_id`.
4. Append the user message to the conversation store.
5. Dispatch to the conversation `surface`.
6. Preserve transport metadata for replies and audit.

Outbound routing stages:

1. Skill or surface writes to a conversation.
2. Conversation service records the message.
3. Routing policy chooses eligible transports and recipients.
4. Transport adapters deliver messages and record delivery status.
5. UI projections subscribe to conversation state, not transport-specific chat
   state.

Conversation resolution inputs:

- explicit `conversation_id`
- explicit `thread_id`
- active surface selection in the current UI
- active `dialog_channel_id` in the global dialog shell
- Telegram command or deep link
- endpoint audio dialog mode
- previous transport binding for the user
- fallback to `general`

## Dispatcher and Output Contract

The dispatcher must not rely on every skill knowing how to publish into the
active chat UI.

Target contract:

- A tool action may return a plain `message` field, typed `content`, or a
  richer conversation action result.
- If the incoming turn has conversation metadata and the tool result contains
  user-visible content, the dispatcher/conversation service appends that
  content to the active conversation.
- A skill may still emit transport-specific or rich side effects, but ordinary
  text replies should work through the returned result.
- `io.out.chat.append` remains a compatibility path and transport/event
  primitive. It should be bridged into the current conversation when enough
  metadata is present.
- Tool result publication must be idempotent by `request_id`, action target,
  and conversation id so a retry does not duplicate the assistant reply.
- If the skill both emits `io.out.chat.append` and returns `message`, the core
  must dedupe or mark one path as already materialized.

Minimal compatibility rule for the current Voice path:

```text
skill tool result {ok: true, message: "..."}
  + incoming turn resolves to the Voice/dialog surface
  -> dispatcher publishes io.out.chat.append with original _meta
  -> router projects into the current Voice/dialog tail
```

This is an interim bridge. The target path is:

```text
skill tool result
  -> conversation.append_message(...)
  -> active dialog projection/WebIO stream refresh
  -> transport delivery as allowed by routing_policy
```

## History and Context Policy

History has several distinct forms and they should not be collapsed.

- Raw message ledger: canonical, append-friendly, durable, node-local, and
  core-governed, bounded by retention policy.
- Visible tail: compact browser projection or stream payload for the active
  channel/conversation.
- Prompt context: selected and possibly summarized subset assembled according
  to `context_policy`.
- Owner-scoped memory: skill/core/agent-owned profile and preference state
  stored in the node conversation/memory service or a compatibility
  `skill_memory` namespace.
- Audit/evidence refs: traces, Pending Actions, validation reports, and tool
  outcomes linked by id rather than copied into every chat turn.

Conversation owners may request different context strategies:

- `isolated`: only this conversation's history and declared memory.
- `shared`: this conversation may include selected general context.
- `ephemeral`: visible turn handling without durable raw history beyond audit
  minimums.
- `summary_only`: raw messages are compacted into a summary after a small
  window.

The default for user-facing skill chats should be `isolated` with a bounded raw
window, FTS-enabled retrieval, and rolling summaries. Builder and Teacher
conversations should keep evidence references rather than unbounded
generated-text transcripts.

## LLM Skill Authoring Guidance

LLM-facing SDK docs and prompt context should teach these rules:

- Use `conversation.current().send(...)` to answer in the current dialog.
- Use `conversation.open(...)` or manifest `conversations` when the skill needs
  its own chat.
- Use `chat.ask(...)` for a bounded user response.
- Use Pending Actions for approvals, mutations, retries, and decisions that
  need durable evidence.
- Use transport-specific APIs only for platform-native features.
- Never store Telegram `chat_id`, voice session id, or browser session id as
  conversation memory keys.
- Do not mix Builder planning history into the general assistant conversation.

## Critical Roadmap Review

The target architecture is directionally correct, but the development plan must
not be read as a purely serial waterfall. A usable dialog system crosses
storage, transport, policy, SDK, and UI boundaries, so the first deliverable
should be a thin vertical slice with explicit tests.

Roadmap verdict:

- The proposed first practical slice matches the roadmap, but it spans Phase
  0.5, Phase 1, Phase 4, and Phase 5. It should be treated as a vertical slice,
  not as evidence that each phase is complete.
- The original sequence was too implicit about the missing dialog manager. A
  first-class Dialog Runtime / Tracker is required between transport input, NLU,
  skill dispatch, and output materialization.
- The storage plan is sound: one node-local conversation/memory store per node
  gives fast append, local search, summaries, policy checks, and future
  federation without making every skill a database owner.
- Retrieval and SDK work can start against a minimal store before FTS, segment
  summaries, or vector search are complete, as long as the context-packet
  contract already includes budgets, evidence refs, and policy denials.
- Voice should become the global dialog shell. It should not decide semantic
  behavior beyond channel selection, STT/TTS controls, and compatibility
  forwarding.
- `conversation_companions` is the right pilot because it exercises channel
  switching, skill-owned history, multiple agents, profile correction, fallback
  turns, and memory policy without device-control risk.
- Builder and NLU Teacher should migrate after the core slice proves channel
  identity, turn tracking, result materialization, and context isolation.

Known missing architecture elements now promoted into the roadmap:

- Dialog Runtime / Tracker with explicit turn state, active frame, repair
  state, owner, active agent, and trace id.
- Dialog act and response envelope schemas so tools can return structured
  results without publishing directly to a transport tail.
- Task frames / forms for slot filling, validation, correction, confirmation,
  cancel, resume, and parameter change.
- Response planning and rendering policy for text, speech, cards, Pending
  Actions, notifications, and Builder evidence views.
- End-to-end trace continuity from transport/STT through NLU, retrieval, tool
  calls, memory writes, response rendering, and delivery attempts.
- Dialog-level golden conversations and metrics: success rate, repair rate,
  fallback rate, latency, context budget, memory-write quality, and policy
  denials.
- Safety policy for memory writes, cross-owner retrieval, prompt-injection
  through memory/history, PII redaction, export, delete, and consent.

## LLM Threat Model

This threat model applies to Dialog Runtime, Builder, generated skills, memory
retrieval, and user-facing assistants. It is intentionally practical: every
class below must map to a runtime control, a trace/audit artifact, and at least
one regression test before broad rollout.

Threat classes:

- Prompt injection through retrieved memory, history, remote node fragments,
  web content, or skill-owned notes. Retrieved text is evidence, not
  instruction. It must be separated from system/developer policy, labelled with
  a trust boundary, and inspected for instruction-like or exfiltration-like
  content before it enters a context packet.
- Sensitive information disclosure. User profile memory, skill memory,
  credentials, system prompts, hidden policy, transport ids, and cross-owner
  fragments must be redacted or denied by policy before they become prompt
  context, export bundles, diagnostics, or model output.
- Excessive agency. LLM-generated or LLM-selected actions that touch files,
  networks, credentials, devices, browsers, runtime lifecycle, cross-node
  retrieval, or destructive operations require an explicit action-risk class
  and must pass an approval gate before execution.
- Insecure output handling. Model output must not be treated as trusted code,
  SQL, shell, routing policy, NLU regex, or UI schema until it passes schema
  validation, static checks, route-budget checks, preview review, and, where
  required, Pending Action approval.
- Unbounded consumption. Long histories, retrieval fan-out, generated routes,
  streams, TTS, model calls, and retries must have budgets, latency limits,
  idempotency keys, and cancellation/recovery states. No UI projection or
  browser tail should become the unbounded source of truth.

Controls already present in the current slice:

- Retrieved memory/history is annotated as
  `trust_boundary=retrieved_untrusted_evidence` and inspected by
  `conversation_safety.inspect_retrieved_text(...)`.
- `conversation_safety.classify_action_risk(...)` produces the shared
  `adaos.conversation.action_risk.v1` contract.
- Builder preview policy now includes action-risk evidence and blocks
  auto-apply when filesystem, network, device-control, credential, or
  cross-node risk requires review.
- Builder draft/patch Pending Actions carry source refs and action-risk
  metadata.
- Conversation export, soft redaction, and hard delete write durable
  node-local audit events with counts and reasons.
- Initial golden datasets cover `general`, `conversation_companions`,
  `builder`, and `teacher` conversation flows.

Open controls:

- Consent grant/revoke UI and durable consent audit events.
- Runtime enforcement of action-risk gates for every filesystem, network,
  device-control, credential, cross-node, and destructive action outside
  Builder preview.
- Policy inspector UI that explains context sources, trust boundaries,
  memory access, redaction state, action-risk class, approval identity, and
  denial reason for a turn.
- Full migration gate that runs the golden suite before removing compatibility
  Voice projections or enabling broad generated-skill rollout.

## Verifiable Milestones and User Stories

These milestones are phrased as acceptance scenarios rather than implementation
tasks. They are intended for manual testing, automated golden conversations,
and control-group validation.

### UC1. Start Companion Dialog From Global Voice

User story: as a user, I say or type "let's talk" / Russian "pogovorim" in the global
dialog shell and enter a companion conversation without opening a separate app.

Acceptance:

- Input is recorded as one user turn with `turn_trace_id`, `dialog_channel_id`,
  and `conversation_id`.
- The active channel switches to `conversational`.
- `conversation_companions.start` runs and the returned message is visible in
  the same global chat shell.
- The message is appended through the conversation service or compatibility
  bridge with idempotency.
- The general channel history remains unchanged except for optional audit refs.
- A repeat of the same request does not duplicate the assistant reply.

### UC2. Continue Skill-Owned Conversation

User story: after starting `conversational`, I type an ordinary message such as
"give advice" and the active character answers without requiring a fresh intent
match for every phrase.

Acceptance:

- The dialog runtime resolves the turn to the active `conversational`
  conversation before NLU dispatch.
- If no higher-priority exit/switch intent is found, the owner policy routes the
  turn to `conversation_companions.talk`.
- Recent turns and selected memory are passed as a bounded context packet.
- The reply carries `agent_id` for the active character.
- The visible tail and canonical ledger stay consistent.

### UC3. Switch Agent Inside One Conversation

User story: as a user, I say "call Nika" and future companion replies use
Nika without creating a new dialog channel.

Acceptance:

- The channel remains `conversational`.
- `active_agent_id` changes in conversation state.
- The turn and reply record the selected `agent_id`.
- Agent-specific memory remains scoped to
  `agent:conversation_companions:nika` or equivalent owner/scope metadata.
- Returning to another agent is a state change, not a transport or channel
  change.

### UC4. Correct Character Profile Safely

User story: as a user, I say "be shorter" and the active character adapts
future replies without silently writing broad global memory.

Acceptance:

- The correction is interpreted as a profile/memory update for the active
  skill/agent scope.
- The memory item has source refs, confidence, consent, visibility, and owner.
- If the update would become long-term or cross-skill reusable, it requires an
  explicit policy or Pending Action.
- Diagnostics can show that a profile changed without exposing private dialog
  text.

### UC5. Switch Back To General Without Mixing Context

User story: as a user, I switch from `conversational` back to `general` and ask
a normal AdaOS command.

Acceptance:

- The active channel changes to `general`.
- The visible tail changes to the general conversation.
- Companion history is not included in the general prompt unless explicitly
  allowed by policy.
- General command routing remains compatible with existing NLU behavior.

### UC6. Builder Has Isolated Working Context

User story: as a user, I open Builder and discuss a skill draft without
polluting the general assistant or companion histories.

Acceptance:

- Builder has a dedicated conversation and channel entry.
- Builder receives a context packet with draft refs, validation evidence,
  Pending Action refs, and recent Builder turns.
- Builder does not receive raw companion or general history by default.
- Apply/review decisions use Pending Actions or explicit evidence refs, not
  free-form chat text as the durable approval source.

### UC7. NLU Teacher Clarifies As A Conversation

User story: when AdaOS cannot safely classify a command, Teacher asks a
clarification question and later resumes the original repair path.

Acceptance:

- The clarification is represented as a `teacher` conversation or thread.
- Allowed answers, rejected alternatives, missing slots, and resolution path
  are stored as structured state.
- A voice yes/no answer and a Pending Action response update the same domain
  object.
- The original user turn, candidate, preview, apply result, and final response
  share a trace chain.

### UC8. Endpoint Audio Dialog Mode Uses The Same Runtime

User story: an endpoint audio `dialog` session sends transcripts into the same
conversation model as browser typed input.

Acceptance:

- Final transcripts resolve to a conversation before NLU or skill dispatch.
- Audio session refs remain transport/session metadata, not conversation ids.
- Barge-in, no-input, and interruption are dialog policy states, not STT
  implementation details.
- Delivery can target browser, voice, notification, or Telegram according to
  routing policy.

### UC9. Long Conversation Retrieval Stays Bounded

User story: after a long companion or Builder conversation, the next reply
remains fast and relevant.

Acceptance:

- Runtime never reads the full transcript into a prompt.
- Recent turns, segment summaries, memory items, and evidence refs are selected
  under explicit token/time budgets.
- Retrieval diagnostics show selected and skipped sources.
- If FTS, summaries, or model-backed retrieval are unavailable, the runtime
  degrades deterministically.

### UC10. Federated Memory Search Is Policy Checked

User story: Builder on one node can discover relevant fragments from another
node only when policy allows it.

Acceptance:

- The requester sends a scoped federated retrieval request, not a remote SQL
  query.
- The target node applies local owner, user, retention, and consent policy.
- The target returns fragments, summaries, refs, scores, and denials.
- The requester records partial results and timeouts as evidence.

## Parallel Development Tracks

The roadmap can be split into independent tracks after Phase 0 contracts are
stable enough for interfaces to stop moving daily.

| Track | Owns | Can Start After | Must Not Block On | Primary Milestones |
| --- | --- | --- | --- | --- |
| A. Contracts and Schemas | conversation, turn, dialog act, memory, response envelope, manifest schema | Phase 0 start | UI polish, vector search | UC1-UC4 schema review |
| B. Node Store and Retrieval | SQLite tables, WAL, append ledger, FTS, summaries, context packets | minimal Phase 0 schemas | full SDK, Builder migration | UC1, UC2, UC9 |
| C. Dialog Runtime and Policy | tracker, frames/forms, repair states, channel policy, owner dispatch | Phase 0 turn contract | FTS/vector search, final UI | UC1-UC5, UC7 |
| D. Global Dialog UI and Transports | Voice shell, channel selector, `dialog.user_message`, Telegram/audio routing | dialog-channel contract | full memory extraction | UC1, UC5, UC8 |
| E. SDK and Skill Migration | `adaos.sdk.conversation`, `adaos.sdk.memory`, manifest support, generated-skill templates | minimal store + runtime context | Builder migration | UC2-UC4 |
| F. Builder and Teacher Migration | dedicated Builder/Teacher conversations, Pending Action links, evidence refs | minimal store + tracker + context packets | vector search, federation | UC6, UC7 |
| G. Evaluation and Observability | traces, golden conversations, metrics, diagnostics, performance tests | first vertical slice | federation | all UCs |
| H. Federation and Privacy | node-to-node retrieval, audit, export/delete/redaction, consent | local store + policy model | Builder UI polish | UC10 |

Synchronization points:

- Phase 0 schemas are the first integration gate.
- Vertical Slice A is the first runtime gate.
- Context packet contract is the Builder/skill-runtime gate.
- Dialog-level golden tests are the quality gate before broad migration from
  `voice_chat`.
- Export/delete/redaction and memory-write consent are required before broad
  long-term personalization.

## Implementation Roadmap

Priority markers:

- `[must]`: required for the professional target architecture and before broad
  LLM Builder / skill-runtime adoption
- `[should]`: important hardening or scale work after the first safe slice
- `[could]`: valuable extension that should not block the initial architecture

### Vertical Slice A. Companion Dialog Through Global Shell

This is the first practical slice. It validates the architecture with minimal
depth across several phases.

- [x] `[must]` Keep the current Voice UI usable while adding neutral
  `dialog.user_message` semantics behind it.
- [x] `[must]` Preserve `dialog_channel_id`, `conversation_id`, `request_id`,
  `turn_trace_id`, and transport metadata through NLU and skill dispatch.
- [x] `[must]` Add a minimal active dialog-channel registry for
  `conversational` in the Voice compatibility path.
- [x] `[must]` Add a browser-visible active dialog-channel projection for
  `general` and `conversational`, including active owner/agent metadata.
- [x] `[must]` Add a static pilot named-agent registry for `general` plus the
  `conversation_companions` agents, so addressed agent names can switch the
  active channel and delegate to the owning skill before NLU/Teacher fallback.
- [x] `[must]` Persist the active dialog-channel registry for `general` and
  `conversational`; extension to `builder` and future skill-owned channels is
  still pending.
- [x] `[must]` Replace the static pilot registry with a persisted,
  manifest-fed agent/channel registry that supports skill-declared aliases,
  capabilities, voice profiles, and policy. Current implementation seeds the
  registry from `conversation_companions.skill.yaml`; schema validation and
  dynamic marketplace registration remain pending.
- [x] `[must]` Add a minimal conversation ledger or compatibility service that
  can append user and assistant turns idempotently.
- [x] `[should]` Replace the bounded process-local Voice history cache with
  `Еще истории` pagination backed by the node-local conversation ledger.
- [x] `[must]` Make `conversation.start` switch to `conversational`, run
  `conversation_companions.start`, and show the returned message.
- [x] `[must]` Route active `conversational` Voice turns directly to
  `conversation_companions.talk` while preserving explicit exit/general
  commands.
- [x] `[must]` Move in-channel agent switching by addressed name under the
  `conversation_companions` owner policy and project the active agent to the
  browser shell.
- [x] `[should]` Project pilot `gender`/`voice`/`voice_profile`/`icon` hints from
  core and `conversation_companions` into chat messages so the browser can
  label the speaking agent and choose a more appropriate speech-synthesis
  voice.
- [x] `[should]` Keep the global app header compact by showing the active
  agent chip next to the listen button and leaving explicit channel selection
  inside the Voice dialog surface.
- [x] `[should]` Add conservative pre-NLU autocorrection for common local text
  input mistakes while preserving the original text in diagnostics metadata.
- [x] `[must]` Move in-channel profile-correction commands fully under the
  `conversation_companions` owner policy.
- [x] `[must]` Keep `voice_chat.messages` as a compatibility projection, not
  the canonical design.
- [x] `[must]` Add a golden conversation test for "pogovorim" -> reply ->
  follow-up -> switch character -> style correction -> back to `general`.
- [x] `[should]` Add initial diagnostics showing active channel, conversation,
  owner, active agent, and projected memory scopes.
- [x] `[should]` Extend diagnostics with last policy decision, turn trace, and
  materialization path.

### Phase 0. Contract Freeze

- [x] `[must]` Define `Conversation`, `ConversationMessage`,
  `ConversationThread`, `DialogChannel`, `MemoryItem`,
  `ConversationSegment`, and `ConversationRoutingPolicy` schemas. The canonical
  Python contract is `adaos.domain.conversation`.
- [x] `[must]` Define `DialogTurn`, `DialogAct`, `DialogFrame`,
  `DialogPolicyState`, `ResponseEnvelope`, and `TurnTrace` schemas.
- [x] `[must]` Define actor ids for `core:*`, `skill:*`,
  `agent:<skill_id>:<agent_id>`, users, nodes, endpoints, and transports.
- [x] `[must]` Define `created_by` / `initiator` shape for conversations,
  threads, messages, memory items, and proactive prompts.
- [x] `[must]` Define node-local storage policy: physical store is node-owned,
  logical owner is core/skill/agent, access is policy-checked.
- [x] `[must]` Define `history_policy`, `retrieval_policy`,
  `personalization_policy`, `repair_policy`, `response_policy`, and
  `retention_policy`.
- [x] `[must]` Define projection rules for `data.dialog` and WebIO stream
  receivers so Yjs carries only compact active tails.
- [x] `[should]` Add manifest schema support for `conversations`, `history`,
  `retrieval`, `dialog_channel`, `repair`, `response`, form/frame, and agent
  declarations. Runtime skill schema now accepts a `conversation` contract with
  channel, history, retrieval, repair, response, memory, forms/frames, and
  agent declarations.
- [ ] `[should]` Add SDK design docs for `adaos.sdk.conversation` and
  `adaos.sdk.memory`.
- [ ] `[could]` Define optional vector-index metadata while keeping vector
  storage out of the MVP contract.

### Phase 0.25. Dialog Runtime and Tracker Contract

- [x] `[must]` Define the Dialog Runtime as the owner of turn lifecycle,
  current conversation resolution, active frame, repair state, response
  materialization, and trace continuity. NLU, Voice, Builder, and skills may
  provide evidence or actions, but Dialog Runtime is the sole authority that
  advances `DialogTurn`, selects the active `DialogFrame`, records repair
  state, invokes response materialization, and appends/finishes `TurnTrace`.
- [x] `[must]` Define repair states for no-match, no-input, disambiguation,
  correction, interruption, cancel, resume, and parameter change. These are the
  canonical `DialogRepairState` values: `no_match`, `no_input`,
  `disambiguation`, `correction`, `interruption`, `cancel`, `resume`, and
  `parameter_change`; `none` means normal turn handling.
- [x] `[must]` Define task-frame/form semantics for slot collection,
  validation, preview, confirmation, and bounded user answers. A
  `DialogFrame(kind="slot_collection")` owns required slots, validation state,
  preview payload, confirmation policy, retry/answer budgets, and cancellation
  behavior until completed, cancelled, or interrupted.
- [x] `[must]` Define how NLU outputs are consumed as evidence by the Dialog
  Runtime rather than treated as final dialog decisions. NLU intent/entity
  results are `DialogAct` evidence with confidence and source refs; Dialog
  Runtime combines them with active channel, active frame, policy, memory, and
  user corrections before selecting an action.
- [x] `[must]` Define response rendering targets: text tail, speech text, card,
  Pending Action, notification, Builder evidence view, and transport-native
  affordance. These are canonical `ResponseTarget` values and are materialized
  through `ResponseEnvelope`, never by making a skill write directly to a
  transport-specific history store.
- [x] `[should]` Add a first policy-inspection projection for one turn:
  selected channel, conversation, owner, action target, routing reason, and
  response renderer are available in `data.dialog.last_turn_trace`.
- [x] `[should]` Promote the projection into a small policy-inspection API that
  also includes NLU evidence, frame state, repair state, and replay/debug links.
  `conversation_policy.inspect_turn_policy(...)` and
  `inspect_last_turn_policy(...)` now normalize selected channel, agent, tool,
  renderer, routing reasons, fallback/materialization/repair state, and source
  refs from canonical turn traces.
- [ ] `[could]` Add a simulator API that replays golden conversations without a
  browser or real transport.

### Phase 0.5. Voice Compatibility Stabilization

- [x] `[must]` Make dispatcher publish a skill tool result `message` to the
  active Voice/dialog route when the result has not already been materialized.
- [x] `[must]` Preserve existing `_meta.webspace_id`, `route_id`,
  `target_node_id`, and `request_id` through current Voice NLU and skill tool
  calls.
- [x] `[must]` Preserve current `dialog_channel_id` through the Voice
  compatibility Dialog Runtime and skill tool calls.
- [x] `[must]` Preserve future canonical dialog metadata through neutral
  `dialog.*` events, NLU evidence, and skill tool calls. The first
  implementation keeps `route_id=voice_chat` as the compatibility route while
  carrying `dialog_event_kind`, `canonical_event_kind`, and
  `input_event_kind=dialog.user_message`.
- [x] `[must]` Add diagnostics when a skill action returns `ok` and `message`
  but no visible output is published. Dialog tool dispatch now probes
  `io.out.chat.append` during the skill call and records
  `materialized` / `unmaterialized` plus renderer diagnostics in
  `TurnTrace` and `data.dialog.last_turn_trace`.
- [x] `[must]` Add tests for `conversation.start` from Voice producing a
  visible reply and activating the companion dialog channel.
- [x] `[must]` Add `turn_trace_id` to the Voice compatibility path and preserve
  it through NLU action outcomes.
- [x] `[should]` Keep `voice_chat.messages` as the compatibility tail while
  introducing neutral `dialog.*` events and metadata.
- [x] `[could]` Add a temporary Voice debug panel that shows NLU logs,
  runtime flags, active dialog/memory projection, and current owner/agent.
- [x] `[could]` Add last dispatch result and renderer/materialization status
  to the Voice debug panel through `data.dialog.last_turn_trace`.

### Phase 1. Node Conversation/Memory Store

- [x] `[must]` Add a node-local conversation/memory service backed by the
  existing node SQLite database.
- [x] `[must]` Enable WAL and define tables for conversations, dialog channels,
  messages, memory items, agent registry, turn traces, and idempotency. Segments
  and delivery attempts remain pending.
- [x] `[must]` Implement append-only message writes with per-conversation
  monotonic `seq`.
- [x] `[must]` Implement owner-scoped reads and writes for core, skills, and
  skill agents at the service/API boundary. Full policy enforcement remains
  pending.
- [x] `[must]` Implement idempotency for inbound platform message ids and skill
  action result materialization at the ledger write level.
- [x] `[must]` Implement retention/redaction fields even if the first pass only
  enforces conservative defaults. Conversations, messages, and memory items now
  carry `retention_class`, `retention_until`, `redaction_state`, `redacted_at`,
  and `redaction_reason`; export/delete enforcement remains a later hardening
  flow.
- [x] `[must]` Publish bounded Yjs/WebIO projections from the node store for
  active browser consumers.
- [x] `[should]` Add FTS5 indexes for messages, segment summaries, and memory
  items. `conversation_store` now maintains FTS5 tables for messages, memory,
  and segment summaries, exposes rebuild/health APIs, and falls back to LIKE
  search when FTS is unavailable.
- [x] `[should]` Add background segment summarization jobs with bounded queue
  and failure diagnostics. `enqueue_segment_summary_job(...)`,
  `process_segment_summary_jobs(...)`, and `segment_summary_job_health(...)`
  provide durable queued/running/completed/failed state, bounded queue depth,
  retry attempts, and retrieval-health degradation reasons.
- [x] `[should]` Add projection recovery from node store when WebIO/Yjs tail is
  empty or stale. `conversation_store.recover_projection_from_store(...)`
  detects empty, mismatched, and stale visible-tail projections, restores from
  the canonical ledger, and annotates `data.dialog.visible_tail.recovery` with
  the recovery reason/source for diagnostics.
- [ ] `[could]` Add optional embedding queue and vector-index adapter behind a
  feature flag.

### Phase 2. Retrieval and Context Packets

- [x] `[must]` Implement budgeted context assembly:
  recent turns + relevant segments + memory items + evidence refs. First pass
  is `adaos.services.conversation_context.build_context_packet(...)`: it
  assembles recent ledger messages, node-local memory items, evidence refs, and
  explicit diagnostics; segment summaries are represented as an unavailable
  deterministic fallback until Phase 2 `should` indexing lands.
- [x] `[must]` Add strict token/message/time budgets and deterministic fallback
  when FTS, summaries, or model-backed retrieval are unavailable. Context
  packets use local token estimation, bounded message/memory counts, a time
  budget, and stable fallback markers (`fts_unavailable`,
  `summaries_unavailable`, `semantic_retrieval_unavailable`).
- [x] `[must]` Implement cross-owner memory reuse as deny-by-default. Context
  assembly refuses `memory_owner != requester_owner` unless the caller passes
  an explicit policy override.
- [x] `[must]` Attach source refs, confidence, consent, and visibility to
  memory items. Memory rows expose `source_ref`, `confidence`,
  `consent_state`, and policy-derived `visibility` in store, SDK, and context
  packets.
- [x] `[must]` Add a memory-write policy that distinguishes immediate
  conversation facts, skill-scoped preferences, agent-scoped preferences, and
  global reusable user memory. `memory.write_policy(...)` maps these classes to
  canonical `conversation`, `skill_user`, `agent_user`, and `global_user`
  scopes with default consent and reuse policies.
- [x] `[should]` Add retrieval diagnostics: selected sources, skipped sources,
  estimated tokens, latency, and policy denials. First pass diagnostics are
  embedded in every context packet; richer scoring waits for FTS/summary work.
- [x] `[should]` Add summary compaction for long conversations without losing
  message range refs. `conversation_store.compact_conversation_history(...)`
  now rebuilds durable segment summaries for the compacted range, returns
  segment `source_refs`, keeps a raw tail window, and leaves the canonical
  message ledger intact.
- [x] `[should]` Add golden retrieval tests for long companion, Builder, and
  Teacher conversations. `tests/test_conversation_context.py` now includes
  regression coverage for long companion segment/search retrieval, Builder
  thread-scoped context isolation, and Teacher owner-scoped memory/history
  retrieval.
- [ ] `[could]` Add semantic search / vector retrieval once FTS and summaries
  are stable.

### Phase 3. SDK and Skill Runtime

- [x] `[must]` Implement `adaos.sdk.conversation.current()`,
  `open(...)`, and `get(...)` as the first low-level SDK facade. Manifest-driven
  default conversation creation remains pending.
- [x] `[must]` Implement `chat.send`, `chat.ask`, `chat.history`,
  `chat.context`, and `chat.start_thread`. `adaos.sdk.chat` now wraps the
  canonical ledger, bounded history, context packets, durable threads, and
  visible response materialization.
- [x] `[must]` Implement structured `ResponseEnvelope` handling so generated
  skills can return user-visible content without directly calling
  `io.out.chat.append`. `conversation_response.materialize_response(...)`
  accepts `ResponseEnvelope`-style dict/dataclass values and legacy tool
  `message` results, then publishes chat/speech targets and persists the
  ledger record.
- [x] `[must]` Implement scoped memory helpers:
  `memory.search`, `memory.remember`, `memory.list`, and `memory.forget`.
  The first pass uses node-local store search plus soft-redaction by default;
  FTS scoring remains Phase 2 `should` work.
- [x] `[must]` Propagate current conversation context into skill tool calls.
  Router dialog turns and NLU skill-tool actions now include
  `conversation_context` in the payload when a canonical conversation id is
  available.
- [x] `[must]` Teach LLM skill-development docs to prefer conversation/memory
  APIs over `io.out.chat.append` and direct `skill_memory` transcript storage.
- [x] `[should]` Add generated-skill templates for skill-owned conversations,
  multi-agent skill conversations, and bounded `chat.ask` flows. The default
  skill template now declares a skill-owned dialog channel, two starter agents,
  node-local history/retrieval/memory policy, and SDK-based `chat`,
  `ask_for_details`, and consent-gated `remember_preference` tools; Builder
  rewrites template ids to the generated artifact id.
- [x] `[should]` Add lint/validation warnings for skills that store
  user-visible transcript history in arbitrary files or use conversation/memory
  SDK APIs without declared manifest policy. `SkillValidationService` now warns
  when a skill uses `adaos.sdk.conversation` without a `conversation`
  declaration or uses `adaos.sdk.memory` without a skill-local memory route /
  conversation memory policy.
- [ ] `[could]` Add SDK helpers for memory extraction proposals that require
  user confirmation before long-term storage.

### Phase 4. Transport and Global Dialog Integration

- [x] `[must]` Make Voice/browser typed input resolve to conversations before
  NLU or skill dispatch in the Voice compatibility path.
- [x] `[must]` Convert `voice.chat.user` into a compatibility alias for neutral
  `dialog.user_message`.
- [x] `[must]` Add active dialog-channel registry per webspace.
- [x] `[must]` Add browser channel selector support for `general` and
  `conversational`.
- [x] `[should]` In the Voice compatibility path, resolve pilot addressed-agent
  names from a core-owned registry and switch the visible channel/agent
  projection accordingly.
- [x] `[must]` Extend browser channel selector support to `builder` and
  dynamically declared skill-owned channels.
- [x] `[must]` Make browser chat panels subscribe to `data.dialog` /
  conversation projections instead of transport-specific chat state.
- [x] `[should]` Publish `data.dialog.visible_tail` from the canonical node
  ledger, so modal and widget chat surfaces can restore the active conversation
  after reload without treating `voice_chat.messages` as the source of truth.
- [x] `[should]` Keep "load earlier history" compatible with both legacy
  `voice_chat` and canonical `dialog.visible_tail` projections during the
  migration window.
- [x] `[should]` Keep the browser agent chip sourced from
  `data.dialog.active_agent` and the active channel projection, including
  `builder` and dynamically declared skill-owned channels.
- [ ] `[should]` Make Telegram inbound messages resolve to conversations before
  NLU or skill dispatch.
- [ ] `[should]` Make endpoint audio dialog mode resolve to conversations
  before NLU or skill dispatch.
- [ ] `[should]` Record delivery status per transport attempt.
- [ ] `[could]` Add deep links that open a specific conversation/thread from a
  notification, Pending Action, or Telegram command.

### Phase 5. Surface Dispatch and Conversation Policies

- [x] `[must]` Route `general` conversations to the default assistant/NLU
  surface.
- [x] `[must]` Route `skill` conversations to their logical skill owner through
  owner-scoped tool dispatch.
- [x] `[must]` Add channel policies for entry intents, default tools, fallback
  behavior, and exit/switch intents.
- [x] `[must]` Implement Dialog Runtime handling for no-match, no-input,
  interruption, cancel, resume, correction, and parameter-change states.
- [x] `[must]` Implement task-frame/form routing for multi-turn parameter
  collection and validation.
- [x] `[must]` Add `conversation_companions` as the first multi-agent skill
  conversation pilot: "let's talk" enters `conversational`, unmatched turns
  route to `talk`, and exit/switch commands return to `general`.
- [x] `[must]` Support multiple pilot agents in one skill conversation with
  `active_agent_id` and browser-visible active-agent projection.
- [x] `[should]` Attach pilot agent gender/voice hints to projections and
  emitted chat messages for browser speech synthesis.
- [x] `[must]` Persist agent-scoped memory as canonical `MemoryItem` records.
- [x] `[should]` Persist `conversation_companions` profile corrections as
  deterministic agent-scoped upserts, while keeping skill-local profile storage
  as a compatibility cache.
- [x] `[should]` Attach repair-state and active-frame metadata to
  owner-scoped tool payloads, so the selected skill can handle correction,
  cancellation, and parameter updates without re-parsing transport state.
- [ ] `[should]` Move legacy semantic fallback out of
  `voice_chat_skill.handle_text` into conversation owner/surface policies.
- [ ] `[should]` Add explicit fallback when a surface or owning skill is
  unavailable.
- [ ] `[should]` Add response-planning rules for text vs speech vs card vs
  Pending Action, so skills do not choose transport-specific rendering by
  default.
- [ ] `[could]` Add operator-visible policy inspection for one conversation:
  owner, channel, retrieval policy, memory scopes, and last dispatch.

Important lacunae found during Phase 4/5 implementation:

- [x] `[must]` Persist active `DialogFrame` state in the node DB before
  enabling multi-process runtime or restart-resumable forms. The runtime now
  writes the active frame to `conversation_dialog_frames`, restores it after a
  process-local cache loss, and clears the durable row when a frame completes
  or is cancelled.
- [x] `[must]` Promote dynamic channel declarations from ad-hoc persisted
  rows to manifest-backed validation, including owner, default tool, policy,
  and renderer capabilities. The first implementation scans skill manifests
  from the workspace and packaged templates, seeds node-local conversations and
  dialog channels, and registers declared agents for addressed-name routing.
- [ ] `[should]` Move addressed-agent payload forwarding policy into the skill
  manifest. The compatibility slice keeps `conversation_companions` receiving
  the original user text while Builder receives the command with the agent
  address stripped.
- [x] `[should]` Harden conversation-store schema initialization for local
  debugging and runtime DB replacement. `ensure_schema()` now verifies the
  marker dialog-channel table before trusting the process-local schema cache.
- [ ] `[should]` Add a first-class policy inspector UI for the last turn:
  selected channel, selected agent, owner, fallback path, repair state,
  frame id, renderer, and memory scopes.

### Phase 6. Builder and NLU Teacher Migration

- [x] `[must]` Create the default Builder conversation on first Builder entry.
  The current slice creates the `builder` conversation/channel when Builder
  API draft/preview flows run and reuses the same ids as the browser Builder
  channel.
- [x] `[must]` Make LLM Builder consume context packets instead of raw chat
  history. Builder draft and preview payloads now include a budgeted
  `context_packet` tied to the Builder conversation; later LLM Builder calls
  must use that packet rather than ad hoc transcript reads.
- [x] `[must]` Link Builder drafts and preview validation evidence to
  conversation refs. `builder.draft.json` stores `links.conversation`, and
  preview bundles expose `conversation`, `context_packet`, and `source_refs`.
- [x] `[must]` Link Builder Pending Actions to conversation/thread refs when
  Builder approval/apply actions become first-class Pending Actions. The
  Builder skill now publishes draft and patch review Pending Actions with
  `domain_ref` and `metadata.source_refs` containing conversation, thread,
  trace, request, draft, session, scenario, and patch ids where available.
- [x] `[must]` Move NLU Teacher clarification sessions into `kind=teacher`
  conversations. The first pass creates a canonical Teacher conversation and
  per-request thread while keeping the legacy Teacher read model as a
  projection/cache.
- [x] `[must]` Link candidate confirmations and Pending Actions to Teacher
  conversations and source message ids.
- [ ] `[should]` Preserve approval/apply evidence outside plain chat messages.
- [x] `[must]` Add Builder workspace binding to context packets and browser
  projections: source webspace id, paired `*-dev` webspace id, workbench
  scenario id, and active draft id. The current implementation adds
  canonical Builder topic/thread refs through `conversation_links`,
  `BuilderWorkbenchService.dialog_widget_config`, `/api/builder/workbench/dialog-widget`,
  and `builder_skill.attach_dialog_widget`; embedded Voice Chat sends
  `conversation_id`, `thread_id`, `topic_id`, source webspace, dev webspace,
  runtime scenario, and active draft metadata with every turn.
- [ ] `[should]` Add acceptance tests for Builder through browser and Telegram
  transport.
- [ ] `[should]` Add tests for multi-turn NLU correction with separate
  `general`, `teacher`, and `builder` contexts.
- [ ] `[could]` Add Builder repair conversations that span generated files,
  validation runs, CI logs, and user review.

Important lacunae found during Phase 6 implementation:

- [x] `[must]` Make the NLU Teacher read model reconstructable from
  `kind=teacher` ledger records, then treat `data.nlu_teacher` as a projection
  rather than a second source of truth. Teacher events are now mirrored into
  the canonical teacher conversation ledger, and
  `write_teacher_projection_from_ledger()` can rebuild `data.nlu_teacher`
  threads, workbench signals, items, candidates, revisions, and LLM logs from
  ledger messages.
- [x] `[must]` Add Builder approval Pending Actions with `source_refs` before
  enabling browser apply/approve flows. Current responses route to
  `builder.pending_action.response`; applying approved changes and writing a
  release/rollback record remains in the Builder runtime roadmap.
- [x] `[must]` Make Builder history and context topic-aware for the first
  practical slice. `conversation_context.build_context_packet(...)`,
  `adaos.sdk.conversation.context(...)`, Voice snapshot/load-more, and
  ChatWidget history requests now accept/pass `thread_id`, so Builder project
  chats can be filtered by active draft/scenario thread while still sharing the
  node-local conversation ledger.

### Phase 7. Subnet Federation

- [x] `[must]` Define a policy-checked federated search/read request shape for
  node-to-node conversation and memory retrieval. The first contract is
  `adaos.conversation_federated_retrieval.request.v1`, normalized by
  `conversation_federation.normalize_request()`, with requester owner, scopes,
  limits, target nodes, and deny-by-default cross-owner policy.
- [x] `[must]` Keep federation timeout-bound and partial-result-friendly.
  `conversation_federation.execute_local_request()` enforces per-node timeout
  budgets and returns `status=partial` plus denials instead of blocking.
- [x] `[must]` Return fragments, summaries, refs, and scores, not direct remote
  database access. The response schema returns `fragments[]` with text,
  summary, score, source refs, and `remote_sql=false`.
- [x] `[should]` Add node-local retrieval health and index-status diagnostics.
  `conversation_store.retrieval_health_report(...)` now reports scoped message,
  segment, and memory counts, FTS index health, segment-summary status, and
  degraded reasons for diagnostics surfaces.
- [x] `[should]` Add cross-node query audit events with requesting actor,
  target node, owner scope, and denied/returned counts.
  `conversation_federation.execute_local_request(...)` now records
  `conversation.federated_retrieval.audit.v1` audit events with request id,
  requester, node targets, owner/memory scopes, returned/denied counts, denial
  reasons, and `remote_sql=false`.
- [ ] `[should]` Add the actual node-to-node transport adapter for federated
  retrieval after the local executor contract is stable.
- [ ] `[could]` Add subnet-level search UI after local-node retrieval and policy
  gates are stable.

### Phase 8. Cleanup and Hardening

- [x] `[must]` Remove public dependency on `voice_chat` as the canonical chat
  state. Public docs now describe `adaos.sdk.chat` / response envelopes,
  node-local conversation ledger, and `data.dialog.visible_tail` as the
  default path; `voice_chat` is documented as a compatibility projection.
- [x] `[must]` Replace `route_id == "voice_chat"` semantic checks with
  conversation, channel, and surface routing. Router semantic fallback checks
  now use `_is_dialog_surface_route(...)`, which considers canonical dialog
  event kind, conversation id, dialog channel id, and only then the legacy
  Voice route for compatibility.
- [x] `[must]` Update SDK IO docs to make conversation/memory APIs the default
  path. The LLM skill guide and WebIO overview now direct generated skills to
  `chat.send`, response envelopes, and scoped memory helpers.
- [x] `[must]` Keep agent/channel handoff from blocking semantic routing on
  UI projection writes. Addressing the core `general` agent now deactivates
  the skill-owned channel and forwards the remaining text to NLU before
  waiting on compatibility Voice tail materialization.
- [ ] `[should]` Retire direct writes to transport-specific chat projections.
- [ ] `[should]` Add export/delete/redaction flows for conversation and memory
  records.
- [ ] `[should]` Add safety tests for prompt injection through retrieved
  memory/history and for cross-owner memory denial.
- [ ] `[should]` Add dialog-level golden conversations and metrics for repair
  rate, fallback rate, success rate, latency, and context budget.
- [ ] `[should]` Add performance soak tests for long conversations, FTS,
  summaries, and active WebIO/Yjs projections.
- [ ] `[could]` Add model-backed memory extraction and summarization quality
  evaluation datasets.

### Phase 9. Quality and Evaluation Gate

- [x] `[must]` Define a conversation evaluation result schema for golden
  dialogs, trace-derived metrics, routing expectations, repair expectations,
  latency, fallback rate, and context-budget evidence. The first deterministic
  schema is `adaos.conversation.eval.result.v1`.
- [x] `[must]` Add a reusable evaluator that can score a stored conversation
  ledger plus durable turn traces without depending on the browser surface.
  `conversation_eval.evaluate_golden_conversation()` scores ledger messages,
  turn traces, required/forbidden text, required agents/channels, success
  rate, fallback rate, repair rate, no-match rate, and latency summary.
- [x] `[must]` Add first checked-in golden datasets for `general`,
  `conversation_companions`, `builder`, and `teacher` flows. The initial
  fixtures cover general no-match repair, companion agent handoff, Builder
  review handoff, and Teacher candidate repair through
  `tests/fixtures/conversation/*`.
- [x] `[must]` Add the first Builder first-idea preview/correction fixture to
  the migration-gated suite. It covers phrase-level entry, draft preview,
  `webui.json` evidence, and follow-up patching in one Builder topic.
- [ ] `[must]` Broaden the golden datasets to include companion profile
  correction, no-input repair, Builder apply/reject handoff, memory-write
  consent, long-context retrieval, and Builder validation-failure repair.
- [x] `[must]` Make golden evaluation a migration gate before broad removal of
  compatibility Voice projections or broad generated-skill rollout.
  `conversation_eval.run_golden_migration_gate()` loads checked-in golden
  datasets, verifies the required baseline suite, and returns
  `adaos.conversation.eval.migration_gate.v1` with blocking fixture failures.
- [ ] `[should]` Add optional model-backed graders for answer quality,
  unsupported claims, memory-write quality, and persona consistency after the
  deterministic evaluator is stable.
- [x] `[should]` Publish evaluation summaries into diagnostics / Pending
  Actions so Builder repair tasks can link failing traces and fixtures.
  `conversation_eval.publish_eval_repair_pending_action(...)` now turns a
  failed golden result or migration gate into a `builder.eval_repair.review`
  Pending Action with `adaos.conversation.eval.repair_summary.v1`,
  dataset/source refs, and action-risk metadata.

### Phase 10. Security, Privacy, and Governance

- [x] `[must]` Add an LLM threat model aligned with prompt injection, sensitive
  information disclosure, excessive agency, insecure output handling, and
  unbounded consumption risks. This document now defines the threat classes,
  current controls, and open controls for Dialog Runtime, Builder, generated
  skills, memory retrieval, and assistants.
- [x] `[must]` Treat retrieved memory/history as untrusted evidence by default:
  retrieved text must be separated from system/developer instructions and
  flagged when it contains instruction-like or exfiltration-like content. The
  first implementation annotates context-packet messages and memory with
  `trust_boundary=retrieved_untrusted_evidence` and
  `adaos.conversation.retrieved_evidence_safety.v1` diagnostics.
- [x] `[must]` Add first safety tests for prompt injection through
  memory/history, cross-owner memory denial, and redaction filtering.
- [x] `[must]` Add action-risk escalation tests for filesystem, network,
  device-control, credential, and cross-node effects. The first contract is
  `adaos.conversation.action_risk.v1` from
  `conversation_safety.classify_action_risk(...)`.
- [x] `[must]` Add first export/delete/redaction APIs for conversations,
  messages, conversation-scoped memory items, and traces.
  `conversation_store.export_conversation(...)` and
  `redact_conversation(...)` support redaction-aware export, soft redaction,
  and hard delete for a conversation bundle.
- [x] `[must]` Back export/delete/redaction operations with durable audit
  events. `conversation_audit_events` records export, soft redaction, and hard
  delete events with counts, reasons, and operation metadata.
- [x] `[must]` Add user-visible consent controls and durable consent
  grant/revoke audit events for reusable global, core, skill, and agent memory.
  The first service/SDK controls are `memory.record_consent(...)` and
  `conversation_store.record_memory_consent(...)`, which update matching memory
  items and append `conversation.memory.consent.v1` audit events.
- [x] `[must]` Add action risk classes for tools with filesystem, network,
  device-control, credential, or cross-node effects.
- [x] `[must]` Wire action-risk classes into Builder preview and Builder
  Pending Action approval gates. Preview policy includes
  `adaos.builder.action_risk_review.v1` evidence and blocks auto-apply for
  mandatory action-risk classes.
- [x] `[must]` Enforce action-risk approval gates in the runtime path before
  executing filesystem, network, device-control, credential, destructive, or
  cross-node effects outside Builder preview. `tool_bridge.call_tool()` now
  classifies runtime tool requests with
  `conversation_safety.classify_action_risk(...)` and rejects mandatory-review
  effects unless an explicit Pending Action / approval identity is attached.
- [ ] `[should]` Add a policy inspector UI that explains memory access,
  retrieved evidence, action approval class, redaction state, and denial
  reasons for the last turn.

### Phase 11. Memory and Retrieval Maturity

- [x] `[must]` Add FTS indexes for conversation messages and memory items with
  index health diagnostics and rebuild status. `conversation_store` now
  creates/syncs FTS5 indexes when available, exposes
  `search_index_health()`, `rebuild_search_indexes()`, `search_messages()`,
  and FTS-first/fallback memory search, while context packets report search
  index readiness.
- [x] `[must]` Add segment summaries for long conversations with source refs,
  retention/redaction awareness, and summary freshness diagnostics. The first
  deterministic layer stores `conversation_segments`, builds summaries from
  non-redacted ledger ranges, preserves source refs, exposes rebuild/list/search
  APIs, and reports freshness through `segment_summary_health()` and context
  packet diagnostics.
- [ ] `[must]` Add memory provenance, conflict handling, confidence decay,
  stale-memory filtering, and explicit memory review/edit flows.
- [x] `[must]` Add memory extraction proposals that default to Pending Action
  or user-visible approval before broad reusable memory is written.
  `adaos.sdk.memory.propose_write(...)` publishes `memory.write.review`
  Pending Actions with proposed memory, write policy, source refs, and
  approve/refuse/postpone actions instead of writing reusable memory directly.
- [ ] `[should]` Add hybrid semantic retrieval / embeddings after deterministic
  FTS and summary retrieval are stable and measured.
- [ ] `[should]` Add retrieval regression tests for long companion,
  Builder, Teacher, and federated-node histories.

### Phase 12. Production Voice

- [ ] `[must]` Make the non-blocking Voice widget a reusable dialog component
  that can be embedded by Prompt IDE and other workbenches.
- [ ] `[must]` Add explicit no-input, barge-in, interruption, TTS-cancel,
  transcript-confidence, and end-of-speech states to the dialog policy model.
- [ ] `[must]` Add per-agent voice profile contract, gender/language hints,
  preview controls, and fallback policy when a requested voice is unavailable.
- [ ] `[must]` Keep semantic routing independent from STT/TTS projection
  latency and browser recovery paths.
- [x] `[must]` Make the compatibility Voice history path thread-aware before
  broader Voice widget extraction. `voice_chat.messages` snapshots and
  `conversation.history.more` now preserve `thread_id` / topic identity so
  browser recovery does not collapse separate Builder project threads into one
  transcript.
- [ ] `[should]` Add optional OpenAI TTS generation/cache for character voices,
  with cache lifecycle, consent, and local fallback.
- [ ] `[should]` Add voice-latency and interruption golden tests.

### Phase 13. Builder and Skill Runtime Reference

- [ ] `[must]` Treat `builder_skill` as the reference conversation-native skill:
  it owns the Builder dialog, uses context packets, writes Pending Actions,
  stores review/apply evidence, and never reads raw UI chat state.
- [x] `[must]` Complete the first reference-skill slice for `builder_skill`:
  it now returns and emits canonical Builder topic/thread refs, stores them in
  sessions, passes them to chat append metadata, and keeps Pending Action
  `domain_ref` / `source_refs` attached to the same thread.
- [x] `[must]` Add the first practical `builder_skill.chat` acceptance flow:
  phrase-level first idea creates a draft preview, writes `webui.json`, opens a
  review Pending Action, and applies a follow-up UI patch in the same Builder
  topic.
- [ ] `[must]` Add generated-skill templates for skill-owned conversations,
  multi-agent skill conversations, bounded `chat.ask` flows, and memory
  extraction proposal flows.
- [x] `[must]` Add validation/lint warnings that block direct transcript files,
  direct Yjs chat writes, unbounded in-process histories, and transport-owned
  memory in generated LLM skills. The shared `SkillValidationService` now
  rejects direct Yjs symbols, raw transcript/chat-history files,
  transport-owned chat references, and unbounded module-level conversation
  state during install/strict validation.
- [ ] `[must]` Add browser acceptance tests for Builder through Voice/global
  dialog, Prompt IDE, and Pending Actions.
- [ ] `[should]` Add Builder repair conversations that connect validation
  failures, CI/test logs, user review comments, and generated-file diffs.
- [ ] `[should]` Use the first reference Builder skill as a public-quality
  example for third-party skill authors.

## Should Readiness Checkpoint

The foundation is ready for selective `[should]` work, provided the remaining
`[must]` items continue as explicit parallel tracks rather than being treated
as optional polish.

Foundation now in place:

- Durable node-local conversation ledger, channels, agents, topics/threads,
  history paging, turn traces, export/redaction audit, FTS search, segment
  summaries, scoped memory, consent audit, and Pending Action memory proposals.
- Runtime action-risk approval gates, Builder preview approval gates, retrieved
  evidence safety, golden evaluation, and migration-gated baseline fixtures.
- Builder first practical flow: addressed/phrase-level entry, isolated Builder
  conversation, workbench binding, draft preview, `webui.json` patching,
  Pending Action review handoff, and generated-skill conversation lint.

Parallel `[must]` tracks that remain before production maturity:

- Voice production: reusable non-blocking widget, no-input/barge-in/TTS states,
  per-agent voice profiles, and semantic routing isolation from STT/TTS latency.
- Builder production lifecycle: Prompt IDE as full Workbench, release records,
  approval identity on applied changes, post-activation checks, and repair task
  routing from runtime evidence.
- Evaluation expansion: companion profile correction, no-input repair,
  apply/reject handoff, memory-write consent, validation failure, and repair
  golden dialogs plus browser acceptance.
- Memory maturity beyond the service gate: conflict resolution, confidence
  decay, stale-memory review/edit UX, and higher-quality extraction policies.
- Generated-skill templates for skill-owned conversations, multi-agent skills,
  bounded ask flows, and memory proposal patterns.

## Acceptance Criteria

The architecture is implemented when:

- A generated skill can declare and use its own conversation without knowing
  whether the user is in Telegram, browser, or voice.
- User-visible dialog history and memory live in the node-local
  conversation/memory service, while logical ownership remains scoped to core,
  skills, and skill agents.
- Skills can access their own history and memory through SDK APIs without
  direct SQL or arbitrary transcript files.
- LLM Builder and skill runtimes receive budgeted context packets assembled
  from recent turns, summaries, memory items, and evidence refs.
- Builder has a separate context from the general assistant.
- One user can have concurrent `general`, `builder`, and `teacher`
  conversations with different context policies.
- One skill conversation can host multiple agents/personas without creating
  one dialog channel per persona.
- Telegram and voice messages can enter the same conversation when policy says
  they should.
- Transport ids are preserved for delivery and audit but are never used as the
  canonical memory boundary.
- Pending Actions link back to conversations and threads for human review
  context.
- Low-level `io.out.chat.append` is no longer the recommended SDK surface for
  ordinary skill dialog.
- Voice can switch between `general`, `conversational`, and `builder` without
  mixing histories, while still using one global listening/chat shell.
- A core-initiated NLU Teacher clarification and a skill-initiated companion
  prompt both record initiator evidence and obey the same conversation policy
  model.
- Subnet-wide search is federated, policy-checked, timeout-bound, and based on
  summaries/fragments/refs rather than shared remote database access.
