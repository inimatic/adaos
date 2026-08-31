"""Run a governed conceptual Research Fabric candidate from a case package."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.apps.bootstrap import init_ctx
from adaos.sdk.research import (
    ResearchLlmCallError,
    author_synthesis_revision,
    build_llm_failure_receipt,
    digest_payload,
    review_synthesis_revision,
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _material_content(source: Mapping[str, Any]) -> str:
    return json.dumps(dict(source), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_request(package_dir: Path, *, run_id: str, model: str, now: str) -> dict[str, Any]:
    brief_path = package_dir / "author-brief.md"
    scope_path = package_dir / "scope.json"
    sources_path = package_dir / "sources.json"
    workbench_path = package_dir / "workbench.json"
    brief = brief_path.read_text(encoding="utf-8")
    scope = _json(scope_path)
    sources = _json(sources_path)
    workbench = _json(workbench_path) if workbench_path.is_file() else {}
    if not isinstance(scope, dict) or not isinstance(sources, list):
        raise ValueError("case package requires object scope.json and array sources.json")
    if not isinstance(workbench, dict):
        raise ValueError("workbench.json must be an object")
    direction_id = str(workbench.get("direction_id") or "evolnomics")
    task = workbench.get("task") if isinstance(workbench.get("task"), Mapping) else {}
    task_id = str(task.get("task_id") or f"{direction_id}.phase_a")
    synthesis_id = str(workbench.get("synthesis_id") or task_id)

    materials: list[dict[str, Any]] = []
    input_refs: list[dict[str, Any]] = []
    brief_ref = f"artifact:{direction_id}.author-brief"
    brief_digest = digest_payload({"content": brief})
    materials.append(
        {
            "ref": brief_ref,
            "kind": "genesis_brief",
            "digest": brief_digest,
            "title": "Evolnomics Phase A Author Brief",
            "content": brief,
        }
    )
    input_refs.append(
        {
            "ref": brief_ref,
            "kind": "genesis_brief",
            "authority": "authoritative_input",
            "digest": brief_digest,
            "accessed_at": now,
            "fragments": [],
        }
    )

    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("every source record must be an object")
        source_id = str(source["source_id"])
        source_ref = f"source:{source_id}"
        content = _material_content(source)
        digest = digest_payload({"content": content})
        metadata = {
            key: source[key]
            for key in ("title", "authors", "year", "url", "doi")
            if source.get(key) not in (None, "")
        }
        literature_record = {
            "source_id": source_id,
            "metadata": metadata,
            "actual_reading_status": source["actual_reading_status"],
            "source_ref": source_ref,
            "digest": digest,
        }
        materials.append(
            {
                "ref": source_ref,
                "kind": "external_literature",
                "digest": digest,
                "title": source["title"],
                "actual_reading_status": source["actual_reading_status"],
                "content": content,
                "literature_record": literature_record,
            }
        )
        input_refs.append(
            {
                "ref": source_ref,
                "kind": "external_literature",
                "authority": "admitted_literature",
                "digest": digest,
                "accessed_at": now,
                "fragments": [],
            }
        )

    snapshot = {
        "source_bundle_digest": digest_payload(materials),
        "input_refs": input_refs,
        "allowed_paths": [str(package_dir.resolve())],
        "allowed_external_sources": [str(item["url"]) for item in sources],
        "denied_material_classes": [
            "hidden comparator drafts",
            "unlisted web or model knowledge",
            "Builder implementation context",
            "Phase B artifacts",
            "empirical results not present in admitted sources",
        ],
        "coverage_note": (
            "Bounded scoping corpus for conceptual positioning and novelty-risk assessment; "
            "not exhaustive and not proof of novelty."
        ),
    }
    snapshot["snapshot_digest"] = digest_payload(snapshot)
    return {
        "run_id": run_id,
        "synthesis_id": synthesis_id,
        "revision": 1,
        "direction_ref": f"research-direction:{direction_id}",
        "task_ref": f"research-task:{task_id}",
        "research_question": (
            "What defensible conceptual framework connects human-agent-artifact coevolution, "
            "governed artifact lineage, contribution provenance, downstream utility, and resources "
            "for subsequent AI-native software evolution without prematurely selecting an economic mechanism?"
        ),
        "author_intent": (
            "Use Evolnomics to test Research Fabric conceptual formalization. Calibrate novelty "
            "against close artifact-inclusive and coevolutionary prior work; produce claims, "
            "counterarguments, threats, boundary conditions, and a falsifiable research agenda."
        ),
        "source_snapshot": snapshot,
        "literature_scope": scope,
        "materials": materials,
        "model": model,
        "request_id": f"research.{run_id}.synthesis",
        "created_at": now,
    }


def _add_generated_material(
    request: dict[str, Any],
    *,
    path: Path,
    ref: str,
    title: str,
) -> None:
    content = path.read_text(encoding="utf-8")
    digest = digest_payload({"content": content})
    request["materials"].append(
        {
            "ref": ref,
            "kind": "generated_analysis",
            "digest": digest,
            "title": title,
            "content": content,
        }
    )
    snapshot = request["source_snapshot"]
    snapshot["input_refs"].append(
        {
            "ref": ref,
            "kind": "generated_analysis",
            "authority": "generated_non_authoritative",
            "digest": digest,
            "accessed_at": request["created_at"],
            "fragments": [],
        }
    )
    snapshot["allowed_paths"].append(str(path.resolve()))
    snapshot["source_bundle_digest"] = digest_payload(request["materials"])
    snapshot_without_digest = dict(snapshot)
    snapshot_without_digest.pop("snapshot_digest", None)
    snapshot["snapshot_digest"] = digest_payload(snapshot_without_digest)


def _sum_usage(*records: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
    complete = bool(records) and all(
        record.get("accuracy") == "provider_reported"
        and all(record.get(field) is not None for field in fields)
        for record in records
    )
    return {
        "accounting_scope": "researcher_llm",
        **{
            field: sum(int(record[field]) for record in records) if complete else None
            for field in fields
        },
        "accuracy": "provider_reported" if complete else "unavailable",
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _base_manifest(run_id: str, now: str) -> dict[str, Any]:
    return {
        "schema": "adaos.research.case_run_manifest.v1",
        "run_id": run_id,
        "builder_codex": {
            "required": False,
            "invoked": False,
            "total_tokens": 0,
            "accounting_scope": "builder_codex",
        },
        "current_codex_session_accounted": False,
        "accepted_synthesis_created": False,
        "draft_candidate_created": False,
        "gate_a1_created": False,
        "phase_b_authorized": False,
        "research_release_created": False,
        "created_at": now,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--recover-authoring-job-id")
    parser.add_argument("--recover-source-snapshot", type=Path)
    parser.add_argument("--authoring-request-id")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--prior-synthesis", type=Path)
    parser.add_argument("--prior-review", type=Path)
    args = parser.parse_args()

    package_dir = args.package_dir.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    request = _build_request(package_dir, run_id=args.run_id, model=args.model, now=now)
    request["revision"] = args.revision
    if args.prior_synthesis:
        _add_generated_material(
            request,
            path=args.prior_synthesis.resolve(),
            ref="artifact:prior-synthesis",
            title="Prior ResearchSynthesisRevision candidate",
        )
    if args.prior_review:
        _add_generated_material(
            request,
            path=args.prior_review.resolve(),
            ref="artifact:prior-synthesis-review",
            title="Adversarial review of the prior synthesis",
        )
    if args.prior_synthesis or args.prior_review:
        request["author_intent"] += (
            " Produce a revised full candidate that addresses the admitted prior review while "
            "treating both prior artifacts as non-authoritative generated analysis."
        )
    if args.recover_source_snapshot:
        request["source_snapshot"] = _json(args.recover_source_snapshot.resolve())
        accessed = request["source_snapshot"].get("input_refs") or []
        if accessed and accessed[0].get("accessed_at"):
            request["created_at"] = str(accessed[0]["accessed_at"])
    if args.authoring_request_id:
        request["request_id"] = args.authoring_request_id
    _write_json(output_dir / "source-snapshot.json", request["source_snapshot"])

    init_ctx()
    try:
        if args.recover_authoring_job_id:
            from adaos.sdk.llm.llm_client import get_response_job

            recovered_response = get_response_job(args.recover_authoring_job_id)
            authored = author_synthesis_revision(
                request,
                llm_call=lambda *_args, **_kwargs: recovered_response,
            )
        else:
            authored = author_synthesis_revision(request)
    except Exception as exc:
        failure = build_llm_failure_receipt(
            exc,
            run_id=args.run_id,
            task_ref=request["task_ref"],
            model=args.model,
            request_id=request["request_id"],
            operation=(
                exc.operation
                if isinstance(exc, ResearchLlmCallError)
                else "synthesis_authoring"
            ),
            started_at=now,
        )
        manifest = {
            **_base_manifest(args.run_id, now),
            "status": "failed_synthesis_authoring",
            "source_snapshot_digest": request["source_snapshot"]["snapshot_digest"],
            "failure_digest": failure["digest"],
            "researcher_llm_usage": failure["usage"],
        }
        _write_json(output_dir / "llm-failure.json", failure)
        _write_json(output_dir / "run-manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 2

    _write_json(output_dir / "visibility-receipt.json", authored["visibility_receipt"])
    _write_json(output_dir / "isolation-receipt.json", authored["isolation_receipt"])
    _write_json(output_dir / "authoring-run.json", authored["authoring_run"])
    _write_json(
        output_dir / f"research-synthesis-revision-{args.revision}.json",
        authored["synthesis"],
    )

    review_started_at = datetime.now(timezone.utc).isoformat()
    try:
        review = review_synthesis_revision(
            authored["synthesis"],
            review_id=f"review.{args.run_id}",
            model=args.model,
            request_id=f"research.{args.run_id}.review",
            created_at=review_started_at,
        )
    except Exception as exc:
        review_request_id = f"research.{args.run_id}.review"
        failure = build_llm_failure_receipt(
            exc,
            run_id=args.run_id,
            task_ref=request["task_ref"],
            model=args.model,
            request_id=review_request_id,
            operation=(
                exc.operation
                if isinstance(exc, ResearchLlmCallError)
                else "synthesis_review"
            ),
            started_at=review_started_at,
        )
        manifest = {
            **_base_manifest(args.run_id, now),
            "status": "failed_synthesis_review",
            "source_snapshot_digest": request["source_snapshot"]["snapshot_digest"],
            "synthesis_digest": authored["synthesis"]["digest"],
            "failure_digest": failure["digest"],
            "researcher_llm_usage": _sum_usage(
                authored["authoring_run"]["usage"], failure["usage"]
            ),
        }
        _write_json(output_dir / "llm-failure.json", failure)
        _write_json(output_dir / "run-manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 3

    aggregate_usage = _sum_usage(authored["authoring_run"]["usage"], review["usage"])
    manifest = {
        **_base_manifest(args.run_id, now),
        "status": "awaiting_human_synthesis_decision",
        "source_snapshot_digest": request["source_snapshot"]["snapshot_digest"],
        "synthesis_digest": authored["synthesis"]["digest"],
        "review_digest": review["digest"],
        "review_verdict": review["verdict"],
        "researcher_llm_usage": aggregate_usage,
        **(
            {"recovered_authoring_job_id": args.recover_authoring_job_id}
            if args.recover_authoring_job_id
            else {}
        ),
    }
    _write_json(output_dir / f"synthesis-review-{args.revision}.json", review)
    _write_json(output_dir / "run-manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
