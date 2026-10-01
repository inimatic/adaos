# Member-Hub Connectivity

Status: public conceptual architecture.

The hub coordinates a subnet. A member joins that subnet to contribute runtime
capacity, local proximity, or device-specific access.

## Public Model

1. The hub creates admission material, such as a join code.
2. The member uses that material during bootstrap.
3. The hub records and supervises the joined member.
4. Applications and scenarios may use the member when their capabilities allow
   it.

## Operator Expectations

- Join material should be short-lived and protected.
- Members should have recognizable names.
- Health and reliability status should be checked after onboarding.
- Removing or replacing a member should not require understanding internal
  architecture documents.

