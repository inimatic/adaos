import json
import logging
import queue

from adaos.services.log_redaction import redact_log_text, redact_log_value
from adaos.services.logging import JsonFormatter, NonBlockingQueueHandler, append_rotating_json_lines
from adaos.sdk.core.logging import JsonFormatter as SdkJsonFormatter


def test_redaction_preserves_routing_and_usage_evidence():
    value = {"url": "wss://host/yws?token=private-value&webspace_id=desktop&%61pi_key=private-key",
             "headers": {"Authorization": "Bearer private-auth", "Cookie": "session=private-cookie"},
             "error": 'failed {"refresh_token": "private-refresh"}',
             "input_tokens": 512, "token_budget": 2048, "session_id": "trace-session"}
    result = redact_log_value(value)
    assert "private-" not in json.dumps(result)
    assert result["input_tokens"] == 512 and result["token_budget"] == 2048
    assert result["session_id"] == "trace-session"
    assert "webspace_id=desktop" in result["url"]
    assert value["headers"]["Authorization"] == "Bearer private-auth"
    assert redact_log_value(result) == result


def test_redaction_covers_credentials_in_exception_and_serialized_strings():
    for value in (
        "connect https://user:private-password@host/path failed",
        "Authorization: Bearer private-auth",
        'request {"token":"private-token","scenario":"drive"}',
        "https://host/media?X-Amz-Signature=private-signature&part=1",
        "GET /yws?token=private-token&webspace=desktop HTTP/1.1",
    ):
        assert "private-" not in redact_log_text(value)


def test_all_diagnostic_sinks_redact_before_persistence(tmp_path):
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                               'accepted %s', ("/yws?token=private-token&webspace=desktop",), None)
    record.extra = {"details": {"authorization": "private-auth"}}
    for formatter in (JsonFormatter(), SdkJsonFormatter()):
        assert "private-" not in formatter.format(record)
    handler = NonBlockingQueueHandler(queue.Queue(), level=logging.INFO)
    try:
        prepared = handler.prepare(record)
        assert "private-" not in logging.Formatter().format(prepared)
        assert "private-" not in json.dumps(prepared.extra)
    finally:
        handler.close()
    path = tmp_path / "ui.jsonl"
    append_rotating_json_lines(path, [{"details": record.extra, "message": record.getMessage()}])
    assert "private-" not in path.read_text()


def test_sidecar_diagnostics_and_console_are_redacted(tmp_path, capsys):
    from adaos.services.realtime_sidecar import RealtimeSidecarServer, _append_realtime_sidecar_diag

    path = tmp_path / "sidecar.jsonl"
    original = {"last_error": "connect wss://host/ws?token=private-token", "frames": 42}
    _append_realtime_sidecar_diag(path, original)
    assert "private-" not in path.read_text()
    assert json.loads(path.read_text())["frames"] == 42
    assert "private-token" in original["last_error"]
    RealtimeSidecarServer._log(None, "failed /yws?token=private-token")
    assert "private-" not in capsys.readouterr().out
