# Builder Context Routing Evidence — 2026-08-03

## Decision

Builder conversation state is a two-level focus, not a global current
Webspace:

1. `BuilderContextRef` selects one Webspace in which the Builder scenario is
   active and binds it to that host's explicit Preview relation.
2. Project focus is stored inside the selected Builder context.

For a normal Builder host the relation is `builder host -> development
Preview`. When Builder itself is developed, the bounded self-host relation is
`development Builder host -> its child Preview`. Webspace names and `-dev`
suffixes are never parsed to infer this topology.

Web turns resolve the Builder context from the current surface and registered
relation. Telegram turns persist the selected Builder host per `chat_id` and
`thread_id`; the shared skill conversation id is not used as a user-focus key.
Changing the project does not change the selected Builder host.

## Contract

`adaos.builder.context_ref.v1` contains:

- Builder Webspace identity, title, kind, source mode, and active scenario;
- Preview Webspace and relation identity/generation;
- readiness status, selection availability, and a stable failure reason.

Discovery is read-only. It does not call the mutating workbench binding getter,
create a relation, or provision a Preview. A stale binding such as legacy
`default -> default-dev` is excluded when Builder is not active in `default`.
Unavailable active Builder surfaces remain observable but are not offered as
actions.

## Interaction

When Telegram has no valid Builder focus:

1. Builder returns `builder_context_required`.
2. A capability-negotiated single-choice interaction offers ready Builder
   hosts; text fallback remains semantically equivalent.
3. `builder.context.select` validates the selected context again and stores it
   for the originating Telegram chat/thread.
4. Builder then presents projects within that host.
5. Preview links use that host's registered Preview Webspace.

Web calls keep compatibility for authorized synthetic API/test surfaces, but
runtime browser surfaces use exact topology resolution.

## Completed checks

- [x] `[must]` Add and validate `adaos.builder.context_ref.v1`.
- [x] `[must]` Discover active Builder hosts without topology mutation.
- [x] `[must]` Exclude the legacy non-Builder `default` binding.
- [x] `[must]` Remove suffix-based Builder host inference from `builder_skill`.
- [x] `[must]` Separate Telegram Builder focus from project focus.
- [x] `[must]` Scope Telegram focus by chat/thread, not the shared skill
  conversation id.
- [x] `[must]` Generate Preview links from the selected Builder relation.
- [x] `[must]` Make confirmed client Webspace navigation canonical across the
  intent reload and fail closed when the runtime rejects the switch.
- [x] `[must]` Run Builder workbench/SDK/ABI tests and the complete Builder
  skill test suite.
- [x] `[must]` Run a local DEV runtime smoke: choose `dev1`, then observe
  `Builder: dev1 -> Preview dev1-dev`, current project `test04_recipes`, and
  project-choice controls.
- [x] `[must]` Publish the validated skill locally to Workspace version
  `0.3.32`, activate slot `B`, and run a Workspace Telegram-route smoke that
  returns `builder_context_required` with `builder.context.select` controls.
- [x] `[must]` Bind the local API/Telegram runtime to the current checkout;
  the previous core slot `0.1.604` predated the Builder host-discovery ABI.
- [x] `[must]` Distinguish an empty Builder inventory from an unavailable or
  incompatible discovery runtime; a discovery failure can no longer be
  reported as "no active Builder".
- [ ] `[must]` Human Telegram acceptance with the real conversation buttons.
- [ ] `[must]` Human browser acceptance of a generated Preview link after the
  updated client is deployed.
- [ ] `[should]` Add lifecycle management for stale smoke Builder Webspaces so
  the selection surface does not accumulate inactive test hosts.

## Local activation and publication status

`builder_skill` version `0.3.38` is validated and activated in the local DEV
runtime. The hardened content is locally published as Workspace version
`0.3.32` and activated on slot `B`, so the Workspace-backed Telegram route no
longer uses version `0.3.30`. Its core compatibility floor is the explicit
Builder-context ABI commit `e4f794b8` (`0.1.660+4316`). The local API reports
the current checkout commit and an HTTP `/api/tools/call` smoke discovers both
`desktop` and `dev1` with a button presentation plan. The registry publication
commits are local and were not pushed. The AdaOS core commits are also local.
Only the client repository is authorized for remote push in this iteration.
