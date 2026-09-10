from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SNAPSHOT_SCHEMA = "adaos.builder.forensic_snapshot.v1"
DEFAULT_SUBNET_ID = "sn_6acf0c01"
IGNORED_PARTS = {".git", ".pytest_cache", "__pycache__", "node_modules"}
TEXT_SUFFIXES = {".json", ".md", ".py", ".scss", ".ts", ".yaml", ".yml"}
SUBJECT_PATTERNS = {
    "applications_recipe": re.compile(
        r"recipe\.application_manager|application_manager|applications\.(?:list|show|plan|apply)",
        re.IGNORECASE,
    ),
    "marketplace_lifecycle": re.compile(
        r"marketplace|pre-?release|prerelease|auto.?update", re.IGNORECASE
    ),
    "example_products": re.compile(
        r"shopping(?:[-_ ]list)?|recipe(?:[-_ ]book)?|todo", re.IGNORECASE
    ),
    "research_domain": re.compile(r"research|scientific", re.IGNORECASE),
}


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected an object in {path}")
    return dict(value)


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return str(completed.stdout or "").strip() or None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return ()
    if root.is_file():
        return (root,)
    return (
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not IGNORED_PARTS.intersection(path.parts)
    )


def _tree_record(path: Path, repo_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for file_path in _files(path):
        raw = file_path.read_bytes()
        records.append(
            {
                "path": _relative(file_path, path.parent if path.is_file() else path),
                "bytes": len(raw),
                "digest": _sha256(raw),
            }
        )
    return {
        "path": _relative(path, repo_root),
        "exists": path.exists(),
        "file_count": len(records),
        "bytes": sum(record["bytes"] for record in records),
        "tree_digest": _sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
    }


def _scan_source(path: Path, repo_root: Path, owner: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    locations: dict[str, list[dict[str, Any]]] = {name: [] for name in SUBJECT_PATTERNS}
    for file_path in _files(path):
        if file_path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for name, pattern in SUBJECT_PATTERNS.items():
                matches = list(pattern.finditer(line))
                if not matches:
                    continue
                counts[name] += len(matches)
                if len(locations[name]) < 40:
                    locations[name].append(
                        {
                            "path": _relative(file_path, repo_root),
                            "line": line_number,
                            "match_count": len(matches),
                        }
                    )
    return {
        "owner": owner,
        **_tree_record(path, repo_root),
        "subject_matches": {
            name: {"count": counts[name], "sample_locations": locations[name]}
            for name in SUBJECT_PATTERNS
            if counts[name]
        },
    }


def _catalog_inventory(path: Path) -> dict[str, Any]:
    catalog = _read_json(path)
    recipes: list[dict[str, Any]] = []
    classifications = {
        "recipe.application_manager": (
            "subject_domain_pack",
            "move_to_versioned_compatibility_pack",
        ),
        "recipe.kanban_board": ("generic_composition_pattern", "retain_generic"),
        "recipe.resource_board_workbench": (
            "generic_composition_pattern",
            "review_for_example_leakage",
        ),
        "recipe.master_detail": ("generic_composition_pattern", "retain_generic"),
        "recipe.data_entry": ("generic_composition_pattern", "retain_generic"),
        "recipe.dashboard": ("generic_composition_pattern", "retain_generic"),
    }
    for recipe in catalog.get("recipes") or []:
        if not isinstance(recipe, Mapping):
            continue
        recipe_id = str(recipe.get("id") or "")
        classification, disposition = classifications.get(
            recipe_id, ("unclassified", "manual_review")
        )
        recipes.append(
            {
                "id": recipe_id,
                "classification": classification,
                "disposition": disposition,
                "serialized_bytes": len(
                    json.dumps(
                        recipe, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                ),
                "digest": _sha256(
                    json.dumps(recipe, ensure_ascii=False, sort_keys=True).encode(
                        "utf-8"
                    )
                ),
            }
        )
    return {
        "schema": catalog.get("schema"),
        "catalog_version": catalog.get("catalog_version"),
        "layout_count": len(catalog.get("layouts") or []),
        "component_count": len(catalog.get("components") or []),
        "recipes": recipes,
    }


def _capsule_inventory(path: Path) -> dict[str, Any]:
    catalog = _read_json(path)
    rows: list[dict[str, Any]] = []
    for item in catalog.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("id") or "")
        if item_id.startswith("adaos.application."):
            classification = "subject_domain_pack"
            disposition = "move_to_application_pack"
        elif item_id.startswith("adaos.research."):
            classification = "subject_domain_pack"
            disposition = "move_to_research_pack"
        else:
            classification = "platform_policy"
            disposition = "retain_generic_after_boundary_review"
        rows.append(
            {
                "id": item_id,
                "classification": classification,
                "disposition": disposition,
            }
        )
    return {"version": catalog.get("version"), "items": rows}


def _usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    keys = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    if not any(key in value for key in keys):
        return None
    return {key: int(value.get(key) or 0) for key in keys}


def _scenario_telemetry(path: Path, repo_root: Path) -> dict[str, Any]:
    revision_dir = path / "ui_revisions"
    revisions = sorted(revision_dir.glob("*.json")) if revision_dir.is_dir() else []
    usage_rows: list[dict[str, Any]] = []
    providers: Counter[str] = Counter()
    models: Counter[str] = Counter()
    provider_processing_ms: list[int] = []
    for revision_path in revisions:
        revision = _read_json(revision_path)
        llm = revision.get("llm") if isinstance(revision.get("llm"), Mapping) else {}
        telemetry = (
            llm.get("telemetry") if isinstance(llm.get("telemetry"), Mapping) else {}
        )
        usage = _usage(telemetry.get("usage"))
        inference = (
            revision.get("inference")
            if isinstance(revision.get("inference"), Mapping)
            else {}
        )
        provider = str(inference.get("provider") or llm.get("provider") or "").strip()
        model = str(inference.get("model") or llm.get("model") or "").strip()
        if provider:
            providers[provider] += 1
        if model:
            models[model] += 1
        if usage:
            usage_rows.append({"revision": revision_path.stem, **usage})
        provider_meta = (
            telemetry.get("provider")
            if isinstance(telemetry.get("provider"), Mapping)
            else {}
        )
        processing = provider_meta.get("upstream_processing_ms")
        if isinstance(processing, (int, float)):
            provider_processing_ms.append(int(processing))
    llm_dir = path / "llm_jobs"
    result_files = (
        [
            item
            for item in sorted(llm_dir.glob("llm_job_*.json"))
            if item.name.count(".") == 1
        ]
        if llm_dir.is_dir()
        else []
    )
    job_statuses: Counter[str] = Counter()
    repair_attempts = 0
    validation_failures = 0
    for result_path in result_files:
        result = _read_json(result_path)
        job_statuses[str(result.get("status") or "unknown")] += 1
        diagnostic = (
            result.get("diagnostic")
            if isinstance(result.get("diagnostic"), Mapping)
            else {}
        )
        repair_attempts += int(diagnostic.get("repair_attempted") is True)
        response = (
            diagnostic.get("result")
            if isinstance(diagnostic.get("result"), Mapping)
            else {}
        )
        validation = (
            response.get("validation")
            if isinstance(response.get("validation"), Mapping)
            else {}
        )
        validation_failures += int(validation.get("ok") is False)
    current_path = revision_dir / "current.txt"
    current_revision = (
        current_path.read_text(encoding="utf-8").strip()
        if current_path.is_file()
        else None
    )
    return {
        **_tree_record(path, repo_root),
        "current_revision": current_revision,
        "ui_revision_count": len(revisions),
        "llm_job_result_count": len(result_files),
        "llm_job_statuses": dict(sorted(job_statuses.items())),
        "repair_attempt_count": repair_attempts,
        "validation_failure_count": validation_failures,
        "providers": dict(sorted(providers.items())),
        "models": dict(sorted(models.items())),
        "usage_by_revision": usage_rows,
        "provider_processing_ms": provider_processing_ms,
    }


def capture(repo_root: Path, *, subnet_id: str, captured_at: str) -> dict[str, Any]:
    dev_root = repo_root / ".adaos" / "dev" / subnet_id
    client_root = repo_root / "src" / "adaos" / "integrations" / "adaos-client"
    core_sources = {
        "generic_capability_service": (
            repo_root / "src" / "adaos" / "services" / "ui_capabilities.py",
            "Core generic UI capability service",
        ),
        "generic_capability_catalog": (
            repo_root / "src" / "adaos" / "abi" / "ui.capability_catalog.v1.json",
            "Core generic UI capability catalog",
        ),
        "generic_prompt_capsules": (
            repo_root
            / "src"
            / "adaos"
            / "services"
            / "builder"
            / "prompt_rule_capsules.json",
            "Core generic Builder context compiler",
        ),
        "dev_builder_skill": (
            dev_root / "skills" / "builder_skill",
            "DEV Builder conversation and orchestration adapter",
        ),
        "client_semantic_adapter": (
            client_root / "src" / "app" / "runtime" / "semantic-schema-adapter.ts",
            "Client generic semantic adapter",
        ),
        "client_widget_registry": (
            client_root / "src" / "app" / "runtime" / "page-widget-registry.service.ts",
            "Client generic widget registry",
        ),
        "client_action_runtime": (
            client_root / "src" / "app" / "runtime" / "page-action.service.ts",
            "Client generic action runtime",
        ),
        "client_collection_grid": (
            client_root
            / "src"
            / "app"
            / "renderer"
            / "widgets"
            / "collection-grid.widget.component.ts",
            "Client generic collection renderer",
        ),
    }
    scenarios = sorted(
        path for path in (dev_root / "scenarios").glob("applications*") if path.is_dir()
    )
    projects = sorted(
        path for path in (dev_root / "projects").glob("applications*") if path.is_dir()
    )
    skill_manifest = dev_root / "skills" / "builder_skill" / "skill.yaml"
    skill_version = None
    if skill_manifest.is_file():
        version_match = re.search(
            r"^version:\s*['\"]?([^'\"\s]+)",
            skill_manifest.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        skill_version = version_match.group(1) if version_match else None
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": "legacy_recipe_guided_20260910",
        "classification": "legacy_recipe_guided",
        "captured_at": captured_at,
        "repository": {
            "commit": _git(repo_root, "rev-parse", "HEAD"),
            "branch": _git(repo_root, "branch", "--show-current"),
            "client_recorded_commit": _git(
                repo_root, "rev-parse", "HEAD:src/adaos/integrations/adaos-client"
            ),
            "client_worktree_commit": _git(client_root, "rev-parse", "HEAD"),
        },
        "builder": {
            "dev_subnet_id": subnet_id,
            "dev_skill_version": skill_version,
            "adapter": "legacy_dev_chat.v1",
            "prompt_autonomy_claim": False,
        },
        "surfaces": {
            name: _scan_source(path, repo_root, owner)
            for name, (path, owner) in core_sources.items()
        },
        "catalog": _catalog_inventory(core_sources["generic_capability_catalog"][0]),
        "prompt_capsules": _capsule_inventory(
            core_sources["generic_prompt_capsules"][0]
        ),
        "development_projects": [_tree_record(path, repo_root) for path in projects],
        "development_scenarios": [
            _scenario_telemetry(path, repo_root) for path in scenarios
        ],
        "evidence_labels": [
            {
                "ref": "docs/architecture/applications-builder-dogfood-evidence-2026-09-07.md",
                "labels": ["recipe-guided", "renderer-qualified"],
                "prompt_autonomy": False,
            }
        ],
        "known_migrations": [
            {
                "source": "recipe.application_manager",
                "owner": "Core generic capability catalog and evaluator",
                "target": "versioned Applications compatibility domain pack",
            },
            {
                "source": "adaos.application.lifecycle.v1 prompt capsule",
                "owner": "Core generic Builder context compiler",
                "target": "versioned Applications policy pack",
            },
            {
                "source": "shopping-list, recipe-book, and todo examples",
                "owner": "DEV Builder and retained experiment fixtures",
                "target": "explicit development/evaluation fixtures only",
            },
            {
                "source": "Marketplace action and renderer branches",
                "owner": "Client generic runtime and collection renderer",
                "target": "typed product adapter or generic operation contract",
            },
        ],
        "limitations": [
            "The current Client worktree commit differs from the parent-recorded submodule commit.",
            "Legacy LLM telemetry has no uniform end-to-end stage timing; provider processing values are partial.",
            "Usage rows are extracted only from canonical ui_revision.llm.telemetry.usage records and are not billed-cost estimates.",
            "Applications-prefixed experiments remain recipe-guided unless an individual retained run proves otherwise.",
        ],
    }
    snapshot["snapshot_digest"] = _sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture the legacy Builder forensic snapshot."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--subnet-id", default=DEFAULT_SUBNET_ID)
    parser.add_argument(
        "--captured-at",
        default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("e2e/builder/forensics/legacy_recipe_guided_20260910.json"),
    )
    args = parser.parse_args()
    repo_root = args.repo_root.expanduser().resolve()
    output = args.output.expanduser()
    if not output.is_absolute():
        output = repo_root / output
    snapshot = capture(
        repo_root, subnet_id=args.subnet_id, captured_at=args.captured_at
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    print(snapshot["snapshot_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
