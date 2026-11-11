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


def test_chat_catalogs_are_dependencies_but_message_text_and_examples_are_not():
    files = {"kind": "resourceQuery", "resourceType": "prototype.files"}
    tools = {"kind": "resourceQuery", "resourceType": "prototype.tools"}
    widget = {"type": "ui.chat", "inputs": {"conversation": {
        "references": {"sources": [files, {"kind": "static", "value": []}]}, "webSearch": {"source": tools}},
        "meta": {"documentation_example": files}}}
    assert list(widget_resource_queries(widget)) == [files, tools]
    widget['type'] = 'item.details'
    assert list(widget_resource_queries(widget)) == []
