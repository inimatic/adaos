from adaos.services.scenario.node_data_scope import node_scope_data_path


def test_node_scope_data_path_scopes_node_owned_media_state() -> None:
    assert node_scope_data_path("data/media/library", "member-1") == "data/nodes/member-1/media/library"
    assert node_scope_data_path("data/nodes/member-1/media/library", "member-2") == "data/nodes/member-1/media/library"


def test_node_scope_data_path_keeps_shared_desktop_roots() -> None:
    assert node_scope_data_path("data/catalog/widgets", "member-1") == "data/catalog/widgets"
    assert node_scope_data_path("data/desktop/widgetOrder", "member-1") == "data/desktop/widgetOrder"
