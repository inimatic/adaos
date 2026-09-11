from adaos.services.ui_resource_queries import widget_resource_queries


def test_dependencies_include_only_executable_typed_query_slots():
    source = {"kind": "resourceQuery", "resourceType": "prototype.records"}
    lookup = {"kind": "resourceQuery", "resourceType": "prototype.choices"}
    widget = {"type": "ui.form", "dataSource": source,
              "inputs": {"fields": [{"optionsDataSource": lookup}, None]},
              "meta": {"documentation_example": lookup}}
    assert list(widget_resource_queries(widget)) == [source, lookup]
    widget["type"] = "item.details"
    assert list(widget_resource_queries(widget)) == [source]
    assert list(widget_resource_queries({"type": "ui.form", "inputs": None})) == []
    assert list(widget_resource_queries({"dataSource": {"kind": "resourceQuery", "resourceType": None}})) == []
