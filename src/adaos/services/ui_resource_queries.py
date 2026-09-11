"""Resource dependencies in executable WebUI query slots, not arbitrary JSON."""

from collections.abc import Iterator, Mapping
from typing import Any


def widget_resource_queries(widget: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    sources = [widget.get("dataSource")]
    inputs = widget.get("inputs")
    if widget.get("type") == "ui.form" and isinstance(inputs, Mapping):
        sources.extend(field.get("optionsDataSource") for field in inputs.get("fields") or []
                       if isinstance(field, Mapping))
    for source in sources:
        if (isinstance(source, Mapping) and source.get("kind") == "resourceQuery"
                and isinstance(source.get("resourceType"), str) and source["resourceType"].strip()):
            yield source
