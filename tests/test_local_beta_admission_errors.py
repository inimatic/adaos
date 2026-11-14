"""A rejected Beta identity must reach browser callers as a CORS-visible 409."""
from types import SimpleNamespace
import json

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
import pytest

from adaos.apps.api import tool_bridge
from adaos.apps.api.tool_bridge import ToolCall
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.applications import runtime_selection
from adaos.services.applications import local_beta_admission
from adaos.services.applications.local_beta_admission import admission_path
from adaos.services.artifact_pipeline.trial_activation import (
    TRIAL_ACTIVATION_SCHEMA, TrialActivationStore,
)


@pytest.mark.parametrize("failure", ["stale_release", "binding_changed", "receipt_changed"])
def test_rejected_beta_identity_returns_409_with_cors(tmp_path, monkeypatch, failure):
    diagnostics = []
    monkeypatch.setattr(local_beta_admission, "_log", SimpleNamespace(
        warning=lambda message, *args: diagnostics.append(message % args),
    ))
    candidate = "candidate-beta"
    selected_release = "sha256:" + "a" * 64
    requested_release = "sha256:" + "b" * 64 if failure == "stale_release" else selected_release
    binding = {"kind": "native-trial", "path": "private-runtime-location"}
    admission = {
        "schema": "adaos.application.local_beta_admission.v1",
        "status": "active", "candidate_id": candidate,
        "release_digest": selected_release,
        "runtime_binding_digest": canonical_payload_digest(binding),
    }
    admission["receipt_digest"] = canonical_payload_digest(admission)
    if failure == "receipt_changed":
        admission["receipt_digest"] = "sha256:" + "0" * 64
    path = admission_path(tmp_path, candidate)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(admission), encoding="utf-8")
    if failure == "binding_changed":
        binding = {**binding, "channel": "changed"}
    activation = TrialActivationStore(tmp_path / "artifact_pipeline/trial-activations").path(candidate)
    activation.parent.mkdir(parents=True)
    activation.write_text(json.dumps({
        "schema": TRIAL_ACTIVATION_SCHEMA, "status": "completed", "runtime_binding": binding,
    }), encoding="utf-8")

    class Store:
        def __init__(self, state):
            pass

        def get_runtime_selection(self, webspace, application):
            return SimpleNamespace(
                release_digest=selected_release, runtime_root_ref="trial:" + candidate,
                revision=1, to_dict=lambda: {"webspace_id": webspace, "application_id": application},
            )

        def _release_path(self, application, release):
            return tmp_path / "release.json"

    monkeypatch.setattr(runtime_selection, "ApplicationStore", Store)
    monkeypatch.setattr(tool_bridge, "_webspace_uses_dev_runtime", lambda *_args: False)
    monkeypatch.setattr(tool_bridge, "_EXACT_TRIAL_ASYNC_CACHE", {})
    monkeypatch.setattr(tool_bridge, "_EXACT_TRIAL_ASYNC_INFLIGHT", {})

    async def must_not_execute(*_args, **_kwargs):
        pytest.fail("A rejected Beta must not execute tools or return cached tool results")

    monkeypatch.setattr(tool_bridge, "_call_tool_impl", must_not_execute)
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST"])

    @app.post("/api/tools/call")
    async def invoke(body: ToolCall, request: Request, response: Response):
        return await tool_bridge._call_tool_with_identity(body, request, response, ctx)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/tools/call", headers={"Origin": "http://127.0.0.1:8100"}, json={
            "tool": "infra_ops_skill:distribution_snapshot", "arguments": {}, "intent": "read",
            "context": {"webspace_id": "root-desktop", "application_id": "infra_ops",
                        "application_release_digest": requested_release},
        })
    assert response.status_code == 409, response.text
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.json()["detail"]["error"] == "trial_runtime_unavailable"
    if failure != "receipt_changed":
        expected_field = "release_digest" if failure == "stale_release" else "runtime_binding_digest"
        assert "mismatched_fields=" + expected_field in diagnostics[0]
        assert "private-runtime-location" not in diagnostics[0]
