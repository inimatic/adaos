from __future__ import annotations

from adaos.services.builder.specification import specification_projection, text_integrity


def test_historical_transport_corruption_is_marked_without_repair() -> None:
    source = "?????? interface \ufffd"
    projected = text_integrity(source)
    assert projected["raw"] == source
    assert projected["display"] == source
    assert projected["integrity"] == "transport_corrupted"
    assert projected["repair_policy"] == "explicit_source_required"


def test_specification_reports_exact_corrupted_paths() -> None:
    projected = specification_projection(
        {
            "change_id": "CH-1",
            "request": "Корректный запрос",
            "request_addenda": ["???? damaged"],
            "issues": [
                {
                    "issue_id": "I-1",
                    "title": "Valid title",
                    "acceptance_criteria": ["Works", "bad \ufffd value"],
                }
            ],
        }
    )
    assert projected["integrity"] == "transport_corrupted"
    assert projected["corrupted_paths"] == [
        "request_addenda[0]",
        "issues[0].acceptance_criteria[1]",
    ]
    assert projected["request_addenda"][0]["raw"] == "???? damaged"
