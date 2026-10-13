import json
import logging

from adaos.services.turn_timing import record_turn_timing


def test_timing_receipt_is_numeric_bounded_and_has_no_context_body(monkeypatch):
    captured = []
    monkeypatch.setattr(logging.getLogger('adaos.builder.timing'), 'info', lambda _template, payload: captured.append(payload))
    receipt = record_turn_timing(meta={'turn_trace_id': 'trace.valid', 'source_message_id': 'm.user',
            'webspace_id': 'desktop', 'authorization': 'private', 'prompt': 'private input'},
            stage='native_turn', timings={'total_ms': 25.1234, 'bad_ms': float('nan'),
                'negative_ms': -1, 'flag_ms': True, 'prompt': 'private input'})
    assert receipt['timings_ms'] == {'total_ms': 25.12}
    assert receipt['source_message_id'] == 'm.user'
    assert 'private' not in json.dumps(receipt)
    assert json.loads(captured[-1]) == receipt
