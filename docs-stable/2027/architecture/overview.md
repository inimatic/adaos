# Architecture Overview

Status: public conceptual architecture.

AdaOS is local-first. A user-facing Assistant is backed by one or more runtime
nodes, applications, skills, scenarios, browser projections, and explicit
access rules.

## Core Vocabulary

| Term | Public meaning |
| --- | --- |
| Assistant | The user-facing environment. |
| Hub | The node that owns and coordinates a subnet. |
| Member | Another node joined to the subnet. |
| Webspace | A browser-visible access and projection context. |
| Application | An installable AdaOS experience. |
| Skill | A focused executable capability. |
| Scenario | A multi-step flow across skills, services, people, and nodes. |
| Device | A physical or virtual host for runtime participation. |

## Shape

```text
User
  -> Browser / device endpoint
  -> Assistant environment
  -> Hub-managed subnet
  -> Applications
  -> Skills and scenarios
  -> Member nodes when needed
```

Public architecture pages explain this model at a level useful to users and
operators. Internal contracts, target-state roadmaps, and evidence records are
not published in this stable track.

