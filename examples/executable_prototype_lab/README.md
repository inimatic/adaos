# Executable Prototype Lab

This scenario is a portable proof of the Builder executable-prototype MVP.
It keeps four concerns separate:

- `webui.json` describes UI composition and responsive placement;
- `prototype/data.json` provides disposable CRUD, recorded provider output and deterministic generated output;
- `workflow.json` remains the canonical full workflow definition;
- `prototype/workflow_slice.json` constrains conversational prototyping and pins the source definition digest.

The checked-in binding and activity mappings are intentionally incomplete.
Automation handoff must therefore fail closed. The end-to-end test then supplies
explicit implementation mappings in memory and proves that the exact same
prototype evidence becomes admissible without changing the source artifacts.

The scenario was installed locally as DEV scenario `executable_prototype_lab`
under subnet `sn_6acf0c01` and validated with the standard AdaOS scenario
validator.
