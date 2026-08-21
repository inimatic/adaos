from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from adaos.domain.execution import ExecutionNetworkPolicy, ExecutionSpec
from adaos.sdk.developer import validation
from adaos.services import developer_project_validation as service


def test_developer_validation_requires_narrow_capability_and_calls_service(monkeypatch) -> None:
    ctx = SimpleNamespace()
    admitted: list[str] = []
    calls: list[tuple[object, str, bool, bool, bool, int | None]] = []
    monkeypatch.setattr(validation, "require_ctx", lambda _operation: ctx)
    monkeypatch.setattr(
        validation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )

    def fake_validate(
        context,
        project_id,
        *,
        strict,
        probe_tools,
        run_packaged_tests,
        test_timeout_seconds,
    ):
        calls.append(
            (
                context,
                project_id,
                strict,
                probe_tools,
                run_packaged_tests,
                test_timeout_seconds,
            )
        )
        return {"ok": True, "digest": "sha256:" + "1" * 64}

    monkeypatch.setattr(
        "adaos.services.developer_project_validation.validate_dev_skill",
        fake_validate,
    )

    result = validation.validate_skill(
        "candidate",
        run_tests=False,
        test_timeout_seconds=180,
    )

    assert result["ok"] is True
    assert admitted == ["builder.project_validation"]
    assert calls == [(ctx, "candidate", True, True, False, 180)]


def test_developer_source_inspection_is_bounded_and_capability_gated(
    monkeypatch, tmp_path: Path
) -> None:
    dev_skills = tmp_path / "dev" / "skills"
    source = dev_skills / "candidate"
    source.mkdir(parents=True)
    (source / "skill.yaml").write_text("name: candidate\n", encoding="utf-8")
    (source / "handlers.py").write_text("VALUE = 'real path'\n", encoding="utf-8")
    (source / "weights.bin").write_bytes(b"not source text")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "handlers.pyc").write_bytes(b"ignored")
    ctx = SimpleNamespace(paths=SimpleNamespace(dev_skills_dir=lambda: dev_skills))
    admitted: list[str] = []
    monkeypatch.setattr(validation, "require_ctx", lambda _operation: ctx)
    monkeypatch.setattr(
        validation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )

    snapshot = validation.inspect_skill_source("candidate")

    assert admitted == ["builder.project_validation"]
    assert snapshot["schema"] == "adaos.developer.source_snapshot.v1"
    assert snapshot["project_ref"] == "skill:candidate"
    assert [item["path"] for item in snapshot["files"]] == [
        "handlers.py",
        "skill.yaml",
    ]
    assert snapshot["files"][0]["text"].splitlines() == ["VALUE = 'real path'"]
    assert snapshot["omitted"] == [
        {
            "path": "weights.bin",
            "size_bytes": len(b"not source text"),
            "digest": snapshot["omitted"][0]["digest"],
            "reason": "non_text",
        }
    ]
    assert snapshot["digest"].startswith("sha256:")


def test_developer_invocation_reuses_the_same_narrow_capability(monkeypatch) -> None:
    ctx = SimpleNamespace()
    admitted: list[str] = []
    monkeypatch.setattr(validation, "require_ctx", lambda _operation: ctx)
    monkeypatch.setattr(
        validation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )
    monkeypatch.setattr(
        "adaos.services.developer_project_validation.activate_dev_skill",
        lambda context, project_id: {"ok": context is ctx, "project_ref": f"skill:{project_id}"},
    )
    monkeypatch.setattr(
        "adaos.services.developer_project_validation.invoke_dev_skill",
        lambda context, project_id, operation_id, arguments, timeout=None: {
            "ok": context is ctx,
            "project_id": project_id,
            "operation_id": operation_id,
            "arguments": arguments,
            "timeout": timeout,
        },
    )

    activated = validation.activate_skill("candidate")
    invoked = validation.invoke_skill("candidate", "smoke", {"seed": 17}, timeout=30)

    assert activated["ok"] is True
    assert invoked["operation_id"] == "smoke"
    assert admitted == ["builder.project_validation", "builder.project_validation"]


def test_developer_trial_execution_is_capability_gated(monkeypatch) -> None:
    ctx = SimpleNamespace()
    admitted: list[str] = []
    monkeypatch.setattr(validation, "require_ctx", lambda _operation: ctx)
    monkeypatch.setattr(
        validation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )
    monkeypatch.setattr(
        "adaos.services.developer_project_validation.execute_dev_spec",
        lambda context, project_id, value, *, idempotency_key, timeout=None: {
            "ok": context is ctx,
            "project_id": project_id,
            "value": value,
            "key": idempotency_key,
            "timeout": timeout,
        },
    )

    result = validation.execute_spec(
        "candidate",
        {"schema": "adaos.execution.spec.v1"},
        idempotency_key="smoke-17",
        timeout=60,
    )

    assert result["ok"] is True
    assert result["key"] == "smoke-17"
    assert admitted == ["builder.project_validation"]


@pytest.mark.parametrize("network_mode", ["offline", "unrestricted"])
def test_developer_trial_uses_candidate_owned_working_directory(
    monkeypatch, tmp_path: Path, network_mode: str
) -> None:
    dev_skills = tmp_path / "dev" / "skills"
    source = dev_skills / "candidate"
    source.mkdir(parents=True)
    script = source / "runner.py"
    script.write_text(
        "from pathlib import Path\n"
        "import json, os\n"
        "from adaos.sdk.skill_env import skill_data_root\n"
        "Path('result.json').write_text(json.dumps({'ok': True, 'contract': os.environ['TEST_CONTRACT'], 'data_root': str(skill_data_root())}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    runtime_bucket = "v0.1"
    runtime_source = (
        dev_skills
        / ".runtime"
        / "candidate"
        / runtime_bucket
        / "slots"
        / "A"
        / "src"
        / "skills"
        / "candidate"
    )
    runtime_source.mkdir(parents=True)
    manifest = runtime_source / "resolved.manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    data_root = dev_skills / ".runtime" / "candidate" / runtime_bucket / "data"
    workdir = data_root / "attempts" / "candidate-smoke"
    workdir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    package_path = tmp_path / "package"
    package_path.mkdir()
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            dev_skills_dir=lambda: dev_skills,
            state_dir=lambda: state_dir,
            package_path=lambda: package_path,
        )
    )

    class _Manager:
        @staticmethod
        def dev_runtime_status(_project_id: str) -> dict:
            return {
                "resolved_manifest": str(manifest),
                "runtime_bucket": runtime_bucket,
            }

    monkeypatch.setattr(service, "_manager", lambda _ctx: _Manager())
    execution = ExecutionSpec(
        spec_id="candidate-smoke",
        owner_ref="skill:candidate",
        command=(sys.executable, str(script.resolve())),
        working_directory=str(workdir),
        network=ExecutionNetworkPolicy(mode=network_mode),
        environment={"TEST_CONTRACT": "preserved"},
        expected_outputs=("result.json",),
    )

    receipt = service.execute_dev_spec(
        ctx,
        "candidate",
        execution.to_dict(),
        idempotency_key=f"smoke-17-{network_mode}",
        timeout=30,
    )

    assert receipt["ok"] is True
    assert receipt["documents"]["result.json"] == {
        "ok": True,
        "contract": "preserved",
        "data_root": str(data_root.resolve()),
    }
    assert receipt["provider"]["process_tree_isolated"] is True
    assert receipt["provider"]["network_intent"] == network_mode
    assert receipt["provider"]["network_enforced"] is False
    assert receipt["limits"]["wall_time_exceeded"] is False
    assert json.loads((workdir / "result.json").read_text(encoding="utf-8")) == {
        "ok": True,
        "contract": "preserved",
        "data_root": str(data_root.resolve()),
    }


def test_developer_trial_timeout_terminates_the_owned_process_tree(
    monkeypatch, tmp_path: Path
) -> None:
    dev_skills = tmp_path / "dev" / "skills"
    source = dev_skills / "candidate"
    source.mkdir(parents=True)
    script = source / "runner.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    runtime_bucket = "v0.1"
    runtime_source = (
        dev_skills
        / ".runtime"
        / "candidate"
        / runtime_bucket
        / "slots"
        / "A"
        / "src"
        / "skills"
        / "candidate"
    )
    runtime_source.mkdir(parents=True)
    manifest = runtime_source / "resolved.manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    workdir = dev_skills / ".runtime" / "candidate" / runtime_bucket / "data" / "internal" / "attempt"
    workdir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    package_path = tmp_path / "package"
    package_path.mkdir()
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            dev_skills_dir=lambda: dev_skills,
            state_dir=lambda: state_dir,
            package_path=lambda: package_path,
        )
    )

    class _Manager:
        @staticmethod
        def dev_runtime_status(_project_id: str) -> dict:
            return {"resolved_manifest": str(manifest), "runtime_bucket": runtime_bucket}

    monkeypatch.setattr(service, "_manager", lambda _ctx: _Manager())
    execution = ExecutionSpec(
        spec_id="candidate-timeout",
        owner_ref="skill:candidate",
        command=(sys.executable, str(script.resolve())),
        working_directory=str(workdir),
        network=ExecutionNetworkPolicy(mode="offline"),
    )

    receipt = service.execute_dev_spec(
        ctx,
        "candidate",
        execution.to_dict(),
        idempotency_key="smoke-timeout",
        timeout=0.5,
    )

    child_pid = int((workdir / "child.pid").read_text(encoding="utf-8"))
    for _ in range(20):
        if not psutil.pid_exists(child_pid):
            break
        time.sleep(0.05)
    assert receipt["ok"] is False
    assert receipt["failure"] == "wall_time_exceeded"
    assert receipt["provider"]["process_tree_terminated"] is True
    assert receipt["limits"]["wall_time_exceeded"] is True
    assert not psutil.pid_exists(child_pid)


def test_developer_trial_rejects_foreign_working_directory(monkeypatch, tmp_path: Path) -> None:
    dev_skills = tmp_path / "dev" / "skills"
    source = dev_skills / "candidate"
    source.mkdir(parents=True)
    script = source / "runner.py"
    script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    runtime_bucket = "v0.1"
    runtime_source = dev_skills / ".runtime" / "candidate" / runtime_bucket / "src"
    runtime_source.mkdir(parents=True)
    manifest = runtime_source / "resolved.manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    ctx = SimpleNamespace(
        paths=SimpleNamespace(
            dev_skills_dir=lambda: dev_skills,
            state_dir=lambda: tmp_path / "state",
            package_path=lambda: tmp_path,
        )
    )

    class _Manager:
        @staticmethod
        def dev_runtime_status(_project_id: str) -> dict:
            return {
                "resolved_manifest": str(manifest),
                "runtime_bucket": runtime_bucket,
            }

    monkeypatch.setattr(service, "_manager", lambda _ctx: _Manager())
    execution = ExecutionSpec(
        spec_id="candidate-smoke",
        owner_ref="skill:candidate",
        command=(sys.executable, str(script.resolve())),
        working_directory=str(tmp_path / "foreign"),
        network=ExecutionNetworkPolicy(mode="offline"),
    )

    with pytest.raises(PermissionError, match="working directory"):
        service.execute_dev_spec(
            ctx,
            "candidate",
            execution.to_dict(),
            idempotency_key="smoke-foreign",
            timeout=30,
        )
