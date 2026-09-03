from __future__ import annotations

import pytest

from adaos.domain.development_escalations import parse_development_escalations


def test_parse_development_escalations_accepts_bounded_core_request() -> None:
    result = parse_development_escalations(
        """The public SDK does not expose this value.

```adaos-development-escalation
{"schema":"adaos.development_escalations.v1","items":[{"kind":"core_capability_request","summary":"Expose subscription token usage","component_ref":"core:sdk.subscription","desired_contract":"Read current plan token use and remaining allowance.","impact":"blocker","motivation":"The project must use a public SDK contract.","observed_limitation":"Only transport quota objects are public.","rejected_workarounds":[{"approach":"Read root state directly","reason":"Project code cannot access private core state."}]}]}
```
"""
    )

    assert result == [
        {
            "schema": "adaos.development_escalation.v1",
            "kind": "core_capability_request",
            "summary": "Expose subscription token usage",
            "component_ref": "core:sdk.subscription",
            "desired_contract": "Read current plan token use and remaining allowance.",
            "impact": "blocker",
            "motivation": "The project must use a public SDK contract.",
            "observed_limitation": "Only transport quota objects are public.",
            "rejected_workarounds": [
                {
                    "approach": "Read root state directly",
                    "reason": "Project code cannot access private core state.",
                }
            ],
        }
    ]


@pytest.mark.parametrize(
    "message",
    [
        "```adaos-development-escalation\n{not-json}\n```",
        """```adaos-development-escalation
{"schema":"adaos.development_escalations.v1","schema":"duplicate","items":[]}
```""",
        """```adaos-development-escalation
{"schema":"adaos.development_escalations.v1","items":[{"kind":"core_capability_request","summary":"x","component_ref":"skill:private","desired_contract":"x","impact":"blocker","observed_limitation":"x"}]}
```""",
    ],
)
def test_parse_development_escalations_rejects_untrusted_shapes(message: str) -> None:
    with pytest.raises(ValueError):
        parse_development_escalations(message)


def test_parse_development_escalations_ignores_normal_completion() -> None:
    assert parse_development_escalations("Implemented the requested UI change.") == []
