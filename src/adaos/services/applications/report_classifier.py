from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.domain.artifact_release import canonical_json_bytes

from .report_admission import DevelopmentReportClassificationUnavailable


_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_OUTPUT_FIELDS = {"category", "confidence", "tags", "summary"}


class OciDevelopmentReportClassifier:
    """Run a fixed, single-shot classifier in a network-free scratch boundary."""

    def __init__(
        self,
        *,
        state_root: Path,
        image: str,
        runtime: str = "docker",
        timeout_s: float = 60.0,
        cpu_cores: float = 1.0,
        memory_mb: int = 1024,
        max_log_bytes: int = 64 * 1024,
        max_output_bytes: int = 32 * 1024,
    ) -> None:
        self.root = (Path(state_root).expanduser().resolve() / "report-classifier").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if "," in str(self.root):
            raise ValueError("report classifier state path cannot contain a comma")
        self.image = str(image or "").strip()
        if not _IMAGE_RE.fullmatch(self.image):
            raise ValueError("report classifier image must be digest-pinned")
        self.runtime = str(runtime or "docker").strip()
        if not self.runtime or any(char in self.runtime for char in ("/", "\\")):
            raise ValueError("report classifier runtime must be an executable name")
        self.timeout_s = max(1.0, min(float(timeout_s), 600.0))
        self.cpu_cores = max(0.1, min(float(cpu_cores), 8.0))
        self.memory_mb = max(128, min(int(memory_mb), 16_384))
        self.max_log_bytes = max(4096, min(int(max_log_bytes), 1_048_576))
        self.max_output_bytes = max(1024, min(int(max_output_bytes), 262_144))

    def _provenance(self, *, input_digest: str, output_digest: str | None = None) -> dict[str, Any]:
        return {
            "schema": "adaos.application.development_report_classifier_execution.v1",
            "input_digest": input_digest,
            "output_digest": output_digest,
            "image_digest": self.image.rsplit("@", 1)[1],
            "protocol": "adaos.report-classifier.v1",
            "isolation": {
                "network": "none",
                "rootfs": "read_only",
                "capabilities": "dropped",
                "privilege_escalation": False,
                "secrets": False,
                "host_mounts": ["input_read_only", "output_scratch"],
                "cpu_cores": self.cpu_cores,
                "memory_mb": self.memory_mb,
                "timeout_s": self.timeout_s,
            },
        }

    def _unavailable(self, reason: str, *, input_digest: str) -> DevelopmentReportClassificationUnavailable:
        return DevelopmentReportClassificationUnavailable(
            reason,
            provider="local-oci",
            model=self.image,
            provenance=self._provenance(input_digest=input_digest),
        )

    @staticmethod
    def _stop(process: subprocess.Popen[bytes], runtime: str, container_name: str) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        subprocess.run(
            [runtime, "rm", "-f", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )

    def classify(
        self,
        *,
        summary: str,
        details: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        normalized = {
            "summary": str(summary),
            "details": str(details),
            "evidence": [dict(item) for item in evidence],
        }
        input_bytes = canonical_json_bytes(normalized)
        input_digest = f"sha256:{hashlib.sha256(input_bytes).hexdigest()}"
        runtime = shutil.which(self.runtime)
        if runtime is None:
            raise self._unavailable("oci_runtime_unavailable", input_digest=input_digest)
        scratch = Path(tempfile.mkdtemp(prefix="attempt-", dir=self.root)).resolve()
        input_path = scratch / "report.json"
        output_dir = scratch / "output"
        output_path = output_dir / "classification.json"
        stdout_path = scratch / "stdout.log"
        stderr_path = scratch / "stderr.log"
        container_name = f"adaos-report-{uuid.uuid4().hex[:24]}"
        process: subprocess.Popen[bytes] | None = None
        try:
            input_path.write_bytes(input_bytes)
            output_dir.mkdir()
            output_path.touch(exist_ok=False)
            try:
                output_path.chmod(0o666)
            except OSError:
                pass
            command = [
                runtime,
                "run",
                "--rm",
                "--pull",
                "never",
                "--name",
                container_name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "64",
                "--cpus",
                str(self.cpu_cores),
                "--memory",
                f"{self.memory_mb}m",
                "--memory-swap",
                f"{self.memory_mb}m",
                "--ipc",
                "none",
                "--ulimit",
                "nofile=64:64",
                "--user",
                "65532:65532",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--mount",
                f"type=bind,src={input_path},dst=/input/report.json,readonly",
                "--mount",
                f"type=bind,src={output_path},dst=/output/classification.json",
                self.image,
                "/input/report.json",
                "/output/classification.json",
            ]
            started = time.monotonic()
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    cwd=str(scratch),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=dict(os.environ),
                )
                while process.poll() is None:
                    if time.monotonic() - started > self.timeout_s:
                        self._stop(process, runtime, container_name)
                        raise self._unavailable("classifier_timeout", input_digest=input_digest)
                    if any(
                        path.is_file() and path.stat().st_size > self.max_log_bytes
                        for path in (stdout_path, stderr_path)
                    ):
                        self._stop(process, runtime, container_name)
                        raise self._unavailable("classifier_log_limit", input_digest=input_digest)
                    if output_path.stat().st_size > self.max_output_bytes:
                        self._stop(process, runtime, container_name)
                        raise self._unavailable("classifier_output_limit", input_digest=input_digest)
                    time.sleep(0.05)
            if process.returncode != 0:
                raise self._unavailable("classifier_execution_failed", input_digest=input_digest)
            if (
                not output_path.is_file()
                or output_path.is_symlink()
                or output_path.stat().st_size > self.max_output_bytes
            ):
                raise self._unavailable("classifier_output_invalid", input_digest=input_digest)
            output_bytes = output_path.read_bytes()
            try:
                output = json.loads(output_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise self._unavailable("classifier_output_invalid", input_digest=input_digest) from exc
            if not isinstance(output, dict) or set(output) - _OUTPUT_FIELDS:
                raise self._unavailable("classifier_output_invalid", input_digest=input_digest)
            output_digest = f"sha256:{hashlib.sha256(output_bytes).hexdigest()}"
            return {
                **output,
                "provider": "local-oci",
                "model": self.image,
                "status": "completed",
                "provenance": self._provenance(
                    input_digest=input_digest,
                    output_digest=output_digest,
                ),
            }
        finally:
            if process is not None and process.poll() is None:
                self._stop(process, runtime, container_name)
            shutil.rmtree(scratch, ignore_errors=True)


__all__ = ["OciDevelopmentReportClassifier"]
