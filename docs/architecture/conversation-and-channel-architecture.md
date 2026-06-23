# Conversation and Channel Architecture

Status: target architecture and implementation checklist.

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

## Target Data Model

Conversation records live under a shared conversation store:

```json
{
  "data": {
    "conversations": {
      "by_id": {
        "conv.builder.default": {
          "id": "conv.builder.default",
          "kind": "builder",
          "owner": "skill:builder",
          "surface": "builder",
          "webspace_id": "default",
          "title": "Builder",
          "state": "active",
          "participants": [
            {"type": "user", "id": "user.local"},
            {"type": "skill", "id": "builder"}
          ],
          "context_policy": {
            "strategy": "isolated",
            "memory_scope": "skill",
            "include_general_history": false
          },
          "routing_policy": {
            "default_transports": ["web", "telegram"],
            "allow_voice": false
          },
          "created_at": 1730000000.0,
          "updated_at": 1730000000.0
        }
      },
      "messages_by_conversation": {
        "conv.builder.default": [
          {
            "id": "msg.123",
            "conversation_id": "conv.builder.default",
            "thread_id": null,
            "role": "assistant",
            "from": {"type": "skill", "id": "builder"},
            "content": [{"type": "text", "text": "What should we change?"}],
            "transport": "web",
            "external_ref": null,
            "meta": {
              "trace_id": "trace.123",
              "route_id": "builder"
            },
            "created_at": 1730000001.0
          }
        ]
      }
    }
  }
}
```

Rules:

- `conversation.id`, `kind`, `owner`, `surface`, `webspace_id`, `state`,
  `context_policy`, and `routing_policy` are required.
- `owner` is the actor accountable for the conversation context.
- `surface` is the runtime that should interpret incoming user messages by
  default.
- `kind` is a product category, not an authorization key.
- `participants` describes who may see or write into the conversation.
- `context_policy` controls memory and LLM prompt assembly.
- `routing_policy` controls where outbound messages may be delivered.
- Message `content` is typed; plain text is only one content part.
- Message `transport` records where the message actually moved. It does not
  decide which context the message belongs to.
- `external_ref` stores platform-specific ids and must never be used as the
  canonical conversation id.

## Canonical Conversation Kinds

Initial kinds:

- `general`: default assistant conversation for normal user requests.
- `builder`: governed creation and modification workflow.
- `skill`: skill-owned dialog with isolated or scoped context.
- `teacher`: NLU Teacher, authoring, correction, and clarification workflows.
- `support`: operational support, repair, onboarding, or troubleshooting.

New kinds require a reason that cannot be expressed by owner, surface,
participants, or context policy.

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
- The runtime resolves the message to the Builder conversation.
- Builder reads and writes through the conversation SDK.
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
    context:
      strategy: isolated
      memory_scope: skill
    routing:
      default_transports: ["web", "telegram"]
      allow_voice: false
```

Builder-like skills use:

```yaml
conversations:
  builder:
    kind: builder
    title: Builder
    context:
      strategy: isolated
      memory_scope: skill
      include_general_history: false
    routing:
      default_transports: ["web", "telegram"]
      allow_voice: false
```

Manifest rules:

- `conversations.<name>.kind` is required.
- `context.strategy` is required and starts with `isolated`, `shared`, or
  `ephemeral`.
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
- Telegram command or deep link
- endpoint audio dialog mode
- previous transport binding for the user
- fallback to `general`

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

## Implementation Checklist

### Phase 0. Contract Freeze

- [ ] Define `Conversation`, `ConversationMessage`, `ConversationThread`, and
  `ConversationRoutingPolicy` schemas.
- [ ] Add manifest schema support for `conversations`.
- [ ] Add SDK design docs for `adaos.sdk.conversation`.
- [ ] Define conversation ids, owner ids, surface ids, and thread id formats.
- [ ] Define content part schema for text, media, action, form, and system
  evidence parts.
- [ ] Define retention and redaction rules for conversation history.

### Phase 1. Core Conversation Service

- [ ] Add a core service that creates, reads, appends, lists, and archives
  conversations.
- [ ] Store conversations under a shared Yjs projection and, if needed,
  durable disk/database backing.
- [ ] Emit lifecycle events: created, message appended, thread created,
  archived, routing failed.
- [ ] Add idempotency for inbound platform message ids.
- [ ] Add owner and participant policy checks.
- [ ] Add tests for append, ask response correlation, thread creation,
  idempotency, and policy rejection.

### Phase 2. SDK

- [ ] Implement `adaos.sdk.conversation`.
- [ ] Add `conversation.current()` context propagation for tool calls.
- [ ] Add `chat.send`, `chat.ask`, `chat.history`, and `chat.start_thread`.
- [ ] Add manifest-driven default conversation creation for skills.
- [ ] Add LLM skill-development docs and examples for private skill chats.
- [ ] Mark low-level chat output helpers as transport/event primitives in SDK
  docs.

### Phase 3. Transport Integration

- [ ] Make Telegram inbound messages resolve to conversations before NLU or
  skill dispatch.
- [ ] Make voice and endpoint audio resolve to conversations before NLU or
  skill dispatch.
- [ ] Make browser chat panels subscribe to `data.conversations` instead of a
  transport-specific chat path.
- [ ] Record external transport refs separately from canonical conversation
  identity.
- [ ] Add delivery status records per transport attempt.

### Phase 4. Surface Dispatch

- [ ] Route `general` conversations to the default assistant/NLU surface.
- [ ] Route `builder` conversations to Builder.
- [ ] Route `teacher` conversations to NLU Teacher surfaces.
- [ ] Route `skill` conversations to their owning skill.
- [ ] Add active-surface selection for browser UI and transport commands.
- [ ] Add explicit fallback behavior when a surface is unavailable.

### Phase 5. Builder Migration

- [ ] Create the default Builder conversation on first Builder entry.
- [ ] Move Builder clarification, draft, preview, and repair messages into the
  Builder conversation.
- [ ] Link Builder Pending Actions to the originating conversation and thread.
- [ ] Preserve approval/apply evidence outside plain chat messages.
- [ ] Add acceptance tests for Builder through browser and Telegram transport.

### Phase 6. NLU Teacher Migration

- [ ] Move Teacher clarification sessions into `kind=teacher` conversations.
- [ ] Link candidate confirmations and Pending Actions to Teacher
  conversations.
- [ ] Keep NLU traces as evidence refs, not unbounded chat history blobs.
- [ ] Add tests for multi-turn correction with separate general and teacher
  contexts.

### Phase 7. Cleanup

- [ ] Remove public dependency on `voice_chat` as the canonical chat state.
- [ ] Replace `route_id == "voice_chat"` checks with conversation and surface
  routing.
- [ ] Retire direct writes to transport-specific chat projections.
- [ ] Update SDK IO docs to make conversation APIs the default path.
- [ ] Remove compatibility bridges once browser, Telegram, voice, Builder, and
  Teacher use the conversation service.

## Acceptance Criteria

The architecture is implemented when:

- A generated skill can declare and use its own conversation without knowing
  whether the user is in Telegram, browser, or voice.
- Builder has a separate context from the general assistant.
- One user can have concurrent `general`, `builder`, and `teacher`
  conversations with different context policies.
- Telegram and voice messages can enter the same conversation when policy says
  they should.
- Transport ids are preserved for delivery and audit but are never used as the
  canonical memory boundary.
- Pending Actions link back to conversations and threads for human review
  context.
- Low-level `io.out.chat.append` is no longer the recommended SDK surface for
  ordinary skill dialog.
