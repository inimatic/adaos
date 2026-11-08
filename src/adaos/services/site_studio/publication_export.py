from __future__ import annotations

from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from adaos.services.public_site_delivery import build_delivery_bundle


_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def export_publication(
    database_path: Path,
    files_root: Path,
    output: Path,
    *,
    canonical_base_url: str | None = None,
) -> dict[str, Any]:
    """Export one exact active Site Studio revision for public delivery."""

    database_path = database_path.resolve(strict=True)
    files_root = files_root.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError("publication output must not already exist")
    if not files_root.is_dir():
        raise ValueError("Site Studio files root must be a directory")
    database = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        project_rows = database.execute(
            "SELECT project_id,body FROM site_studio_projects ORDER BY project_id"
        ).fetchall()
        if len(project_rows) != 1:
            raise ValueError("publication requires one retained Site Studio project")
        project_id, raw_project = project_rows[0]
        project = json.loads(raw_project)
        active_revision = str(project.get("active_revision_id") or "")
        revision_row = database.execute(
            "SELECT source_digest,artifact_digest,source_body FROM site_studio_revisions "
            "WHERE project_id=? AND revision_id=?",
            (project_id, active_revision),
        ).fetchone()
        if not revision_row:
            raise ValueError("active Site Studio revision is unavailable")
        source_digest, artifact_digest, raw_source = revision_row
        source = json.loads(raw_source)
        if semantic_digest(source) != source_digest:
            raise ValueError("active Site Studio source digest mismatch")
        artifact_row = database.execute(
            "SELECT body FROM site_studio_artifacts "
            "WHERE digest=? AND kind='compiled_site_revision'",
            (artifact_digest,),
        ).fetchone()
        if not artifact_row:
            raise ValueError("active compiled Site Studio artifact is unavailable")
        artifacts = json.loads(artifact_row[0])
    finally:
        database.close()
    if semantic_digest(artifacts) != artifact_digest:
        raise ValueError("compiled Site Studio artifact digest mismatch")
    source_artifact_digest = artifact_digest
    if canonical_base_url:
        artifacts = bind_canonical_base_url(artifacts, canonical_base_url)
        artifact_digest = semantic_digest(artifacts)
    output.mkdir(parents=True)
    for relative, value in sorted(artifacts.items()):
        target = admitted_target(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            target.write_text(value, encoding="utf-8", newline="\n")
        else:
            target.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    projection = artifacts.get("site.projection.json")
    if not isinstance(projection, dict):
        raise ValueError("compiled Site Studio projection is unavailable")
    for asset in projection.get("assets", []):
        copy_asset(files_root, output, asset)
    bundle = build_delivery_bundle(output)
    return {
        "schema": "adaos.site_studio.publication_export.v1",
        "project_id": project_id,
        "site_id": bundle.site_id,
        "revision_id": active_revision,
        "source_digest": source_digest,
        "artifact_digest": artifact_digest,
        "source_artifact_digest": source_artifact_digest,
        "canonical_url": normalize_canonical_base_url(canonical_base_url)
        if canonical_base_url
        else "",
        "deployment_digest": bundle.deployment_digest,
        "bundle_digest": bundle.manifest["bundle_digest"],
        "artifacts": len(bundle.manifest["artifacts"]),
    }


def bind_canonical_base_url(
    artifacts: dict[str, Any], canonical_base_url: str
) -> dict[str, Any]:
    """Bind release metadata to its stable public address without changing source."""

    result = deepcopy(artifacts)
    base_url = normalize_canonical_base_url(canonical_base_url)
    projection = result.get("site.projection.json")
    deployment = result.get("site.deployment.json")
    discovery = result.get(".well-known/adaos-site.json")
    if not all(isinstance(value, dict) for value in (projection, deployment, discovery)):
        raise ValueError("compiled Site Studio release metadata is unavailable")

    route_urls: list[str] = []
    for route_entry in projection.get("routes", []):
        route_ref = str(route_entry.get("artifact_ref") or "")
        route = result.get(route_ref)
        if not isinstance(route, dict):
            raise ValueError("compiled Site Studio route artifact is unavailable")
        route_url = canonical_route_url(base_url, str(route_entry.get("path") or ""))
        route["canonical_url"] = route_url
        head = route.get("head")
        if isinstance(head, dict):
            open_graph = head.get("open_graph")
            if isinstance(open_graph, dict):
                open_graph["og:url"] = route_url
        for entry in route.get("json_ld", []):
            if isinstance(entry, dict) and "url" in entry:
                entry["url"] = route_url
        route_entry["artifact_digest"] = semantic_digest(route)
        route_urls.append(route_url)

    projection_body = {
        key: value for key, value in projection.items() if key != "projection_digest"
    }
    projection["projection_digest"] = semantic_digest(projection_body)
    deployment["projection_digest"] = projection["projection_digest"]
    deployment_body = {
        key: value for key, value in deployment.items() if key != "deployment_digest"
    }
    deployment["deployment_digest"] = semantic_digest(deployment_body)
    discovery["canonical_origin"] = base_url.rstrip("/")
    discovery["projection_digest"] = projection["projection_digest"]
    result["sitemap.xml"] = sitemap(route_urls)
    result["robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n"
    for route_entry in projection.get("routes", []):
        route_ref = str(route_entry.get("artifact_ref") or "")
        route = result.get(route_ref)
        if isinstance(route, dict):
            result[f"routes/{route_entry['id']}.md"] = route_markdown(route)
    result["llms.txt"] = llms_text(discovery, result)
    return result


def normalize_canonical_base_url(value: str | None) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("canonical public base URL is invalid")
    path = "/" + parsed.path.strip("/") if parsed.path.strip("/") else ""
    return urlunsplit(("https", parsed.netloc, path + "/", "", ""))


def canonical_route_url(base_url: str, route_path: str) -> str:
    if not route_path.startswith("/") or "?" in route_path or "#" in route_path:
        raise ValueError("canonical route path is invalid")
    suffix = route_path.lstrip("/")
    return base_url if not suffix else base_url + suffix


def sitemap(route_urls: list[str]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    lines.extend(f"  <url><loc>{escape(url)}</loc></url>" for url in route_urls)
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def route_markdown(route: dict[str, Any]) -> str:
    head = route.get("head") or {}
    title = str(head.get("title") or route.get("route_id") or "Route").strip()
    description = str(head.get("description") or "").strip()
    lines = [f"# {title}", "", f"Canonical: {route['canonical_url']}"]
    if description:
        lines.extend(["", description])
    visible = [str(item).strip() for item in route.get("visible_content") or []]
    if visible:
        lines.extend(["", "## Content", ""])
        lines.extend(f"- {item}" for item in visible if item)
    resources = route.get("public_resource_refs") or []
    if resources:
        lines.extend(["", "## Structured data", ""])
        lines.extend(f"- [{item['id']}]({item['ref']})" for item in resources)
    return "\n".join(lines) + "\n"


def llms_text(discovery: dict[str, Any], artifacts: dict[str, Any]) -> str:
    lines = [f"# {discovery['name']}", "", f"Canonical: {discovery['canonical_origin']}"]
    routes = discovery.get("routes") or []
    if routes:
        lines.extend(["", "## Routes", ""])
        for item in routes:
            route_ref = str(item.get("artifact_ref") or "").removeprefix("../")
            route = artifacts.get(route_ref) or {}
            head = route.get("head") or {}
            title = str(head.get("title") or item["id"])
            description = str(head.get("description") or "").strip()
            suffix = f": {description}" if description else ""
            lines.append(f"- [{title}]({item['markdown_ref']}){suffix}")
    resources = discovery.get("resources") or []
    if resources:
        lines.extend(["", "## Structured data", ""])
        lines.extend(
            f"- [{item['id']}]({item['ref']}) ({item['type']})"
            for item in resources
        )
    lines.extend(
        [
            "",
            "## Discovery",
            "",
            "- [Site discovery](.well-known/adaos-site.json)",
            "- [Sitemap](sitemap.xml)",
        ]
    )
    return "\n".join(lines) + "\n"


def read_active_revision(database_path: Path) -> dict[str, Any]:
    """Read the immutable identity used for publication compare-and-swap."""

    database_path = database_path.resolve(strict=True)
    database = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = database.execute(
            "SELECT project_id,body FROM site_studio_projects ORDER BY project_id"
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("publication requires one retained Site Studio project")
        project_id, raw = rows[0]
        project = json.loads(raw)
        revision_id = str(project.get("active_revision_id") or "")
        source_digest = str(project.get("active_source_digest") or "")
        site_id = str(project.get("site_id") or "")
        row = database.execute(
            "SELECT source_digest,artifact_digest FROM site_studio_revisions "
            "WHERE project_id=? AND revision_id=?",
            (project_id, revision_id),
        ).fetchone()
    finally:
        database.close()
    if (
        not revision_id
        or not site_id
        or not _DIGEST.fullmatch(source_digest)
        or not row
        or row[0] != source_digest
    ):
        raise ValueError("active Site Studio revision identity is invalid")
    return {
        "project_id": str(project_id),
        "site_id": site_id,
        "revision_id": revision_id,
        "source_digest": source_digest,
        "artifact_digest": str(row[1]),
        "preview_deployment_digest": str(
            project.get("preview_deployment_digest") or ""
        ),
    }


def copy_asset(files_root: Path, output: Path, asset: dict[str, Any]) -> None:
    expected = str(asset.get("digest") or "")
    if not _DIGEST.fullmatch(expected):
        raise ValueError("Site Studio asset digest is invalid")
    public_path = str(asset.get("public_path") or "")
    target = admitted_target(output, public_path)
    candidates = list(
        (files_root / "attachments/objects" / expected[7:9]).glob(
            expected[7:] + ".*"
        )
    )
    if (
        len(candidates) != 1
        or not candidates[0].is_file()
        or candidates[0].is_symlink()
    ):
        raise ValueError("owned Site Studio asset blob is unavailable")
    raw = candidates[0].read_bytes()
    if byte_digest(raw) != expected or len(raw) != asset.get("size_bytes"):
        raise ValueError("owned Site Studio asset blob digest mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def admitted_target(root: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise ValueError("invalid Site Studio publication path")
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Site Studio publication path escapes output")
    return target


def semantic_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return byte_digest(raw)


def byte_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


__all__ = [
    "bind_canonical_base_url",
    "export_publication",
    "read_active_revision",
]
