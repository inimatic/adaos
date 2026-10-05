from tools.management_read_model_benchmark import parse_server_timing, summarize


def test_timing_projection_is_numeric_and_does_not_copy_descriptions():
    assert parse_server_timing('skill-dispatch;dur=12.5, admission;dur=8;desc="private", bad;dur=1.2.3') == {
        'skill-dispatch': 12.5, 'admission': 8,
    }


def test_percentiles_report_sample_count_and_do_not_invent_missing_stages():
    samples = [{'wall_ms': value, 'response_bytes': 100, 'server_timing_ms': {}} for value in (20, 10, 30)]
    assert summarize(samples)['wall_ms'] == {'n': 3, 'p50': 20, 'p95': 30}
    assert summarize([]) == {}
    assert 'admission' not in summarize(samples)
