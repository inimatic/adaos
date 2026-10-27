"""Exact authority admission collapses equal keys, never unrelated I/O."""
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_selection as selections


@pytest.fixture
def exact(monkeypatch, tmp_path):
    with selections._EXACT_TRIAL_CACHE_LOCK:
        selections._EXACT_TRIAL_CACHE.clear()
        selections._EXACT_TRIAL_SELECTION_FINGERPRINT_CACHE.clear()
        selections._EXACT_TRIAL_SELECTION_EXPIRES_AT.clear()
        assert not selections._EXACT_TRIAL_FLIGHTS
    fingerprint = (("authority", 1, 1),)
    monkeypatch.setattr(selections, '_exact_trial_selection_stat_fingerprint', lambda *_args: fingerprint)
    monkeypatch.setattr(selections, '_exact_trial_selection_fingerprint', lambda *_args: fingerprint)
    monkeypatch.setattr(selections, '_selected_trial_exact_uncached', lambda *args: args[3])
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    def read(application='first', component='widget'):
        return selections.selected_trial_exact(ctx, 'isolated', application, 'digest', 'skill', component)
    return read


def test_unrelated_authority_does_not_wait_for_a_cold_resolution(exact, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def resolve(*args):
        if args[3] == 'first':
            entered.set()
            assert release.wait(5)
        return args[3]
    monkeypatch.setattr(selections, '_selected_trial_exact_uncached', resolve)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(exact)
        try:
            assert entered.wait(2)
            assert executor.submit(exact, 'second').result(timeout=1) == 'second'
        finally:
            release.set()
        assert first.result(timeout=2) == 'first'
    assert not selections._EXACT_TRIAL_FLIGHTS


def test_exact_resolution_does_not_share_the_legacy_catalog_lock(exact):
    with ThreadPoolExecutor(max_workers=1) as executor:
        with selections._SELECTED_TRIAL_CACHE_LOCK:
            assert executor.submit(exact).result(timeout=1) == 'first'


def test_equal_keys_share_one_cold_resolution(exact, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def resolve(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return args[3]
    monkeypatch.setattr(selections, '_selected_trial_exact_uncached', resolve)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(exact) for _ in range(8)]
        try:
            assert entered.wait(2)
        finally:
            release.set()
        assert [future.result(timeout=2) for future in futures] == ['first']*8
    assert calls == [True]
    assert not selections._EXACT_TRIAL_FLIGHTS


def test_exact_cache_and_metadata_are_bounded_together(exact):
    for number in range(selections._SELECTED_TRIAL_CACHE_MAX+20):
        assert exact(component=str(number)) == 'first'
    assert len(selections._EXACT_TRIAL_CACHE) == selections._SELECTED_TRIAL_CACHE_MAX
    assert set(selections._EXACT_TRIAL_CACHE) == set(selections._EXACT_TRIAL_SELECTION_FINGERPRINT_CACHE)
    assert set(selections._EXACT_TRIAL_CACHE) == set(selections._EXACT_TRIAL_SELECTION_EXPIRES_AT)
    assert not selections._EXACT_TRIAL_FLIGHTS


def test_successful_stat_recheck_refreshes_the_short_collapse_window(exact, monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(selections.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(selections, '_exact_trial_selection_stat_fingerprint',
                        lambda *_args: calls.append(True) or (("authority", 1, 1),))
    exact()
    assert len(calls) == 2  # Before and after the cold resolution.
    clock[0] = 2.0
    exact()
    assert len(calls) == 3
    clock[0] = 2.1
    exact()
    assert len(calls) == 3
