# Conversational Workflow Skill

This package is the executable reference for AdaOS conversational-to-declarative
development. It keeps the governed workflow authoritative while conversational
sources define intent proposals, deterministic matching, affordances, repair,
semantic output, and deterministic stories.

Use the developer SDK to validate it and export static evidence:

```python
from adaos.sdk.developer.conversational import export_package

export_package(
    "examples/conversational-workflow-skill",
    kind="skill",
    output_dir="build/conversational-workflow-skill",
)
```

The export contains JSON validation/evidence plus a Markdown report with a
Mermaid statechart and conversation-story timelines. The generated report is a
projection; `workflow.json` and `conversational/` remain authoritative.
