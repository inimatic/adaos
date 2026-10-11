"""Explicit model-family controls shared by Builder primary and repair calls."""
from __future__ import annotations


def reasoning_for_model(model: str | None, effort: str = "low") -> dict[str, str] | None:
    token = str(model or "").strip().lower()
    if token.startswith("gpt-6"):
        supported = {"low", "medium", "high", "xhigh", "max"}
        if token.startswith(("gpt-6-sol", "gpt-6-luna")):
            supported.add("none")
    elif token.startswith("gpt-5-pro") or not token.startswith("gpt-5"):
        return None
    else:
        supported = {"minimal", "low", "medium", "high"}
    if effort not in supported:
        raise ValueError(f"Unsupported Builder reasoning effort {effort!r} for {model!r}")
    return {"effort": effort}


def supports_sampling(model: str | None) -> bool:
    return not str(model or "").strip().lower().startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))
