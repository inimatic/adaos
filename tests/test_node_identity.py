from adaos.domain.node_identity import node_identities_match, node_identity_token


def test_node_identity_token_accepts_supported_canonical_forms() -> None:
    node_id = "8db40740-b3ff-44bf-baf5-9fb013b35b01"

    assert node_identity_token(node_id) == node_id
    assert node_identity_token(f"hub:{node_id}") == node_id
    assert node_identity_token(f"member:{node_id}") == node_id
    assert node_identity_token(f"node:{node_id}") == node_id
    assert node_identity_token(f"redevice:{node_id}") == node_id
    assert node_identity_token(f"root:{node_id}") == f"root:{node_id}"


def test_node_identities_match_requires_nonempty_equivalent_tokens() -> None:
    assert node_identities_match("hub:node-1", "node-1")
    assert node_identities_match("member:node-1", "redevice:node-1")
    assert not node_identities_match("root:node-1", "node-1")
    assert not node_identities_match("", "")
    assert not node_identities_match("node-1", "node-2")
