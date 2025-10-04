from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
import typer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from adaos.apps.cli.root_ops import (
    RootCliError,
    key_path,
    keys_dir,
    load_root_cli_config,
    save_root_cli_config,
)

app = typer.Typer(help="Developer utilities for Root integration")
hub_app = typer.Typer(help="Subnet bootstrap commands")
node_app = typer.Typer(help="Node registration commands")
app.add_typer(hub_app, name="hub")
app.add_typer(node_app, name="node")


def _root_url(base: str, suffix: str) -> str:
    return f"{(base or '').rstrip('/')}{suffix}"


def _post_json(
    url: str,
    *,
    payload: dict,
    headers: Optional[dict[str, str]] = None,
    cert: Optional[tuple[str, str]] = None,
    verify: bool | str = False,
    timeout: float = 30.0,
) -> dict:
    try:
        response = httpx.post(url, json=payload, headers=headers, cert=cert, verify=verify, timeout=timeout)
    except httpx.RequestError as exc:
        raise RootCliError(f"POST {url} failed: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text.strip()
        raise RootCliError(f"POST {url} failed with status {response.status_code}: {detail or 'no body'}")
    try:
        return response.json()
    except ValueError as exc:
        raise RootCliError(f"Root response is not valid JSON: {exc}") from exc


def _write_private_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        pass


def _write_pem(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data.endswith("\n"):
        data = data + "\n"
    path.write_text(data, encoding="utf-8")


@hub_app.command("init")
def hub_init(
    token: str = typer.Option(..., "--token", help="One-time bootstrap token"),
    name: Optional[str] = typer.Option(None, "--name", help="Optional subnet display name"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing hub credentials"),
) -> None:
    try:
        config = load_root_cli_config()
    except RootCliError as err:
        typer.secho(str(err), fg=typer.colors.RED)
        raise typer.Exit(1)

    keys_dir()
    hub_key_path = key_path('hub_key')
    hub_cert_path = key_path('hub_cert')
    ca_path = key_path('ca_cert')

    if not force and (hub_key_path.exists() or hub_cert_path.exists()):
        typer.secho("Hub credentials already exist; use --force to overwrite.", fg=typer.colors.RED)
        raise typer.Exit(1)

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "subnet:pending")]))
        .sign(key, hashes.SHA256())
    )
    payload = {"csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")}
    if name:
        payload["subnet_name"] = name

    url = _root_url(config.root_base, "/v1/subnets/register")
    data = _post_json(
        url,
        payload=payload,
        headers={"X-Bootstrap-Token": token},
        verify=False,
    )

    subnet_id = data.get("subnet_id")
    cert_pem = data.get("cert_pem")
    ca_pem = data.get("ca_pem")
    if not subnet_id or not cert_pem or not ca_pem:
        raise RootCliError("Root response is missing required fields")

    _write_private_key(hub_key_path, key)
    _write_pem(hub_cert_path, cert_pem)
    _write_pem(ca_path, ca_pem)

    config.subnet_id = subnet_id
    config.keys.hub.key = str(hub_key_path)
    config.keys.hub.cert = str(hub_cert_path)
    config.keys.ca = str(ca_path)
    save_root_cli_config(config)

    typer.secho(f"Registered subnet: {subnet_id}", fg=typer.colors.GREEN)
    typer.echo(f"Hub key saved to {hub_key_path}")
    typer.echo(f"Hub certificate saved to {hub_cert_path}")
    typer.echo(f"CA certificate saved to {ca_path}")
    forge_info = data.get("forge")
    if isinstance(forge_info, dict):
        repo = forge_info.get("repo")
        path_hint = forge_info.get("path")
        typer.echo(f"Forge repository: {repo} ({path_hint})")


@node_app.command("register")
def node_register(
    token: Optional[str] = typer.Option(None, "--token", help="Bootstrap token for node provisioning"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing node credentials"),
) -> None:
    try:
        config = load_root_cli_config()
    except RootCliError as err:
        typer.secho(str(err), fg=typer.colors.RED)
        raise typer.Exit(1)

    if not config.subnet_id:
        typer.secho("Subnet is not registered. Run 'adaos dev hub init' first.", fg=typer.colors.RED)
        raise typer.Exit(1)

    keys_dir()
    node_key_path = key_path('node_key')
    node_cert_path = key_path('node_cert')
    hub_key_path = key_path('hub_key')
    hub_cert_path = key_path('hub_cert')
    ca_path = key_path('ca_cert')

    if not force and (node_key_path.exists() or node_cert_path.exists()):
        typer.secho("Node credentials already exist; use --force to overwrite.", fg=typer.colors.RED)
        raise typer.Exit(1)

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "node:pending"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, f"subnet:{config.subnet_id}"),
        ]
    )
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    payload = {"csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")}

    headers: dict[str, str] = {}
    cert: Optional[tuple[str, str]]
    verify: bool | str
    if token:
        headers["X-Bootstrap-Token"] = token
        payload["subnet_id"] = config.subnet_id
        cert = None
        verify = False
    else:
        if not hub_cert_path.exists() or not hub_key_path.exists():
            typer.secho(
                "Hub credentials are missing; provide --token or run 'adaos dev hub init' again.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        if not ca_path.exists():
            typer.secho("CA certificate is missing; run 'adaos dev hub init' first.", fg=typer.colors.RED)
            raise typer.Exit(1)
        cert = (str(hub_cert_path), str(hub_key_path))
        verify = str(ca_path)

    url = _root_url(config.root_base, "/v1/nodes/register")
    data = _post_json(url, payload=payload, headers=headers or None, cert=cert, verify=verify)

    node_id = data.get("node_id")
    cert_pem = data.get("cert_pem")
    ca_pem = data.get("ca_pem")
    if not node_id or not cert_pem:
        raise RootCliError("Root response is missing required fields")

    _write_private_key(node_key_path, key)
    _write_pem(node_cert_path, cert_pem)
    if ca_pem:
        _write_pem(ca_path, ca_pem)

    config.node_id = node_id
    config.keys.node.key = str(node_key_path)
    config.keys.node.cert = str(node_cert_path)
    if ca_pem:
        config.keys.ca = str(ca_path)
    save_root_cli_config(config)

    typer.secho(f"Registered node: {node_id}", fg=typer.colors.GREEN)
    typer.echo(f"Node key saved to {node_key_path}")
    typer.echo(f"Node certificate saved to {node_cert_path}")


__all__ = ["app"]
