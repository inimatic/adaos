"""Explicit standard-tariff estimates for observed text usage, never invoices."""
from __future__ import annotations
from typing import Mapping

GPT5_STANDARD = {'model': 'gpt-5', 'snapshots': ['gpt-5-2025-08-07'],
    'input_usd_per_million': 1.25, 'cached_input_usd_per_million': 0.125, 'output_usd_per_million': 10.0,
    'source': 'https://developers.openai.com/api/docs/pricing/', 'verified_on': '2026-10-14',
    'basis': 'published_standard_text_tariff', 'currency': 'USD'}


def estimate_observed_model_cost(metrics: Mapping) -> dict | None:
    """Fail unknown; no extrapolation to other models, tiers or partial repairs."""
    if metrics.get('llm_calls') != 1 or metrics.get('repair_attempts', 0) != 0:
        return None
    model = metrics.get('resolved_model') or metrics.get('model')
    if model not in [GPT5_STANDARD['model'], *GPT5_STANDARD['snapshots']]:
        return None
    values = [metrics.get(name) for name in ('input_tokens', 'cached_input_tokens', 'output_tokens')]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    input_tokens, cached, output = values
    if cached > input_tokens:
        return None
    components = {'uncached_input': (input_tokens-cached)*1.25/1e6, 'cached_input': cached*0.125/1e6,
                  'output_including_reasoning': output*10.0/1e6}
    return {'estimated_usd': round(sum(components.values()), 9), 'basis': 'estimate',
        'components': components, 'tariff': GPT5_STANDARD,
        'scope': 'Standard API text tariff, not a Root invoice or an unobserved service-tier charge'}
