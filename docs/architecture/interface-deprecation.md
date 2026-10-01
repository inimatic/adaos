# Interface deprecation and Builder authoring

AdaOS separates runtime compatibility from authoring availability. A deprecated
interface may remain readable by the runtime while installed immutable releases
are migrated, but it is not an admissible choice for Builder, an LLM-generated
Prototype, Automation, or new package source.

## Lifecycle contract

Every deprecated declarative interface must have a machine-readable entry in
the relevant capability catalog with:

- its stable interface identity;
- `status: deprecated`;
- an authoring policy (`migration_only` or `forbidden`);
- the supported replacement;
- a bounded migration rule;
- a data-only detector (`field` + expected value);
- a named migrator registered by Core;
- the compatibility promise, if any.

Catalog and compact compiler views are the source supplied through Root MCP and
Builder context. Prompts may summarize that contract, but must not be the only
place where deprecation is expressed.

The generic pipeline lives in `adaos.services.interface_deprecations`. It reads
the catalog, scans candidate artifacts, dispatches only registered migrators,
rescans their output, and returns a receipt plus unresolved findings. Adding a
later migration therefore extends the catalog and migrator registry; it does
not add another special prompt-only convention.

The resulting boundary is:

```text
existing immutable artifact -> compatibility reader -> migration
new/revised authoring       -> current catalog only -> fail closed
```

## Builder behavior

Builder applies four controls:

1. Prototype context presents the replacement and marks the old interface as
   migration-only. Examples must use only the replacement.
2. Existing source is normalized through the versioned migration before it is
   offered as the editable baseline.
3. Generated output is validated against the current authoring contract. A
   deprecated interface that remains after normalization blocks the revision.
4. Automation receives the same lifecycle data in its compact implementation
   bindings, so Codex cannot reintroduce an interface removed by Prototype LLM.

Runtime support and user-facing diagnostics are removed only after registry and
installed-release telemetry show that no supported artifact still requires the
compatibility reader.

## `openModal` migration

`action.type:openModal` is the first governed migration. It remains a runtime
compatibility input, but Builder must produce `navigate` to a public view:

```json
{
  "type": "navigate",
  "params": {
    "to": "example.record.edit",
    "surface": "modal",
    "modalId": "record_editor"
  }
}
```

The view is declared under `ui.application.interfaces.<owner>.views`. The modal
lists that view in `implements`, and `schema.interface.routes` binds the public
view to the concrete modal implementation. The migration is implemented by
`adaos.apps.open_modal_migrate`; it is idempotent and reports any unresolved
legacy action rather than guessing an ambiguous binding.

## Removal gate

A compatibility path may be deleted only when all of the following are true:

- registry artifacts have been scanned and migrated or retired;
- active installation telemetry reports zero legacy use for the agreed window;
- Builder and Automation reject new occurrences;
- the migration tool remains available for offline/imported artifacts;
- the removal version and rollback policy are documented.
