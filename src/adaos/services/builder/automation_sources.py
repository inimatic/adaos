"""Seal the accepted Core Prototype into Automation's isolated source snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from adaos.services.resources.prototype import prototype_webui_digest
from adaos.services.skill_factory_sources import _copy_source_tree, capture_source_snapshot


def capture_automation_sources(*, workflow, object_type, object_id, acceptance,
                               state_dir, artifacts, attachments, created_at):
    artifacts = list(artifacts)
    publication = (workflow._committed_prototype_publication(object_type, object_id)
                   if object_type == "scenario" and acceptance
                   and acceptance.get("decision") == "accepted" else None)
    if publication is None:
        return capture_source_snapshot(state_dir=state_dir, artifacts=artifacts,
                                       attachments=attachments, created_at=created_at)
    compiled = publication["compiled"]
    webui = compiled["webui"]
    if (publication["seed"]["revision"] != acceptance.get("revision")
            or prototype_webui_digest(webui) != acceptance.get("webui_digest")):
        raise ValueError("Core Prototype source differs from the accepted revision")
    matches = [(index, path) for index, (kind, identity, path) in enumerate(artifacts)
               if kind == object_type and identity == object_id]
    if len(matches) != 1:
        raise ValueError("Core Prototype requires exactly one owning source artifact")
    staging_root = Path(state_dir) / "builder" / "source_staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=staging_root, prefix="accepted-prototype-") as temporary:
        index, source = matches[0]
        target = Path(temporary) / "scenario"
        _copy_source_tree(source, target, excluded_dirs=frozenset({"artifacts"}))

        def write(relative, value):
            destination = (target / relative).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise ValueError("Core Prototype resource escapes its source artifact")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

        write("webui.json", webui)
        write("semantic.webui.json", publication["document"])
        dictionaries = compiled.get("locale_dictionaries") or {}
        declared_locales = set()
        for definition in webui.get("ui", {}).get("application", {}).get("resources", {}).values():
            if definition.get("role") != "i18n":
                continue
            locale = definition.get("locale")
            if locale not in dictionaries:
                raise ValueError("Core Prototype source is missing a declared locale")
            write(definition["path"], dictionaries[locale])
            declared_locales.add(locale)
        for locale, dictionary in dictionaries.items():
            if locale not in declared_locales:
                write(f"assets/i18n/{locale}.json", dictionary)
        artifacts[index] = (object_type, object_id, target)
        return capture_source_snapshot(state_dir=state_dir, artifacts=artifacts,
                                       attachments=attachments, created_at=created_at)
