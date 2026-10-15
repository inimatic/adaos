import io

import pytest

from adaos.services import io_console


@pytest.mark.parametrize("entry", ["print", "print_text"])
@pytest.mark.parametrize("encoding", ["cp1251", "utf-8"])
def test_console_handles_unicode_on_windows_service_stream(monkeypatch, entry, encoding):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding)
    monkeypatch.delenv("ADAOS_TESTING", raising=False)
    monkeypatch.setattr(io_console.sys, "stdout", stream)
    text = "План → результат 🙂"
    getattr(io_console, entry)(text)
    observed = raw.getvalue().decode(encoding)
    assert "План" in observed and "результат" in observed
    if encoding == "utf-8":
        assert text in observed
    else:
        assert "\\u2192" in observed and "\\U0001f642" in observed
