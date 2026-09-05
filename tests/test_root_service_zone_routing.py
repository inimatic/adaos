from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import types
import uuid

import pytest
from adaos.domain.artifact_release import ArtifactSourceRef

fake_y_py = types.SimpleNamespace(
    YDoc=type("YDoc", (), {}),
    apply_update=lambda *args, **kwargs: None,
)
sys.modules.setdefault("y_py", fake_y_py)
fake_ystore_module = types.ModuleType("ypy_websocket.ystore")
fake_ystore_module.BaseYStore = object
fake_ystore_module.YDocNotFound = RuntimeError
fake_ypy_websocket = types.ModuleType("ypy_websocket")
fake_ypy_websocket.ystore = fake_ystore_module
sys.modules.setdefault("ypy_websocket", fake_ypy_websocket)
sys.modules.setdefault("ypy_websocket.ystore", fake_ystore_module)

from adaos.services.node_config import NodeConfig, RootSettings
from adaos.services.root.client import RootHttpError
from adaos.services.root.service import RootDeveloperService, RootServiceError


class _DummyBus:
    def publish(self, event) -> None:
        return None


class _DummyPaths:
    def __init__(self, base: Path) -> None:
        self._base = base

    def base_dir(self) -> Path:
        return self._base

    def workspace_dir(self) -> Path:
        return self._base / "workspace"


def _workspace_tmp_dir() -> Path:
    path = Path("artifacts") / "test_tmp" / f"root-service-zone-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _install_dummy_ctx(monkeypatch: pytest.MonkeyPatch, base_dir: Path) -> None:
    ctx = SimpleNamespace(bus=_DummyBus(), paths=_DummyPaths(base_dir))
    monkeypatch.setattr("adaos.services.root.service.get_ctx", lambda: ctx)
    monkeypatch.setattr("adaos.services.node_config.get_ctx", lambda: ctx)


def test_root_service_client_uses_stored_effective_root_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dummy_ctx(monkeypatch, _workspace_tmp_dir())
    monkeypatch.setenv("ADAOS_ZONE_ID", "ru")
    cfg = NodeConfig(
        node_id="node-1",
        subnet_id="subnet-1",
        role="hub",
        root_settings=RootSettings(base_url="https://ru.api.inimatic.com"),
    )

    service = RootDeveloperService(config_loader=lambda: cfg, config_saver=lambda _cfg: None)

    assert service._client(cfg).base_url == "https://ru.api.inimatic.com"


def test_root_service_client_keeps_explicit_non_default_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dummy_ctx(monkeypatch, _workspace_tmp_dir())
    monkeypatch.setenv("ADAOS_ZONE_ID", "ru")
    cfg = NodeConfig(
        node_id="node-1",
        subnet_id="subnet-1",
        role="hub",
        root_settings=RootSettings(base_url="https://custom-root.example"),
    )

    service = RootDeveloperService(config_loader=lambda: cfg, config_saver=lambda _cfg: None)

    assert service._client(cfg).base_url == "https://custom-root.example"


def test_project_candidate_keeps_exact_pushed_component_source_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    project_dir = workspace / "projects" / "media"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(
        "schema: adaos.project.v1\nid: media\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    exact_ref = ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/adaos-registry",
        revision="a" * 40,
        path_scope=("subnets/sn_test/nodes/node_test/skills/media_skill/",),
    )
    captured: dict = {}

    class _Publication:
        def load_pushed_source(self, kind: str, name: str):
            assert (kind, name) == ("skill", "media_skill")
            return SimpleNamespace(source_ref=exact_ref, source_tree="b" * 40)

        def verify_pushed_source(self, pushed, source: Path) -> None:
            assert pushed.source_ref == exact_ref
            assert source == workspace / "skills" / "media_skill"

        def prepare_project_candidate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                candidate=SimpleNamespace(to_dict=lambda: {"candidate_id": "candidate-media"}),
                plan=SimpleNamespace(
                    release=SimpleNamespace(to_dict=lambda: {"project_id": "media"})
                ),
                trial_workspace=tmp_path / "trial",
                trial_activation={"status": "active"},
            )

    service = RootDeveloperService(config_loader=lambda: None, config_saver=lambda _cfg: None)
    monkeypatch.setattr(service, "_load_config", lambda: object())
    monkeypatch.setattr(service, "_workspace_root", lambda _cfg: workspace)
    monkeypatch.setattr(service, "_artifact_publication_service", lambda _cfg: _Publication())
    monkeypatch.setattr(
        "adaos.services.root.service.project_source_snapshot",
        lambda **_kwargs: {"source_revision": "sha256:" + "c" * 64},
    )

    result = service.prepare_project_candidate(
        "media",
        source_kind="skill",
        source_name="media_skill",
        source_revision=exact_ref.revision,
        change_ids=("change-1",),
    )

    assert result["candidate"]["candidate_id"] == "candidate-media"
    assert result["lifecycle_phase"] == "beta"
    assert captured["source_ref"] == exact_ref
    assert captured["source_ref"].path_scope == (
        "subnets/sn_test/nodes/node_test/skills/media_skill/",
    )
    assert captured["release_source_ref"].forge == "content-addressed-dev"
    assert captured["release_source_ref"].path_scope == ("projects/media/",)
    assert captured["release_validation_evidence"][0]["builder"] == "adaos.dev.project.push"


def test_project_candidate_can_resolve_primary_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "dev"
    project_dir = workspace / "projects" / "media"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(
        """schema: adaos.project.v1
kind: project
id: media
version: 1.0.0
profiles: []
components:
  owned:
    - ref: scenario:media
      role: primary
      exposure: application
      lifecycle: bound
      relations: [uses]
  dependencies: []
entrypoints: []
catalog:
  title: Media
  description: ''
  categories: []
  tags: []
publication:
  stage: alpha
  visibility: unlisted
  channel: stable
install:
  default: false
  features: []
lifecycle:
  uninstall:
    components: remove_if_unreferenced
    runtime_data: retain
    source_artifacts: retain
""",
        encoding="utf-8",
    )
    checkpoint = SimpleNamespace(
        source_ref=SimpleNamespace(revision="a" * 40)
    )
    publication = SimpleNamespace(
        load_pushed_source=lambda kind, name: (
            checkpoint
            if (kind, name) == ("scenario", "media")
            else (_ for _ in ()).throw(AssertionError((kind, name)))
        )
    )
    service = RootDeveloperService(config_loader=lambda: object(), config_saver=lambda _cfg: None)
    monkeypatch.setattr(service, "_workspace_root", lambda _cfg: workspace)
    monkeypatch.setattr(service, "_artifact_publication_service", lambda _cfg: publication)
    captured: dict[str, object] = {}

    def prepare(project_id, **kwargs):
        captured.update({"project_id": project_id, **kwargs})
        return {"candidate": {"candidate_id": "candidate-media"}}

    monkeypatch.setattr(service, "prepare_project_candidate", prepare)

    result = service.prepare_project_candidate_from_primary_checkpoint(
        "media",
        change_ids=("change-1",),
        validation_evidence={"status": "passed"},
        target_webspace_id="desktop-dev",
    )

    assert result["candidate"]["candidate_id"] == "candidate-media"
    assert captured["source_kind"] == "scenario"
    assert captured["source_name"] == "media"
    assert captured["source_revision"] == "a" * 40
    assert captured["change_ids"] == ("change-1",)
    assert captured["target_webspace_id"] == "desktop-dev"


def test_project_candidate_reports_registry_phase_and_promotion_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_dummy_ctx(monkeypatch, tmp_path)
    source_receipt = {
        "status": "completed",
        "repository": "origin",
        "branch": "main",
        "commit": "b" * 40,
    }

    class _Publication:
        def get_candidate(self, candidate_id):
            assert candidate_id == "candidate-media"
            return SimpleNamespace(
                status="accepted",
                to_dict=lambda: {
                    "candidate_id": candidate_id,
                    "project_id": "media",
                    "status": "accepted",
                },
            )

        def load_promotion(self, candidate_id):
            assert candidate_id == "candidate-media"
            return {
                "status": "completed",
                "receipts": {"source_registry_published": source_receipt},
            }

        def get_trial_activation(self, candidate_id):
            assert candidate_id == "candidate-media"
            return {"status": "active"}

    service = RootDeveloperService(
        config_loader=lambda: object(),
        config_saver=lambda _cfg: None,
    )
    monkeypatch.setattr(
        service,
        "_artifact_publication_service",
        lambda _cfg: _Publication(),
    )

    result = service.get_artifact_candidate("candidate-media")

    assert result["lifecycle_phase"] == "registry"
    assert result["promotion"]["receipts"]["source_registry_published"] == source_receipt


def test_promoted_project_source_publication_is_path_scoped_and_receipted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    git_calls: list[tuple[str, object]] = []

    class _Git:
        def changed_files(self, _root, subpath=None):
            return [str(subpath)] if subpath == "projects/media" else []

        def commit_subpath(self, _root, **kwargs):
            git_calls.append(("commit", kwargs))
            return "b" * 40

        def push(self, _root, **kwargs):
            git_calls.append(("push", kwargs))

        def current_commit(self, _root):
            return "b" * 40

    receipt_calls: list[dict[str, object]] = []
    release = SimpleNamespace(
        release=SimpleNamespace(
            project_id="media",
            version="1.2.0",
            release_digest="sha256:" + "a" * 64,
            components=(
                SimpleNamespace(kind="scenario", artifact_id="media"),
                SimpleNamespace(kind="skill", artifact_id="media_skill"),
            ),
        )
    )

    class _Publication:
        def verify_promoted_workspace_source(self, candidate_id):
            assert candidate_id == "candidate-media"
            return {"status": "passed"}

        def get_candidate_release(self, candidate_id):
            assert candidate_id == "candidate-media"
            return release

        def record_source_registry_publication(self, candidate_id, **kwargs):
            receipt_calls.append({"candidate_id": candidate_id, **kwargs})
            return {"status": "completed", **kwargs}

    ctx = SimpleNamespace(
        bus=_DummyBus(),
        paths=_DummyPaths(tmp_path),
        git=_Git(),
        settings=SimpleNamespace(
            git_author_name="AdaOS Test",
            git_author_email="test@adaos.local",
        ),
    )
    monkeypatch.setattr("adaos.services.root.service.get_ctx", lambda: ctx)
    service = RootDeveloperService(
        config_loader=lambda: object(),
        config_saver=lambda _cfg: None,
    )
    monkeypatch.setattr(
        service,
        "_artifact_publication_service",
        lambda _cfg: _Publication(),
    )

    result = service.publish_project_candidate_source(
        "candidate-media",
        remote="registry",
        branch="main",
        message="publish media",
    )

    assert result["status"] == "published"
    commit = dict(git_calls[0][1])
    assert commit["subpath"] == (
        "projects/media",
        "scenarios/media",
        "skills/media_skill",
        "registry.json",
    )
    assert git_calls[1] == ("push", {"remote": "registry", "branch": "main"})
    assert receipt_calls[0]["commit"] == "b" * 40
    assert receipt_calls[0]["paths"] == commit["subpath"]


def test_root_init_reports_zone_aware_handshake_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dummy_ctx(monkeypatch, _workspace_tmp_dir())
    monkeypatch.setenv("ADAOS_ZONE_ID", "ru")
    cfg = NodeConfig(
        node_id="node-1",
        subnet_id="subnet-1",
        role="hub",
        root_settings=RootSettings(base_url="https://ru.api.inimatic.com"),
    )
    service = RootDeveloperService(config_loader=lambda: cfg, config_saver=lambda _cfg: None)
    monkeypatch.setattr(
        service,
        "_register_hub",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RootHttpError(
                "POST /v1/bootstrap_token failed: _ssl.c:999: The handshake operation timed out",
                status_code=0,
            )
        ),
    )

    with pytest.raises(RootServiceError) as exc_info:
        service.init(root_token="dev-root-token")

    message = str(exc_info.value)
    assert "https://ru.api.inimatic.com" in message
    assert "ADAOS_ZONE_ID=ru" in message


def test_root_init_rotates_keypair_for_explicit_preferred_subnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = _workspace_tmp_dir()
    _install_dummy_ctx(monkeypatch, base_dir)
    cfg = NodeConfig(
        node_id="node-1",
        subnet_id="sn_b083ff0c",
        role="hub",
        root_settings=RootSettings(base_url="https://ru.api.inimatic.com"),
    )
    service = RootDeveloperService(config_loader=lambda: cfg, config_saver=lambda _cfg: None)

    key_path = cfg.hub_key_path()
    cert_path = cfg.hub_cert_path()
    ca_path = cfg.ca_cert_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text("old-key", encoding="utf-8")
    cert_path.write_text("old-cert-for-sn-b083ff0c", encoding="utf-8")
    ca_path.write_text("old-ca", encoding="utf-8")

    ensure_calls: list[bool] = []
    registered_subnets: list[str | None] = []

    def _acceptable(cert_pem: str, *, subnet_id: str, owner_id: str | None = None) -> bool:
        return cert_pem == "new-cert-for-sn-c3d1fc00" and subnet_id == "sn_c3d1fc00"

    def _ensure_keypair(_cfg: NodeConfig, *, force_new: bool = False):
        ensure_calls.append(force_new)
        return key_path, object()

    def _register_hub(*args, subnet_id: str | None = None, **kwargs):
        registered_subnets.append(subnet_id)
        return {
            "subnet_id": subnet_id,
            "cert_pem": "new-cert-for-sn-c3d1fc00",
            "ca_pem": "new-ca",
        }

    monkeypatch.setattr(service, "_hub_certificate_is_acceptable", _acceptable)
    monkeypatch.setattr(service, "_ensure_hub_keypair", _ensure_keypair)
    monkeypatch.setattr(service, "_plain_verify", lambda _cfg: True)
    monkeypatch.setattr(service, "_client", lambda _cfg: object())
    monkeypatch.setattr(service, "_prepare_workspace", lambda _cfg, owner: base_dir / "dev" / "sn_c3d1fc00")
    monkeypatch.setattr(service, "_register_hub", _register_hub)

    result = service.init(root_token="dev-root-token", preferred_subnet_id="sn_c3d1fc00")

    assert ensure_calls == [True]
    assert registered_subnets == ["sn_c3d1fc00"]
    assert result.subnet_id == "sn_c3d1fc00"
    assert cfg.subnet_id == "sn_c3d1fc00"


def test_root_init_fails_when_root_returns_different_preferred_subnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = _workspace_tmp_dir()
    _install_dummy_ctx(monkeypatch, base_dir)
    cfg = NodeConfig(
        node_id="node-1",
        subnet_id="sn_c3d1fc00",
        role="hub",
        root_settings=RootSettings(base_url="https://ru.api.inimatic.com"),
    )
    service = RootDeveloperService(config_loader=lambda: cfg, config_saver=lambda _cfg: None)

    key_path = cfg.hub_key_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(service, "_ensure_hub_keypair", lambda _cfg, *, force_new=False: (key_path, object()))
    monkeypatch.setattr(service, "_plain_verify", lambda _cfg: True)
    monkeypatch.setattr(service, "_client", lambda _cfg: object())
    monkeypatch.setattr(
        service,
        "_register_hub",
        lambda *args, **kwargs: {
            "subnet_id": "sn_0e8c7326",
            "cert_pem": "new-cert-for-sn-0e8c7326",
            "ca_pem": "new-ca",
        },
    )

    with pytest.raises(RootServiceError) as exc_info:
        service.init(root_token="dev-root-token", preferred_subnet_id="sn_c3d1fc00")

    message = str(exc_info.value)
    assert "sn_0e8c7326" in message
    assert "sn_c3d1fc00" in message
