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

The selected application's operational runtime is observed through Core, not
Builder's mutable authoring history. **Manage runtime and Trials** opens the same
reviewed controls used by Applications and Management. Candidate acceptance,
temporary admission, installed local Beta and publication are separate decisions;
accepting a Candidate does not by itself remove its Trial deadline. See
[Application runtime lifecycle](../operations/versioning.md#application-runtime-lifecycle-local-beta).

Core keeps progress events for development operations, including failed attempts
and recovery, across restarts. These private events identify the application,
actor, source references and operation revision; prompts, provider responses and
raw service errors stay in their private stores. Public release notes remain
bound to the exact successful publication.

For an older accepted Candidate whose source points to a local Git Project,
the node owner can run `adaos dev project source-qualify-candidate <candidate-id>
--repository <historical-repository> --json`. Core checks the original commit
against every owned package and retains the source through private Forge. A
mismatch stops qualification. This source-only procedure preserves the Candidate,
current development edits and application data; it does not publish the release.

The native Builder SDK also supports preparing a Trial from an exact private
source checkpoint. Supply its Change, operation and snapshot digest together
with verification evidence for that snapshot. Core prepares the owned packages,
checks access and places the Trial for the existing application. Recovery uses
the saved checkpoint and evidence, including after further development edits.
This path does not run new research or generation on your behalf.

## Desktop launchers

An application contributes one desktop launcher through its primary scenario
entrypoint. Supporting scenarios, such as Prompt IDE, remain part of the
application's navigation. Creating a scenario with `type: desktop` does not
publish a separate launcher; declare it as an application's primary entrypoint.

Desktop launchers follow the selected release. A temporary Trial appears only
on its admitted desktops, while an installed local Beta can be opened on another
desktop through the normal runtime admission checks. A scenario missing from
that release does not appear through an older installation or a saved pin.

In Management, open desktop settings to review **Pinned applications**. The
checklist shows installed applications available to the current participant.
Saving it changes the shared desktop's pins; it does not grant application
access. Applications can also be pinned from their own context in Applications.

The desktop owner can delegate renaming the desktop, pinning applications or
widgets, reordering icons and widgets, changing desktop appearance, and renaming
the participant's own verified endpoint. Other administrative settings remain
hidden or read only for participants. Changing Home, managing access, and
installing applications require administration rights. Display preferences are
stored separately for each user, desktop, and subnet in the current browser.

## Public Promise

The public promise should stay modest until the workflow is packaged for
external users:

- Builder can support guided AdaOS application development.
- Generated changes remain reviewable.
- Runtime activation is separate from drafting.
- Validation evidence matters more than model confidence.

## Current Limits

For beta layout and editor capabilities, see [Declarative workspaces and image layers](workspaces.md).

- Builder is still a development workflow, not a general public app builder.
- Internal implementation contracts and roadmaps are not part of this public
  documentation track.
- Users should not assume autonomous production deployment without review.

