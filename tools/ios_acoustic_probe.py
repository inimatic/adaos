"""Bounded acoustic lab playback/capture with explicit Windows audio devices.

Requires numpy and sounddevice. Does not change system defaults or volume.
Raw input is discarded unless --capture-wav is explicitly supplied.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave


def prepend_silence(pcm: bytes, sample_rate: int, seconds: float) -> bytes:
    """Preserve every source sample after a real PCM16 mono quiet interval."""
    return bytes(round(sample_rate * seconds) * 2) + pcm


def play_sapi(args: argparse.Namespace, pcm: bytes | None, sample_rate: int) -> float:
    import comtypes.client
    scratch = Path(__file__).resolve().parents[1] / ".tmp/jailbreak/acoustic"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="enjoy-", dir=scratch) as folder:
        if args.say:
            synth = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
            voices = synth.GetVoices()
            russian = [voices.Item(i) for i in range(voices.Count)
                       if voices.Item(i).GetAttribute("Language") == "419"]
            if not russian:
                raise ValueError("Expected a local Russian voice")
            synth.Voice = russian[0]
            synth.Rate = -2
            destination = comtypes.client.CreateObject("SAPI.SpFileStream", dynamic=True)
            audio_format = comtypes.client.CreateObject("SAPI.SpAudioFormat", dynamic=True)
            audio_format.Type = 18  # PCM16 mono, 16 kHz.
            destination.Format = audio_format
            source = Path(folder) / "speech.wav"
            try:
                destination.Open(str(source), 3, False)
                synth.AudioOutputStream = destination
                synth.Speak(args.say, 16)  # SVSFIsNotXML.
            finally:
                destination.Close()
            with wave.open(str(source), "rb") as speech:
                sample_rate = speech.getframerate()
                pcm = speech.readframes(speech.getnframes())
        speech_seconds = len(pcm) / (sample_rate * 2)
        if not 0 < speech_seconds <= 30 or speech_seconds + args.lead_silence + 1.5 > args.seconds:
            raise ValueError("Capture duration too short for speech, leading silence and margins")
        padded = Path(folder) / "playback.wav"
        with wave.open(str(padded), "wb") as output:
            output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            output.writeframes(prepend_silence(pcm, sample_rate, args.lead_silence))
        voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
        outputs = voice.GetAudioOutputs()
        tokens = [outputs.Item(i) for i in range(outputs.Count)
                  if args.output.casefold() in outputs.Item(i).GetDescription().casefold()]
        if len(tokens) != 1:
            raise ValueError("Expected one named SAPI output")
        voice.AudioOutput = tokens[0]
        voice.Volume = 100 if args.say else round(args.gain * 100)
        fixture = comtypes.client.CreateObject("SAPI.SpFileStream", dynamic=True)
        try:
            fixture.Open(str(padded), 0, False)
            voice.SpeakStream(fixture, 0)
        finally:
            fixture.Close()
        return speech_seconds


def run_attempts(command: list[str], retries: int, delay: float, timeout: float,
                 *, run=subprocess.run, sleep=time.sleep) -> tuple[dict, int]:
    """Retry only this lab playback/capture process, refreshing devices each time."""
    attempts = []
    for number in range(1, retries + 2):
        started = time.time()
        try:
            child = run(command, stdin=subprocess.DEVNULL, capture_output=True,
                        text=True, encoding="utf-8", timeout=timeout)
            lines = child.stdout.strip().splitlines()
            result = json.loads(lines[-1]) if lines else {}
            if not isinstance(result, dict):
                raise ValueError("Worker returned a non-object report")
            ok = child.returncode == 0 and result.get("playback_ok") is True
            detail = {"attempt": number, "started_at": started, "ok": ok,
                      "exit_code": child.returncode, "result": result}
            if not ok and child.stderr:
                detail["error"] = child.stderr[-1500:]
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            ok = False
            detail = {"attempt": number, "started_at": started, "ok": False,
                      "error": str(error)[:1500]}
            result = {}
        detail["finished_at"] = time.time()
        attempts.append(detail)
        if ok:
            return dict(result, attempts=attempts, retry_delay_seconds=delay), 0
        if number <= retries:
            sleep(delay)
    return {"playback_ok": False, "attempts": attempts,
            "retry_delay_seconds": delay}, 1


def select_device(fragment: str, direction: str) -> int:
    import sounddevice as sd
    matches = [i for i, d in enumerate(sd.query_devices())
               if fragment.casefold() in d["name"].casefold()
               and d[f"max_{direction}_channels"] > 0
               and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"]
    if len(matches) != 1:
        raise ValueError(f"Expected one WASAPI {direction} device matching {fragment!r}; found {len(matches)}")
    return matches[0]


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--play-wav", type=Path)
    parser.add_argument("--say", help="Use local Russian SAPI synthesis on the selected output")
    parser.add_argument("--output", default="Enjoy 30 Stereo")
    parser.add_argument("--input", default="AN-VC500 Camera")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--gain", type=float, default=0.6)
    parser.add_argument("--capture-wav", type=Path)
    parser.add_argument("--play-only", action="store_true")
    parser.add_argument("--backend", choices=("sapi", "wasapi"), default="sapi",
                        help="Playback backend; SAPI reads a WAV's declared format directly")
    parser.add_argument("--retries", type=int, default=3, help="Additional playback attempts (default: 3)")
    parser.add_argument("--retry-delay", type=float, default=2, help="Seconds between failed attempts")
    parser.add_argument("--lead-silence", type=float, default=1,
                        help="PCM silence before each phrase so the speaker can wake (default: 1 second)")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def perform(args: argparse.Namespace) -> dict:
    import numpy as np
    import sounddevice as sd

    output = select_device(args.output, "output") if args.play_wav or args.say else None
    input_device = select_device(args.input, "input") if not args.play_only else None
    audio = None
    rate = int(sd.query_devices(output)["default_samplerate"]) if output is not None else 16000
    wav_seconds = None
    pcm = None
    original_rate = 16000
    if args.play_wav:
        with wave.open(str(args.play_wav), "rb") as wav:
            if (wav.getsampwidth() != 2 or wav.getnchannels() != 1 or
                    not 8000 <= wav.getframerate() <= 96000 or
                    not 0 < wav.getnframes() <= wav.getframerate() * 30):
                raise ValueError("Expected bounded mono PCM16 fixture")
            original_rate = wav.getframerate()
            wav_seconds = wav.getnframes() / original_rate
            pcm = wav.readframes(wav.getnframes())
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if wav_seconds + args.lead_silence + 1.5 > args.seconds:
            raise ValueError("Capture duration too short for fixture and margins")
        if args.backend == "wasapi":
            audio = np.interp(np.arange(round(len(samples) * rate / original_rate)) * original_rate / rate,
                              np.arange(len(samples)), samples).astype(np.float32) * args.gain
            audio = np.concatenate((np.zeros(round(rate * args.lead_silence), dtype=np.float32), audio))
            channels = min(2, int(sd.query_devices(output)["max_output_channels"]))
            audio = np.repeat(audio.reshape(-1, 1), channels, axis=1)

    chunks = []
    errors = []
    def capture(data, frames, timing, status):
        del frames, timing
        if status:
            errors.append(str(status))
        if sum(len(x) for x in chunks) < int(args.seconds * 16000):
            chunks.append(data[:, :1].copy())
    stream = None
    playback_started = playback_finished = None
    try:
        if input_device is not None:
            input_channels = min(2, int(sd.query_devices(input_device)["max_input_channels"]))
            stream = sd.InputStream(device=input_device, samplerate=16000, channels=input_channels,
                                    dtype="int16", callback=capture)
            stream.start()
        started = time.monotonic()
        time.sleep(0.75)
        playback_started = time.time()
        if audio is not None:
            with sd.OutputStream(device=output, samplerate=rate, channels=channels,
                                 latency="high", dtype="float32") as speaker:
                if speaker.write(audio):
                    errors.append("output_underflow")
        elif args.say or args.play_wav:
            wav_seconds = play_sapi(args, pcm, original_rate)
        playback_finished = time.time()
        time.sleep(max(0, args.seconds - (time.monotonic() - started)))
    finally:
        if stream:
            stream.stop()
            stream.close()
    captured = np.concatenate(chunks).reshape(-1) if chunks else np.array([], dtype=np.int16)
    report = {"playback_ok": not errors, "backend": args.backend,
              "output": sd.query_devices(output)["name"] if output is not None else None,
              "input": sd.query_devices(input_device)["name"] if input_device is not None else None,
              "seconds": round(len(captured) / 16000, 3), "status": errors,
              "playback_started": playback_started, "playback_finished": playback_finished,
              "fixture_seconds": wav_seconds,
              "lead_silence_seconds": args.lead_silence,
              "peak": int(np.max(np.abs(captured.astype(np.int32)))) if captured.size else 0,
              "rms": round(float(np.sqrt(np.mean(captured.astype(float)**2))), 2) if captured.size else 0,
              "raw_input_saved": bool(args.capture_wav and captured.size)}
    if args.capture_wav and captured.size:
        args.capture_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(args.capture_wav), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(captured.astype("<i2").tobytes())
    return report


def main() -> None:
    arguments = parser()
    args = arguments.parse_args()
    if args.say and (args.play_wav or len(args.say) > 400):
        arguments.error("--say accepts at most 400 characters and excludes --play-wav")
    if args.say and args.backend != "sapi":
        arguments.error("--say requires the SAPI backend")
    if not 1 <= args.seconds <= 60 or not 0 < args.gain <= 1:
        arguments.error("seconds must be 1..60 and gain 0..1")
    if not 0 <= args.retries <= 3 or not 0 <= args.retry_delay <= 10:
        arguments.error("retries must be 0..3 and retry-delay 0..10")
    if not 0 <= args.lead_silence <= 3:
        arguments.error("lead-silence must be 0..3 seconds")
    if args._worker:
        try:
            report = perform(args)
        except Exception as error:
            print(json.dumps({"playback_ok": False, "error": str(error)[:1500]}, ensure_ascii=True))
            raise SystemExit(1)
        print(json.dumps(report, ensure_ascii=True))
        raise SystemExit(0 if report["playback_ok"] else 1)
    report, code = run_attempts([sys.executable, "-B", str(Path(__file__).resolve()),
                                 *sys.argv[1:], "--_worker"],
                                args.retries, args.retry_delay, args.seconds + 15)
    print(json.dumps(report, ensure_ascii=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
