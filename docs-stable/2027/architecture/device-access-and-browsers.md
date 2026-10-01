# Devices And Browsers

Status: public conceptual architecture.

Browsers and devices are participation surfaces. They do not define separate
AdaOS products by themselves.

## Browser Role

The browser presents AdaOS applications, webspaces, media playback, and runtime
status to a user. It should receive only the state and commands that are valid
for the current user, webspace, and application.

## Device Role

A device can host a hub, host a member node, expose a browser, provide local
media or services, or participate as a reusable endpoint. Public docs should
describe what an operator can do now, not internal placement plans.

## Current Boundary

- Browser access is explicit and tied to the selected zone/environment.
- Member nodes join through hub-controlled admission.
- Application capabilities may later be distributed across nodes, but public
  stable docs should not promise automatic placement until the management
  experience is ready.

