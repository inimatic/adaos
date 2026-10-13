import pytest

from adaos.services.router.voice_chat_stream import (
    _merge_voice_chat_optimistic_message,
    _voice_chat_projection_is_stale,
)


@pytest.mark.parametrize(('current', 'incoming', 'stale'), [
    # Epoch time must never outrank a canonical sequence number.
    ({'id': 'optimistic', 'ts': 1791855570}, {'id': 'receipt', 'seq': 4906, 'ts': 1791855571}, False),
    ({'id': 'receipt', 'seq': 4906, 'ts': 1791855571}, {'id': 'new', 'ts': 1791855572}, False),
    ({'id': 'new', 'ts': 1791855572}, {'id': 'old', 'seq': 4906, 'ts': 1791855571}, True),
    ({'id': 'new', 'seq': 5, 'ts': 1}, {'id': 'old', 'seq': 4, 'ts': 2}, True),
    ({'id': 'old', 'seq': 4, 'ts': 2}, {'id': 'new', 'seq': 5, 'ts': 1}, False),
    ({'id': 'same', 'ts': 2}, {'id': 'same', 'seq': 5, 'ts': 1}, False),
    ({'id': 'bad', 'seq': 'invalid', 'ts': float('nan')}, {'id': 'good', 'seq': 5}, False),
])
def test_staleness_compares_only_comparable_clocks(current, incoming, stale):
    assert _voice_chat_projection_is_stale([current], [incoming]) is stale


def test_ledger_can_collapse_duplicate_receipts_without_losing_the_user():
    user = {'id': 'user', 'from': 'user', 'seq': 4905, 'ts': 1791855570}
    receipt = {'id': 'user.result', 'from': 'hub', 'seq': 4906, 'ts': 1791855571}
    assert not _voice_chat_projection_is_stale([receipt, receipt], [user, receipt])


def test_optimistic_retry_replaces_same_id_and_does_not_inflate_count():
    user = {'id': 'user', 'from': 'user', 'conversation_id': 'conv', 'thread_id': 'topic'}
    receipt = {**user, 'id': 'user.result', 'from': 'hub'}
    cached = {'messages': [user, receipt, receipt], 'total_message_count': 3}
    updated = _merge_voice_chat_optimistic_message(cached, {**receipt, 'text': 'Updated receipt'})
    assert [item['id'] for item in updated['messages']] == ['user', 'user.result']
    assert updated['messages'][-1]['text'] == 'Updated receipt'
    assert updated['total_message_count'] == 2
    assert cached['messages'][-1].get('text') is None


def test_optimistic_new_topic_never_relabels_an_old_tail():
    cached = {'messages': [{'id': 'old', 'conversation_id': 'conv', 'thread_id': 'other-topic'}],
              'total_message_count': 100}
    user = {'id': 'user', 'conversation_id': 'conv', 'thread_id': 'topic'}
    updated = _merge_voice_chat_optimistic_message(cached, user)
    assert updated == {'messages': [user], 'total_message_count': 1}


def test_optimistic_tail_keeps_older_history_count_in_the_same_topic():
    user = {'id': 'user', 'conversation_id': 'conv', 'thread_id': 'topic'}
    updated = _merge_voice_chat_optimistic_message({'messages': [user], 'total_message_count': 100},
        {**user, 'id': 'receipt'})
    assert updated['total_message_count'] == 101
