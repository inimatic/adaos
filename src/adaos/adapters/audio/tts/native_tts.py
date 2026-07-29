# -*- coding: utf-8 -*-
"""
Простой офлайн TTS на базе pyttsx3:
- Windows: SAPI5
- macOS: NSSpeechSynthesizer
- Linux: eSpeak/ng
"""

from __future__ import annotations
from pathlib import Path
import sys
import tempfile
from typing import Optional
import typer

try:
    import pyttsx3
except Exception as e:  # узкий импорт не критичен здесь
    pyttsx3 = None
    _import_error = e
else:
    _import_error = None


class NativeTTS:
    def __init__(
        self,
        voice: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None,
        lang_hint: Optional[str] = None,
    ):
        """
        voice: id/имя голоса (опционально)
        rate: скорость речи (слов/мин)
        volume: 0.0..1.0
        lang_hint: подстрока для выбора по языку (например, 'ru' / 'en')
        """
        if pyttsx3 is None:
            raise RuntimeError(f"pyttsx3 не установлен: {_import_error}. " "Установи: pip install pyttsx3")
        self.engine = pyttsx3.init()

        # Выбор голоса по hint/имени
        if voice or lang_hint:
            try:
                voices = self.engine.getProperty("voices") or []
                picked = None
                for v in voices:
                    v_id = getattr(v, "id", "") or ""
                    v_name = getattr(v, "name", "") or ""
                    v_langs = [str(x).lower() for x in getattr(v, "languages", [])]
                    haystack = (v_id + " " + v_name + " " + " ".join(v_langs)).lower()
                    if voice and voice.lower() in haystack:
                        picked = v
                        break
                    if not picked and lang_hint and lang_hint.lower() in haystack:
                        picked = v
                if picked:
                    self.engine.setProperty("voice", picked.id)
            except Exception:
                # Тихо игнорируем — пусть остаётся дефолт
                pass

        if rate is not None:
            try:
                self.engine.setProperty("rate", int(rate))
            except Exception:
                pass
        if volume is not None:
            try:
                vol = float(volume)
                vol = max(0.0, min(vol, 1.0))
                self.engine.setProperty("volume", vol)
            except Exception:
                pass

    def say(self, text: str) -> None:
        self.engine.say(text)
        self.engine.runAndWait()

    def synthesize(self, text: str) -> bytes:
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp_path = tmp.name
            self.engine.save_to_file(text, tmp_path)
            self.engine.runAndWait()
            return Path(tmp_path).read_bytes()
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def stop(self) -> None:
        try:
            self.engine.stop()
        except Exception:
            pass

    def __del__(self):
        # На всякий случай корректно останавливаем
        try:
            self.stop()
        except Exception:
            pass
