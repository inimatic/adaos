from __future__ import annotations

import pytest

from adaos.domain.development_feedback import parse_development_feedback


def test_parse_development_feedback_envelope() -> None:
    items = parse_development_feedback(
        """Implemented the bounded patch.

```adaos-development-feedback
{"schema":"adaos.development_feedback_output.v1","items":[{"category":"ambiguous_contract","summary":"The retry contract has two plausible stale-value semantics.","blocking":false,"confidence":0.91,"impact":["comprehension","reliability"],"target_refs":["sdk:resources.query"],"details":"The docs and schema do not select one behavior.","recommendation":"Declare last-value behavior in the read policy.","evidence_refs":[{"type":"file","ref":"skills/demo/webui.json"}]}]}
```
"""
    )

    assert items[0]["category"] == "ambiguous_contract"
    assert items[0]["target_refs"] == ["sdk:resources.query"]


def test_parse_development_feedback_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        parse_development_feedback(
            """```adaos-development-feedback
{"schema":"adaos.development_feedback_output.v1","items":[{"category":"guess","summary":"This is not a supported category.","blocking":false,"confidence":0.5,"impact":[],"target_refs":[],"details":"","recommendation":"","evidence_refs":[]}]}
```
"""
        )
