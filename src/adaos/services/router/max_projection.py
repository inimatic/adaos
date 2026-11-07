"""Project MAX responses through the durable Core transport queue."""
from __future__ import annotations
import hashlib
from typing import Any, Mapping
from adaos.services import max_delivery


def text_chunks(text: str, limit: int = 3000) -> list[str]:
    """Bound UTF-16 units as well as Unicode characters accepted by MAX clients."""
    result, part, units = [], [], 0
    for char in str(text or '').strip():
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > limit:
            result.append(''.join(part))
            part, units = [], 0
        part.append(char)
        units += size
    if part:
        result.append(''.join(part))
    return result


def messages_for(payload: Mapping[str, Any]) -> list[dict]:
    text = str(payload.get('text') or '').strip()
    messages = [{'type': 'text', 'text': part} for part in text_chunks(text)]
    rows = []
    for action in payload.get('actions') or []:
        if not isinstance(action, Mapping) or len(rows) >= 8:
            continue
        label, token = str(action.get('label') or '')[:64], str(action.get('token') or '')
        config = action.get('action') if isinstance(action.get('action'), Mapping) else {}
        params = config.get('params') if isinstance(config.get('params'), Mapping) else {}
        url = str(action.get('url') or (params.get('url') if config.get('type') == 'openUrl' else '') or '')
        if label and url.startswith(('https://', 'http://')) and len(url) <= 2048:
            rows.append([{'text': label, 'url': url}])
        elif label and token and len(token) <= 1024:
            rows.append([{'text': label, 'callback_data': token}])
    if rows and messages:
        messages[0]['keyboard'] = {'inline_keyboard': rows}
    return messages


def project(payload: Mapping[str, Any], meta: Mapping[str, Any], *, connection=None, operation_key: str | None = None,
            source_message_id: str | None = None) -> bool:
    if meta.get('io_type') != 'max' or meta.get('max_delivery_handled') or meta.get('output_delivery_owned'):
        return False
    text = str(payload.get('text') or '').strip()
    if not text or str(payload.get('from') or 'hub') in {'human', 'user'}:
        return False
    target = {k: meta.get(k) for k in ('hub_id', 'bot_id', 'chat_id', 'user_id', 'binding_id', 'webspace_id')}
    messages = messages_for(payload)
    correlation = str(payload.get('id') or meta.get('request_id') or meta.get('update_id') or '')
    digest = hashlib.sha256((correlation + ':' + text).encode()).hexdigest()
    key = 'max-dialog:' + hashlib.sha256(str(target).encode()).hexdigest()[:16] + ':' + digest
    max_delivery.queue(operation_key=operation_key or key, target=target, messages=messages, connection=connection,
                       source_message_id=source_message_id)
    return True


def consumed(interaction: Mapping[str, Any], response: Mapping[str, Any]) -> bool:
    meta = response.get('metadata') if isinstance(response.get('metadata'), Mapping) else {}
    if meta.get('io_type') != 'max' or not meta.get('max_source_message_id') or not meta.get('max_callback_id'):
        return False
    command = response.get('consumed_command')
    if response.get('status', 'answered') != 'answered' or not isinstance(command, Mapping) or not command:
        return False
    label = str(command.get('label') or command.get('command') or 'действие')[:128]
    text = str(interaction.get('prompt') or '')[:2800] + '\n\n✓ Выбрано: ' + label
    key = 'max-consumed:' + str(meta.get('binding_id')) + ':' + str(response.get('response_id') or interaction.get('interaction_id'))
    max_delivery.queue(operation_key=key, target=meta, messages=[{'type': 'text', 'text': text}],
                       options={'edit_message_id': str(meta['max_source_message_id']), 'callback_id': str(meta['max_callback_id'])})
    return True
