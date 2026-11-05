from uuid import uuid4

from adaos.services import conversation_store
from adaos.services.agent_context import get_ctx


def test_new_message_indexing_avoids_corpus_scan_and_preserves_retry_and_edit():
    conversation_store.ensure_schema()
    identity = uuid4().hex
    conversation = 'conv.fts-insert.' + identity
    message = 'msg.fts-insert.' + identity
    conversation_store.upsert_conversation(
        conversation_id=conversation, webspace_id='desktop', owner='core:test')
    arguments = dict(conversation_id=conversation, webspace_id='desktop',
        channel_id='builder', owner='core:test', role='hub', text='originalcatalogtoken',
        payload={'id': message, 'text': 'originalcatalogtoken'})
    statements = []
    with get_ctx().sql.connect() as con:
        con.set_trace_callback(statements.append)
        first = conversation_store.append_message(**arguments, connection=con)
        repeated = conversation_store.append_message(**arguments, connection=con)
        con.commit()
        assert con.execute('SELECT COUNT(*) FROM conversation_messages_fts WHERE message_id=?',
                           (message,)).fetchone()[0] == 1
    assert first['id'] == repeated['id'] == message
    assert not any('DELETE FROM CONVERSATION_MESSAGES_FTS WHERE' in statement.upper()
                   for statement in statements)
    assert [row['id'] for row in conversation_store.search_messages(
        'originalcatalogtoken', conversation_id=conversation)] == [message]
    conversation_store.update_message(message, text='updatedcatalogtoken')
    assert not conversation_store.search_messages('originalcatalogtoken', conversation_id=conversation)
    assert [row['id'] for row in conversation_store.search_messages(
        'updatedcatalogtoken', conversation_id=conversation)] == [message]
