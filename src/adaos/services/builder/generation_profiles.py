"""Explicit per-project DEV qualification profiles, not model/account defaults."""
from __future__ import annotations


def profile_metadata(name):
    if name in (None, "default"):
        return {}
    modes = {"s2-inline-typed-v1": "typed",
             "s2-inline-json-envelope-v1": "json_text_envelope"}
    if not isinstance(name, str) or name not in modes:
        raise ValueError("Unknown Prototype generation profile")
    return {"builder_generation_profile": name, "builder_generation_run_protocol": "v1",
            "builder_task_pipeline": "1", "builder_context_strategy": "full",
            "builder_context_retrieval": "inline",
            "builder_interpretation_output_mode": modes[name]}
