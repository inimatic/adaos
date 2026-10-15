# Builder

Status: experimental public documentation.

**I have an idea. Let's build it.**

Builder is the AdaOS development workflow that turns an idea into a governed
application candidate. It is not a bypass around AdaOS runtime safety. Builder
uses AdaOS contracts so changes can be inspected, validated, activated,
observed, and rolled back.

## Current Shape

Builder work is organized around:

- intent capture and clarification;
- prototype generation and review;
- automation implementation in an isolated development flow;
- validation, evidence, and source checkpoints;
- human acceptance before release or activation.

During Automation, prefix an informational chat message with `/note`, followed
by the note text. Builder keeps it in the discussion and acknowledges it without
starting another implementation iteration or changing the current candidate.
Ordinary follow-up instructions continue to request implementation work.

When a Telegram notification route is configured, Builder mirrors the chat brief,
replies, and lifecycle messages to that route. Long chat messages are split into
numbered parts.

When a local Beta is upgraded to another candidate, its declared working data
and existing image and analysis jobs are carried into the new isolated runtime.
Checking an existing job keeps its original provider identity. The previous
Beta data remains available for recovery; preparing an upgrade does not publish
the application.

## Public Promise

The public promise should stay modest until the workflow is packaged for
external users:

- Builder can support guided AdaOS application development.
- Generated changes remain reviewable.
- Runtime activation is separate from drafting.
- Validation evidence matters more than model confidence.

## Current Limits

- Builder is still a development workflow, not a general public app builder.
- Internal implementation contracts and roadmaps are not part of this public
  documentation track.
- Users should not assume autonomous production deployment without review.

