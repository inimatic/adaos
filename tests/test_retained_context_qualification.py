import importlib.util
from pathlib import Path

import pytest


def load_helper():
    path = Path(__file__).resolve().parents[1] / 'e2e/stand/qualify-retained-context-latency.py'
    spec = importlib.util.spec_from_file_location('retained_context_qualification', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('task, expected', [
    (None, ['prototype-review']),
    ('inventory-procurement-ru', None),
    ('support-desk-ru', ['prototype-review', 'prototype-capabilities', 'prototype-interactions']),
])
def test_retained_qualification_uses_full_matched_task_probes(task, expected):
    assert load_helper().retained_probes(task) == expected


def test_unknown_task_cannot_silently_become_latency_only():
    with pytest.raises(ValueError, match='Unknown'):
        load_helper().retained_probes('unregistered-task')
