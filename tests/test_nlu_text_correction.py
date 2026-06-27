from __future__ import annotations

import sys
import types

try:
    import y_py  # noqa: F401
except ImportError:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
try:
    import ypy_websocket  # noqa: F401
except ImportError:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.nlu.text_correction import correct_light_text


def test_correct_light_text_normalizes_slideshow_typo() -> None:
    result = correct_light_text("открой слайшоу")

    assert result.text == "открой слайдшоу"
    assert result.corrections == ({"from": "слайшоу", "to": "слайдшоу"},)


def test_correct_light_text_leaves_unrelated_text_unchanged() -> None:
    result = correct_light_text("Арсений, расскажи о Париже")

    assert result.text == "Арсений, расскажи о Париже"
    assert result.corrections == ()
