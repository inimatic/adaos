# Member Node Onboarding

Status: public current-state onboarding guide.

A member node extends an AdaOS subnet. The hub remains the coordinating node,
while the member contributes runtime capacity, device proximity, or local
services.

## Minimal Flow

1. Prepare the hub and create a join code.
2. Bootstrap the member with the join code.
3. Confirm that the member appears in node status.
4. Run the scenario or application that needs the member.

## What To Verify

- The member uses the expected zone.
- The node name is recognizable to the operator.
- Health checks pass after bootstrap.
- The member has only the access needed for the intended task.

Distributed product placement, automatic authority election, and advanced
node-management policy are not public stable 2027 promises.

