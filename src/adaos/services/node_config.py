from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict
import os, uuid, yaml, sys

try:
    from adaos.services.agent_context import get_ctx, AgentContext  # type: ignore
except Exception:
    get_ctx = None  # type: ignore

    class AgentContext:
        pass  # type: ignore


def _default_base_dir() -> Path:
    env = os.environ.get("ADAOS_BASE_DIR")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "AdaOS"
    return Path.home() / ".adaos"


def _base_dir(ctx: AgentContext | None = None) -> Path:
    if ctx and getattr(ctx, "paths", None):
        return Path(getattr(ctx.paths, "base"))
    if "adaos" in sys.modules and get_ctx:
        try:
            return Path(get_ctx().paths.base_dir())  # type: ignore[attr-defined]
        except Exception:
            pass
    return _default_base_dir()


def _config_path(ctx: AgentContext | None = None) -> Path:
    p = _base_dir(ctx) / "node.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class RootOwnerProfile(TypedDict):
    owner_id: str
    subject: str | None
    scopes: list[str]
    access_expires_at: str
    hub_ids: list[str]


class RootState(TypedDict, total=False):
    profile: RootOwnerProfile
    access_token_cached: str | None
    refresh_token_fallback: str | None


@dataclass
class NodeConfig:
    node_id: str
    subnet_id: str
    role: str
    hub_url: str | None = None
    token: str | None = None
    root: RootState | None = None


def _default_conf() -> NodeConfig:
    return NodeConfig(
        node_id=str(uuid.uuid4()),
        subnet_id=str(uuid.uuid4()),
        role="hub",
        hub_url=None,
        token=os.environ.get("ADAOS_TOKEN", "dev-local-token"),
        root=None,
    )


def _normalize_root_state(raw: Any) -> RootState | None:
    if not isinstance(raw, dict):
        return None
    profile_data = raw.get("profile")
    profile: RootOwnerProfile | None = None
    if isinstance(profile_data, dict):
        owner_id = profile_data.get("owner_id")
        if isinstance(owner_id, str) and owner_id:
            subject = profile_data.get("subject")
            scopes = profile_data.get("scopes")
            expires_at = profile_data.get("access_expires_at")
            hub_ids = profile_data.get("hub_ids")
            if not isinstance(scopes, list):
                scopes = []
            else:
                scopes = [s for s in scopes if isinstance(s, str)]
            if not isinstance(hub_ids, list):
                hub_ids = []
            else:
                hub_ids = [h for h in hub_ids if isinstance(h, str)]
            if isinstance(expires_at, str):
                try:
                    datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except ValueError:
                    expires_at = datetime.now(timezone.utc).isoformat()
            else:
                expires_at = datetime.now(timezone.utc).isoformat()
            profile = RootOwnerProfile(
                owner_id=owner_id,
                subject=subject if isinstance(subject, str) else None,
                scopes=scopes,
                access_expires_at=expires_at,
                hub_ids=hub_ids,
            )
    state: RootState = {}
    if profile:
        state["profile"] = profile
    access_cached = raw.get("access_token_cached")
    if isinstance(access_cached, str) and access_cached:
        state["access_token_cached"] = access_cached
    refresh_fallback = raw.get("refresh_token_fallback")
    if isinstance(refresh_fallback, str) and refresh_fallback:
        state["refresh_token_fallback"] = refresh_fallback
    return state or None


def load_node(ctx: AgentContext | None = None) -> NodeConfig:
    path = _config_path(ctx)
    if not path.exists():
        conf = _default_conf()
        save_node(conf, ctx=ctx)
        return conf
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    node_id = data.get("node_id") or str(uuid.uuid4())
    subnet_id = data.get("subnet_id") or str(uuid.uuid4())
    role = (data.get("role") or "hub").strip().lower()
    hub_url = data.get("hub_url")
    token = data.get("token") or os.environ.get("ADAOS_TOKEN", "dev-local-token")
    root_state = _normalize_root_state(data.get("root"))
    return NodeConfig(
        node_id=node_id,
        subnet_id=subnet_id,
        role=role,
        hub_url=hub_url,
        token=token,
        root=root_state,
    )


def save_node(conf: NodeConfig, *, ctx: AgentContext | None = None) -> None:
    data = asdict(conf)
    _config_path(ctx).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def ensure_hub(conf: NodeConfig) -> None:
    if conf.role.strip().lower() != "hub":
        raise ValueError("root features are available only for nodes with role 'hub'")


def node_base_dir(ctx: AgentContext | None = None) -> Path:
    return _base_dir(ctx)


def set_role(role: str, *, hub_url: str | None = None, subnet_id: str | None = None, ctx: AgentContext | None = None) -> NodeConfig:
    role = role.lower().strip()
    if role not in ("hub", "member"):
        raise ValueError("role must be 'hub' or 'member'")
    conf = load_config(ctx=ctx)
    conf.role = role
    if subnet_id:
        conf.subnet_id = subnet_id
    conf.hub_url = hub_url if role == "member" else None
    save_config(conf, ctx=ctx)
    return conf


def load_config(ctx: AgentContext | None = None) -> NodeConfig:
    return load_node(ctx=ctx)


def save_config(conf: NodeConfig, *, ctx: AgentContext | None = None) -> None:
    save_node(conf, ctx=ctx)
