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

