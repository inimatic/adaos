"""Bounded acoustic lab playback/capture with explicit Windows audio devices.

Requires numpy and sounddevice. Does not change system defaults or volume.
Raw input is discarded unless --capture-wav is explicitly supplied.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import wave

import numpy as np
import sounddevice as sd


def select_device(fragment: str, direction: str) -> int:
    matches = [i for i, d in enumerate(sd.query_devices())
               if fragment.casefold() in d["name"].casefold()
               and d[f"max_{direction}_channels"] > 0
               and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"]
    if len(matches) != 1:
        raise ValueError(f"Expected one WASAPI {direction} device matching {fragment!r}; found {len(matches)}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--play-wav", type=Path)
    parser.add_argument("--output", default="Enjoy 30 Stereo")
    parser.add_argument("--input", default="AN-VC500 Camera")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--gain", type=float, default=0.6)
    parser.add_argument("--capture-wav", type=Path)
    parser.add_argument("--play-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60 or not 0 < args.gain <= 1:
        parser.error("seconds must be 1..60 and gain 0..1")
    output = select_device(args.output, "output") if args.play_wav else None
    input_device = select_device(args.input, "input") if not args.play_only else None
    audio = None
    rate = int(sd.query_devices(output)["default_samplerate"]) if output is not None else 16000
    if args.play_wav:
        with wave.open(str(args.play_wav), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1 or wav.getnframes() > wav.getframerate() * 30:
                raise ValueError("Expected bounded mono PCM16 fixture")
            original_rate = wav.getframerate()
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
        audio = np.interp(np.arange(round(len(samples) * rate / original_rate)) * original_rate / rate,
                          np.arange(len(samples)), samples).astype(np.float32) * args.gain
        if len(audio) / rate + 1.5 > args.seconds:
            raise ValueError("Capture duration too short for fixture and margins")
        # Match the stereo endpoint's channel count explicitly. A mono shared
        # WASAPI stream is not reliable with some Bluetooth stereo drivers.
        channels = min(2, int(sd.query_devices(output)["max_output_channels"]))
        audio = np.repeat(audio.reshape(-1, 1), channels, axis=1)
    chunks = []
    errors = []
    def capture(data, frames, timing, status):
        del frames, timing
        if status:
            errors.append(str(status))
        if sum(len(x) for x in chunks) < int(args.seconds * 16000):
            chunks.append(data.copy())
    stream = None
    try:
        if input_device is not None:
            stream = sd.InputStream(device=input_device, samplerate=16000, channels=1,
                                    dtype="int16", callback=capture)
            stream.start()
        started = time.monotonic()
        time.sleep(0.75)
        if audio is not None:
            with sd.OutputStream(device=output, samplerate=rate, channels=channels,
                                 latency="high", dtype="float32") as speaker:
                if speaker.write(audio):
                    errors.append("output_underflow")
        time.sleep(max(0, args.seconds - (time.monotonic() - started)))
    finally:
        if stream:
            stream.stop()
            stream.close()
    captured = np.concatenate(chunks).reshape(-1) if chunks else np.array([], dtype=np.int16)
    report = {"output": sd.query_devices(output)["name"] if output is not None else None,
              "input": sd.query_devices(input_device)["name"] if input_device is not None else None,
              "seconds": round(len(captured) / 16000, 3), "status": errors,
              "peak": int(np.max(np.abs(captured.astype(np.int32)))) if captured.size else 0,
              "rms": round(float(np.sqrt(np.mean(captured.astype(float)**2))), 2) if captured.size else 0,
              "raw_input_saved": bool(args.capture_wav and captured.size)}
    if args.capture_wav and captured.size:
        args.capture_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(args.capture_wav), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(captured.astype("<i2").tobytes())
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
