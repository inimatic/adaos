from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .development_reports import DevelopmentReportService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tokens(*values: str) -> frozenset[str]:
    text = unicodedata.normalize("NFKC", " ".join(values)).casefold()
    return frozenset(re.findall(r"[^\W_]{2,64}", text, flags=re.UNICODE)[:512])


class DevelopmentReportTriageService:
    """Publisher-local, explainable triage aids without decision authority."""

    def __init__(
        self,
        report_service: DevelopmentReportService,
        *,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.report_service = report_service
        self.now = now

    @staticmethod
    def privacy_policy() -> dict[str, Any]:
        return {
            "schema": "adaos.application.development_report_triage_policy.v1",
            "scope": "publisher_local_same_application",
            "history_window_days": 365,
            "inputs": ["normalized_summary", "normalized_details", "adjudication_status"],
            "excluded_inputs": ["raw_payload", "secrets", "cross_application_activity"],
            "decision_authority": "advisory_only",
            "automatic_actions": [],
            "reporter_assessment": "factual_history_without_score_or_rank",
            "explanations": "candidate_ids_similarity_and_shared_terms",
            "appeal": "encrypted_reporter_to_publisher_with_visible_resolution",
            "retention": "follows_local_development_report_retention_policy",
        }

    def duplicate_candidates(
        self,
        report_id: str,
        *,
        threshold: float = 0.65,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not 0.5 <= float(threshold) <= 1.0:
            raise ValueError("duplicate threshold must be between 0.5 and 1.0")
        bounded_limit = max(1, min(int(limit), 25))
        state = self.report_service.store.read()
        target = state["intakes"].get(report_id)
        if target is None:
            raise FileNotFoundError(f"publisher intake not found: {report_id}")
        target_tokens = _tokens(
            str(target["normalized_summary"]), str(target["normalized_details"])
        )
        candidates: list[dict[str, Any]] = []
        scanned = 0
        for candidate_id, candidate in state["intakes"].items():
            if scanned >= 2000:
                break
            scanned += 1
            if candidate_id == report_id or candidate.get("application_id") != target.get("application_id"):
                continue
            candidate_tokens = _tokens(
                str(candidate["normalized_summary"]), str(candidate["normalized_details"])
            )
            shared = target_tokens & candidate_tokens
            union = target_tokens | candidate_tokens
            similarity = len(shared) / len(union) if union else 0.0
            if len(shared) < 2 or similarity < float(threshold):
                continue
            candidates.append({
                "report_id": candidate_id,
                "similarity": round(similarity, 4),
                "shared_terms": sorted(shared)[:12],
                "status": candidate.get("status"),
                "explanation": "normalized redacted token overlap",
            })
        candidates.sort(key=lambda item: (-float(item["similarity"]), item["report_id"]))
        return {
            "report_id": report_id,
            "application_id": target["application_id"],
            "threshold": float(threshold),
            "candidates": candidates[:bounded_limit],
            "scanned": scanned,
            "truncated": len(state["intakes"]) > scanned,
            "authority": "advisory_only",
        }

    def reporter_history(self, report_id: str) -> dict[str, Any]:
        state = self.report_service.store.read()
        target = state["intakes"].get(report_id)
        if target is None:
            raise FileNotFoundError(f"publisher intake not found: {report_id}")
        cutoff = self.now() - timedelta(days=365)
        selected = []
        scanned = 0
        for candidate_id, candidate in state["intakes"].items():
            if scanned >= 5000:
                break
            scanned += 1
            raw = state["raw_intake"].get(candidate_id) or {}
            if (
                candidate.get("application_id") != target.get("application_id")
                or candidate.get("reporter_subnet_ref") != target.get("reporter_subnet_ref")
            ):
                continue
            created_at = datetime.fromisoformat(
                str(raw.get("created_at") or candidate["received_at"]).replace("Z", "+00:00")
            )
            if created_at >= cutoff:
                selected.append(candidate)
        selected = selected[:500]
        counts = Counter(str(item.get("status") or "unknown") for item in selected)
        appeal_counts: Counter[str] = Counter()
        for appeal in list(state["appeals"].values())[:500]:
            if (
                appeal.get("application_id") != target.get("application_id")
                or appeal.get("reporter_subnet_ref") != target.get("reporter_subnet_ref")
            ):
                continue
            created_at = datetime.fromisoformat(
                str(appeal["created_at"]).replace("Z", "+00:00")
            )
            if created_at >= cutoff:
                appeal_counts[str(appeal.get("resolution") or appeal.get("status") or "unknown")] += 1
        return {
            "report_id": report_id,
            "application_id": target["application_id"],
            "reporter_subnet_ref": target["reporter_subnet_ref"],
            "window_days": 365,
            "scanned": scanned,
            "truncated": len(state["intakes"]) > scanned,
            "report_count": len(selected),
            "outcome_counts": dict(sorted(counts.items())),
            "appeal_outcome_counts": dict(sorted(appeal_counts.items())),
            "score": None,
            "rank": None,
            "authority": "advisory_only",
            "limitations": [
                "publisher-local observations only",
                "same Application only",
                "small samples are not predictive",
            ],
        }
