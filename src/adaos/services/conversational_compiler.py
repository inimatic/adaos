"""Deterministic conversational source -> runtime bundle compiler."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from adaos.services.conversational_artifacts import ConversationalPackage


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _indexed(values: Any) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): copy.deepcopy(dict(item))
        for item in values or []
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }


def compile_runtime_bundle(
    package: ConversationalPackage,
    *,
    previous_package_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one immutable runtime bundle without creating new source truth."""

    manifest = dict(package.manifest)
    locales = [str(item).strip() for item in manifest.get("locales") or [] if str(item).strip()]
    default_locale = str(manifest.get("default_locale") or (locales[0] if locales else "und")).strip()
    catalogs = {
        str(source.get("locale")): copy.deepcopy(dict(source.get("messages") or {}))
        for source in package.locale_sources
    }
    catalog_digest = _digest({"default_locale": default_locale, "catalogs": catalogs})
    package_ref = {
        "kind": "conversational_package",
        "id": str(manifest.get("package_id") or ""),
        "version": str(manifest.get("version") or ""),
        "digest": package.package_digest,
    }
    provider_artifacts = [
        {
            "kind": "deterministic_matcher_router",
            "artifact_ref": {
                "kind": "conversational_runtime",
                "id": f"{package_ref['id']}:matchers",
                "version": package_ref["version"],
                "digest": _digest(package.matchers_source),
            },
            "source_digest": package.package_digest,
            "rollback_ref": copy.deepcopy(dict(previous_package_ref or {})) or None,
        }
    ]
    return {
        "schema": "adaos.conversational.runtime_bundle.v1",
        "package_ref": package_ref,
        "source_digest": package.package_digest,
        "default_locale": default_locale,
        "supported_locales": locales,
        "catalog_digest": catalog_digest,
        "catalogs": catalogs,
        "intents": _indexed(package.input_source.get("intents")),
        "matchers": _indexed(package.matchers_source.get("matchers")),
        "affordances": _indexed(package.affordances_source.get("affordances")),
        "outputs": _indexed(package.output_source.get("outputs")),
        "repair_policies": _indexed(package.repair_source.get("policies")),
        "provider_artifacts": provider_artifacts,
        "rollout": {
            "strategy": "atomic_package_digest",
            "desired_digest": package.package_digest,
            "rollback_package_ref": copy.deepcopy(dict(previous_package_ref or {})) or None,
        },
    }


def locale_chain(bundle: Mapping[str, Any], requested_locale: str | None) -> tuple[str, ...]:
    supported = [str(item) for item in bundle.get("supported_locales") or []]
    default = str(bundle.get("default_locale") or (supported[0] if supported else "und"))
    requested = str(requested_locale or "").strip()
    language = requested.split("-", 1)[0] if requested else ""
    return tuple(dict.fromkeys(item for item in (requested, language, default) if item in supported))


def resolve_message(
    bundle: Mapping[str, Any],
    message_ref: str,
    *,
    locale: str | None,
    fallback: str | None = None,
) -> dict[str, Any]:
    catalogs = dict(bundle.get("catalogs") or {})
    for candidate in locale_chain(bundle, locale):
        catalog = dict(catalogs.get(candidate) or {})
        if message_ref in catalog:
            return {
                "text": str(catalog[message_ref]),
                "locale": candidate,
                "message_ref": message_ref,
                "catalog_digest": bundle.get("catalog_digest"),
                "fallback": candidate != str(locale or ""),
            }
    if fallback is None:
        raise KeyError(f"localized message is unavailable: {message_ref}")
    return {
        "text": str(fallback),
        "locale": str(bundle.get("default_locale") or "und"),
        "message_ref": message_ref,
        "catalog_digest": bundle.get("catalog_digest"),
        "fallback": True,
    }


__all__ = ["compile_runtime_bundle", "locale_chain", "resolve_message"]
