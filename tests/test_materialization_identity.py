from adaos.services.scenario.webspace_runtime import canonical_materialization_identity


def test_canonical_materialization_identity_is_access_scoped() -> None:
    first = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        revision="021",
        source_fingerprint="abcdef1234567890",
        roles=["Editor", "admin", "editor", ""],
    )
    second = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        revision="021",
        source_fingerprint="abcdef1234567890",
        user_id="guest",
        roles=["admin", "editor"],
    )
    different_access = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        revision="021",
        source_fingerprint="abcdef1234567890",
        user_id="alice",
        roles=["admin"],
    )

    assert first["user_id"] == "guest"
    assert first["guest"] is True
    assert first["roles"] == ["admin", "editor"]
    assert first["key"] == second["key"]
    assert first["key_hash"] == second["key_hash"]
    assert different_access["key"] != first["key"]
    assert "policy-" not in first["key"]


def test_canonical_materialization_identity_uses_current_without_revision_or_source() -> None:
    identity = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        user_id="",
        roles=None,
    )

    assert identity["user_id"] == "guest"
    assert identity["revision"] is None
    assert identity["source_fingerprint"] is None
    assert ":current:guest:" in identity["key"]
