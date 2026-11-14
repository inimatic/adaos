# Declarative workspaces and image layers

Status: beta Client capabilities. Application services still own data, jobs,
authorization, image processing and export.

## Panels

The `workbench`, `master-detail` and `collection-detail` split layouts use the
same workspace container on the desktop and inside a schema modal. Panel
presentation responds to its container width. On compact surfaces, declared
`drawer`, `sheet`, `route` and `overflow` regions become local panel disclosures.

Declare a flexible main region and a bounded supporting region:

```json
{
  "id": "settings",
  "role": "inspector",
  "scroll": "region",
  "size": {
    "minPx": 320,
    "preferredPx": 370,
    "maxPx": 560,
    "resizable": true,
    "remember": true
  },
  "presentation": {"wide": "pane", "compact": "sheet"}
}
```

Drag the separator or focus it and use Left/Right. Shift changes the step from
10 to 50 pixels. Home or double-click restores the preferred width; End selects
the maximum. Escape cancels an unfinished drag. Stored widths are presentation
preferences isolated by subnet, surface scope, page, layout variant and region.
Restoring them never runs an application action.

With `layout.scroll: "regions"`, toolbar and command regions stay outside panel
scrolling. Set `sizing: "fill"` on a widget that should consume the remaining
panel height, including an image comparison or a composition group.

## Composition groups

`layout.group` composes ordinary widgets. Its `inputs.layout` is `row`, `stack`,
`grid`, `section` or `disclosure`. Grid columns can be fixed with `columns` or
adapt to `minItemWidthPx`. Rows wrap by default. Use the bounded gap tokens
`none`, `xs`, `sm`, `md` and `lg`.

```json
{
  "id": "tools",
  "type": "layout.group",
  "area": "main",
  "inputs": {"layout": "row", "gap": "sm"},
  "children": [
    {
      "id": "brush-radius",
      "type": "ui.form",
      "area": "content",
      "inputs": {
        "autoCommit": true,
        "fields": [{"id": "radius", "type": "number", "stateKey": "radius", "min": 1, "max": 500}]
      }
    }
  ]
}
```

Children can declare `slot: "header"`, `"body"` or `"footer"`; body is the
default. Every child retains normal data sources, actions, visibility and scope.
Widget IDs must be unique across the complete surface. Groups support at most
100 direct children, eight nested levels and 500 total widgets per surface.

Disclosure bodies are unmounted when closed. Normal browser-memory drafts
survive a remount; durable storage still requires an application command.

## Native image annotation layers

`visual.imageCompare` accepts a provider-owned `pair.annotations` document using
`adaos.image_annotations.v1`. This is separate from its legacy edit/protection
mask and context rectangle.

Each layer declares a stable ID, label, role, `side: "original" | "result"`,
visibility and explicit editability. A layer may reference an immutable native
raster mask with `encoding: "alpha" | "luminance"`. Ordered `add` and `subtract`
brush strokes correct that base without resizing or modifying it. Coordinates
and radii are pixels of the oriented full-size image.

Layer roles are application data. A focus layer does not implicitly grant or
restrict editing permission; the owning service defines its composition policy.

The Client validates mask media dimensions. A missing or mismatched mask blocks
submission readiness and produces a visible error. Supported bounds are 50 MP,
16 layers and 500 total strokes. Result contours are isolated by the exact
original/result pair; they are not carried into another result.

The built-in mask toolbar selects a layer and provides erase, undo and redo.
The crop tool creates a rectangle, moves its interior and resizes its edges or
corners. Arrow keys move it by one native pixel, or ten with Shift; Alt changes
the size. Escape or pointer cancellation rolls back an unfinished gesture.

For external controls, `editorStateKey` includes
`annotationDraft: {generation, version, layerId, document}`. Use
`editorCommandStateKey` for `layer`, `layerVisibility`, `addLayer`,
`addAnnotationStrokes`, `undo`, `redo`, `clear` and `fitBounds`. Mutating layer
commands must include the current `annotationGeneration` and
`annotationVersion`, in addition to the existing source and draft identity.
`addLayer` accepts a vector layer; a raster base comes from the owning provider.

`annotationSave` emits the layer document, legacy context mask/crop, source ID, draft generation/version
and optimistic revision. A unified run command must snapshot the context mask,
rectangle, annotations and current form values transactionally. The owning
service validates artifact ownership and rasterizes the base plus corrections
at native resolution. The Client does not run segmentation or change an image
merely because a layer is selected.

## Task progress

`feedback.taskProgress` reads `adaos.ui.task_progress.v1` from a normal data
source. `inputs.taskKey` optionally selects the record within its response.
The record supplies identity, status, stages, optional measured
`progress: {completed, total, unit?}`, timestamps, message and error.

The widget never invents a percentage or derives completion from elapsed time.
An absent progress measurement remains absent. Its cancel button requires both
`can_cancel: true` from the provider and a declared `on: "cancel"` action. The
event contains `run_id` and `revision`. Successful dispatch means cancellation
was requested; a terminal provider state confirms the outcome.

Execution belongs to a durable server process. Closing or reopening a surface
only stops or restores observation. Keep the active task identity separate from
a selected historical result.

## Design references in Builder context

Builder can retain an HTML sketch and its JavaScript, CSS or JSON text as
immutable reference material. The prototype SDK operations `add_reference` and
`reference_context` store and verify the exact UTF-8 contents, project identity
and content hashes. A chat submission supplies the returned receipts through
`_meta.builder_reference_receipts`. The reference text participates in input
budget checks and is retained with the exact model request.
Validation repairs receive the same verified reference text. Existing widget
contracts are retained during incremental edits; exact capability IDs in a
request take priority over inferred component suggestions.

References describe the desired layout and interactions. Builder translates
them into supported declarative components. It does not execute the supplied
scripts or fetch their dependencies. A sketch's simulated Drive connection or
AI result is not an implemented application service.

The beta limit is eight text references, 256 KiB per file and 512 KiB in total.
Oversized or changed references fail explicitly before inference. The existing
64,000-token input allowance remains the default; an explicitly authorized
development turn can set `_meta.builder_llm_context_tokens` from 16,384 to
256,000. This allowance does not increase the provider model's context limit.

## Implementation reference files

Design references guide Prototype generation. A governed Development Session
can separately supply readonly source files to Automation. The control tools
`start_automation` and `submit_automation` accept `development_session_id`;
Core validates its target scope and materializes its digest-bound inputs in
the executor snapshot. Binding a session to a workspace alone does not attach
those files to an execution request.

A terminal retry that changes Development Session starts from the newly
admitted snapshot. Previous candidate files and execution evidence remain in
their original run; their old materialization is not restored over the new
context. Reference filenames in the brief must match the materialized names.

When a Development Session supplies an execution budget, that budget governs
the executor. It takes precedence over a separate control-call budget. Its
`token_budget_metric`, `max_billable_tokens` and `max_context_tokens` are
preserved. Declare `fresh_plus_output` explicitly to distinguish new work from
cached input, with a separate aggregate token limit. Changing this governed
budget requires a new session; historical execution records remain unchanged.

## Validation

The workspace components and image comparison have strict input contracts in
the WebUI ABI. Builder validates unknown fields before accepting a candidate;
Client validates the same projected schemas before creating a renderer. The
layer and task data schemas are projected from the same ABI source. Invalid
configuration is reported explicitly instead of silently ignored.
The selected annotation-workspace context includes the complete annotation
and task record schemas, strict widget inputs and layer-command guards, so a
prototype does not depend on resolving an external schema filename.

## Image editing and Google Drive in beta

Applications can inspect local image-edit admission bounds with
`adaos.sdk.llm.images.edit_limits(model)` and check the exact inputs with
`validate_edit(...)` before reserving a request. Validation checks the same
dimensions, formats, inline byte bounds and image decoding rules as submission.
It creates no job and does not establish remote model availability.

`adaos.sdk.continuations.is_replay()` identifies an admitted Core replay. A long
operation can persist its inputs, register a continuation and return its queued
receipt immediately; the CPU work then starts in the replay. Read projections
remain observations, and cancellation must prevent subsequent paid submissions.
Tools declaring `durable_replay: true` can set a CPU execution deadline up to
1,800 seconds; other tools retain the 600-second maximum. This does not change
permission checks or extend the foreground HTTP request deadline.

Builder can validate a retained candidate without invoking a model. The
`automation.validate_preserved_candidate` SDK also accepts an optional bounded
`repair_brief` containing structured edits. Each edit is checked against its
source preconditions and normal component authority; the successor restores
the qualified candidate, applies the edits and runs validation with model
execution forbidden.

The beta `adaos.sdk.providers.drive` facade supports delegated read-only Google
Drive connection, paginated folder/image browsing, metadata, thumbnails and image
bytes for import. Declare `providers.google.drive` and an external provider
`google.drive` with the `drive.readonly` scope in the Application permission
profile. The node uses its configured Google OAuth client; the user completes
Google consent. Core retains credentials and refresh tokens. Applications receive
bounded bytes and metadata, rather than credentials or temporary thumbnail URLs.
The provider supports image downloads up to 64 MiB and thumbnails up to 2 MiB.

Google documents folder queries in [files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list)
and binary downloads in [files.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get).

## Replaced Trial storage

After a new local Trial successfully continues the previous Beta's data, Core
queues the predecessor's executable workspace for removal. Collection requires
a completed data-preserving migration and activation, a committed switch to the
successor, and an idle Application channel. The same rule applies to a chain of
replaced Trials whose data reached the current edition.

Selected Trials, explicit retention holds and storage pins remain protected.
Pending lifecycle operations, running tools, unresolved jobs or retained retired
component data defer cleanup. Starting from Stable instead of continuing Beta
does not authorize automatic deletion of the previous Beta's separate data.

Removal uses the shared quarantine worker in bounded slices. Core keeps the
migration evidence, private recovery snapshots and immutable package metadata
needed to reopen and finalize the surviving Beta. The collected predecessor is
no longer an executable rollback edition; pin it before replacement when that
edition must remain available.

`adaos maintenance artifact-retention --json` previews collection, including
replacements created before this rule was installed. Add `--apply` to apply the
current plan. The older retention rule still keeps three recent candidates per
project and requires seven days plus terminal publication/rejection evidence;
verified data-preserving replacements are eligible immediately.
