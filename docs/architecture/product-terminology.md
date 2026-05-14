# AdaOS Product Terminology

AdaOS is an environment for personal digital assistants. Internally the runtime still uses subnets, scenarios, widgets, browser sessions, hub/member roles, and Yjs webspaces, but normal product UI should lead with named user-facing entities.

## Primary Model

The user-facing hierarchy is:

```text
Assistant -> Webspace -> Application -> Panel
```

The runtime/device hierarchy is:

```text
Assistant -> Device -> Agent
```

Combined:

```text
Assistant
  -> Webspaces
     -> Applications
        -> Panels
  -> Skills
  -> Devices
     -> Agents
  -> Interfaces
  -> Catalog
```

## Term Mapping

| Internal term | Product term | Notes |
| --- | --- | --- |
| `subnet`, `subnet_id` | Assistant, Assistant ID | Show the display name by default. Keep IDs for diagnostics. |
| `webspace`, `default`, `main` | Webspace, Main | Webspace is an access/projection context, not a folder. |
| `scenario` | Application | Scenario remains the implementation/authoring term. |
| `web_desktop` | Capabilities | Default overview application. Keep `web_desktop` as the stable ID. |
| `skill` | Skill | Executable capability used by applications and agents. |
| `widget` | Widget, later Panel | Current UI may keep Widget while the broader product model reserves Panel. |
| `browser`, `member`, `hub`, `subnet endpoint` | Agent | Software participant of the assistant subnet. |
| `device` | Device | Physical or virtual host. One device may host multiple agents. |
| `marketplace` | Catalog | Place to add applications, skills, widgets/panels, interfaces, agents, and integrations. |
| `install` | Add to assistant | Use install/deploy wording only in advanced or developer UI. |

## UI Rules

Use named entities first. For example, render `subnet_id` as `My Assistant` or the user-defined assistant name, `default` as `Main`, and `web_desktop` as `Capabilities`.

The primary top-bar formula is:

```text
Brand | Assistant | Webspace | Application | Status | Actions
```

In compact layouts, the assistant name may be hidden when it is the default `My Assistant`, leaving:

```text
Webspace / Application
```

Debug-first labels such as raw subnet IDs, endpoint IDs, `LINK OK`, or low-level Yjs state belong in diagnostics and advanced mode.

## Compatibility Policy

Do not break the current API or Yjs schema while migrating terminology. Add public aliases and projections first:

- `web.application.*` may delegate to existing `web.desktop.*`.
- `application_id` may alias `scenario_id`.
- `pinned_panels` may alias `pinned_widgets` if and when Panel becomes the visible term.
- New product kinds such as Assistant, Application, Agent, and Panel can exist next to older internal/debug kinds.

Device/Agent migration should happen through projections and catalog views before changing connectivity or pairing data structures.
