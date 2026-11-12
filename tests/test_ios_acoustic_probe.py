"""The Enjoy lab retries transient playback failures without changing devices."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "ios_acoustic_probe", Path(__file__).resolve().parents[1] / "tools/ios_acoustic_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_speaker_warmup_preserves_the_entire_first_word_after_one_second():
    speech = b"\x01\x00\xff\x7f\x01\x80\x34\x12"
    padded = probe.prepend_silence(speech, 16000, 1)
    assert len(padded) == 32000 + len(speech)
    assert padded[:32000] == bytes(32000)
    assert padded[32000:] == speech
    assert probe.parser().parse_args([]).lead_silence == 1


def completed(ok, code=0):
    return subprocess.CompletedProcess([], code, json.dumps({"playback_ok": ok}), "")


def test_reconnect_retries_after_two_seconds_and_stops_at_first_success():
    calls, sleeps = [], []
    results = iter([completed(False, 1), completed(True)])
    command = ["python", "probe.py", "--output", "Enjoy 30 Stereo", "--_worker"]
    def run(actual, **options):
        calls.append(actual)
        assert options["timeout"] == 20
        assert options["stdin"] == subprocess.DEVNULL
        return next(results)
    report, code = probe.run_attempts(command, 3, 2, 20, run=run, sleep=sleeps.append)
    assert code == 0 and report["playback_ok"] is True
    assert len(report["attempts"]) == 2 and sleeps == [2]
    assert calls == [command, command]


def test_failed_playback_has_four_attempts_and_only_three_delays():
    calls, sleeps = [], []
    def run(command, **options):
        calls.append(command)
        return completed(False, 1)
    report, code = probe.run_attempts(["probe"], 3, 2, 20, run=run, sleep=sleeps.append)
    assert code == 1 and report["playback_ok"] is False
    assert len(calls) == len(report["attempts"]) == 4
    assert sleeps == [2, 2, 2]


@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess([], 0, '{"playback_ok":false,"status":["output_underflow"]}', ""),
    subprocess.CompletedProcess([], 0, "not JSON", ""),
    subprocess.CompletedProcess([], 0, "[]", ""),
    subprocess.TimeoutExpired(["probe"], 20),
    OSError("Endpoint temporarily unavailable"),
])
def test_incomplete_or_failed_worker_is_not_reported_as_success(failure):
    outcomes = iter([failure, completed(True)])
    sleeps = []
    def run(command, **options):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result
    report, code = probe.run_attempts(["probe"], 3, 2, 20, run=run, sleep=sleeps.append)
    assert code == 0 and len(report["attempts"]) == 2
    assert report["attempts"][0]["ok"] is False
    assert sleeps == [2]


def test_operator_interrupt_does_not_replay_audio():
    sleeps = []
    def run(command, **options):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        probe.run_attempts(["probe"], 3, 2, 20, run=run, sleep=sleeps.append)
    assert not sleeps
