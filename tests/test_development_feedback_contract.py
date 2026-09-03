from __future__ import annotations

import pytest

from adaos.domain.development_feedback import parse_development_feedback


def test_parse_development_feedback_envelope() -> None:
    items = parse_development_feedback(
        """Implemented the bounded patch.

```adaos-development-feedback
{"schema":"adaos.development_feedback_output.v1","items":[{"category":"ambiguous_contract","summary":"The retry contract has two plausible stale-value semantics.","blocking":false,"confidence":0.91,"impact":["comprehension","reliability"],"target_refs":["sdk:resources.query"],"details":"The docs and schema do not select one behavior.","recommendation":"Declare last-value behavior in the read policy.","evidence_refs":[{"type":"file","ref":"skills/demo/webui.json"}],"application_trace":{"schema":"adaos.development.application_trace.v1","contract_ref":"sdk:resources.query","operation_id":"resources.query","input_summary":"One redacted filter and explicit refresh.","expected_behavior":"Return the current bounded projection.","observed_behavior":"Returned stale data after a successful acknowledgement.","validation_result":"failed","user_response":"Refresh did not update the table.","trace_refs":[{"type":"trace","ref":"trace:query.demo"}]}}]}
```
"""
    )

    assert items[0]["category"] == "ambiguous_contract"
    assert items[0]["target_refs"] == ["sdk:resources.query"]
    assert items[0]["application_trace"]["operation_id"] == "resources.query"
    assert items[0]["application_trace"]["validation_result"] == "failed"


def test_parse_development_feedback_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        parse_development_feedback(
            """```adaos-development-feedback
{"schema":"adaos.development_feedback_output.v1","items":[{"category":"guess","summary":"This is not a supported category.","blocking":false,"confidence":0.5,"impact":[],"target_refs":[],"details":"","recommendation":"","evidence_refs":[]}]}
```
"""
        )


def test_parse_development_feedback_rejects_invented_application_trace_fields() -> None:
    with pytest.raises(ValueError, match="application_trace"):
        parse_development_feedback(
            """```adaos-development-feedback
{"schema":"adaos.development_feedback_output.v1","items":[{"category":"validation_gap","summary":"The operation failed.","blocking":false,"confidence":1,"impact":["reliability"],"target_refs":["sdk:demo.run"],"details":"Observed failure.","recommendation":"Inspect the trace.","evidence_refs":[],"application_trace":{"schema":"adaos.development.application_trace.v1","contract_ref":"sdk:demo.run","operation_id":"demo.run","input_summary":"redacted","expected_behavior":"success","observed_behavior":"failure","validation_result":"failed","raw_secret":"must not pass","trace_refs":[]}}]}
```
"""
        )
