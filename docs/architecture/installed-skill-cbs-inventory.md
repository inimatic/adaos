# Installed skill CBS inventory

Status: CBS migration baseline, 2026-09-23.

The inventory is produced by
`adaos.services.capability_binding_state.inventory_installed_skills`. It scans
the installed workspace and treats the development workspace only as an
upgrade overlay. It never mutates a skill.

## Snapshot

- Installed skills: **52**.
- Native CBS providers: **0**.
- Builder/CBS-aware skills: **1** (`builder_sdk_control_skill`).
- Stateful legacy provider candidates: **40**.
- Stateless legacy provider candidates: **6**.
- Legacy leaf/UI components: **5**.
- Installed skills with a newer development overlay: **18**.
- Inventory digest:
  `sha256:01c52cca52e51a5004bb9f1594dd7562a6465e2132e5522786ba6f08c0276afa`.

The digest describes this machine's snapshot, not a portable architecture
artifact. Re-running the inventory after a skill installation or promotion is
expected to produce a new digest.

## Compatibility rule

`skill.yaml.capabilities` remains a list of runtime access requested by the
skill. It is not interpreted as a list of semantic operations supplied by the
skill. A native provider must ship, at minimum, an explicit
`adaos.capability.contract.v1` and `adaos.binding.definition.v1`; stateful
providers additionally need explicit state contracts and installation-local
state-space binding.

This distinction makes the inventory reverse compatible: existing manifests
continue to load unchanged while migration is additive.

## Migration order

1. `builder_skill`, `builder_sdk_control_skill`, and
   `builder_automation_skill`: preserve the accepted-prototype/CBS compilation
   handoff, package its portable contracts, and emit conformance evidence.
2. The three beta validation applications: Flowboard and AdaOS Drive first as
   stateful provider candidates; Applications as the consumer and lifecycle
   integration proof.
3. Other stateful providers, prioritised by active use and portability risk.
4. Stateless tools only when an operation has a second consumer or a clear
   reusable semantic identity.
5. Leaf/UI skills remain on the compatibility path until reuse justifies a
   contract.

No bulk rewrite is required. A legacy skill may coexist with CBS-native
providers; the resolver sees only explicit portable contracts, and legacy
activation remains authoritative for skills not yet migrated.

## Reproduction

Run from the repository root with the project environment:

```python
from pathlib import Path
from adaos.services.capability_binding_state import inventory_installed_skills

report = inventory_installed_skills(
    Path(".adaos/workspace/skills"),
    dev_root=Path(".adaos/dev"),
)
print(report["summary"])
print(report["inventory_digest"])
```

The full machine-readable result includes installed/development versions,
tool counts, runtime capability requests, state signals, explicitly discovered
portable artifacts, migration class, priority, and suggested next action.
