"""Explicit per-project DEV qualification profiles, not model/account defaults."""
from __future__ import annotations


def profile_metadata(name):
    if name in (None, "default"):
        return {}
    modes = {"s2-inline-typed-v1": "typed",
             "s2-inline-json-envelope-v1": "json_text_envelope",
             "s2-inline-semantic-input-v1":"typed",
             "s1-facts-inline-semantic-input-v1":"typed",
             "s1-facts-inline-first-pass-v1":"typed",
             "s2-inline-no-repair-v1":"typed"}
    if not isinstance(name, str) or name not in modes:
        raise ValueError("Unknown Prototype generation profile")
    return {"builder_generation_profile": name, "builder_generation_run_protocol": "v1",
            "builder_task_pipeline": "1", "builder_context_strategy": "full",
            "builder_context_retrieval": "inline",
            "builder_interpretation_output_mode": modes[name],
            **({'builder_semantic_input_profile':'semantic-input-v1'} if name not in {'s2-inline-typed-v1','s2-inline-json-envelope-v1'} else {}),
            **({'builder_task_interpretation_mode':'deterministic_facts'} if name.startswith('s1-facts-') else {}),
            **({'builder_provider_call_limit':1} if name=='s1-facts-inline-first-pass-v1' else {}),
            **({'builder_provider_call_limit':2} if name=='s2-inline-no-repair-v1' else {})}
