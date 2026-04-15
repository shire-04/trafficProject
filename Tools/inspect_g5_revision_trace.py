from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contracts import IncidentInput, ReviewResult, StrategyDraft  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402


def _draft_to_dict(draft: StrategyDraft) -> dict[str, Any]:
    return {
        "focus": draft.focus,
        "steps": list(draft.steps),
        "required_resources": list(draft.required_resources),
        "legal_references": list(draft.legal_references),
    }


def _review_to_dict(review: ReviewResult) -> dict[str, Any]:
    return {
        "status": review.status,
        "reason": review.reason,
        "retry_count": review.retry_count,
        "failure_type": review.failure_type,
        "overall_score": review.overall_score,
        "score_delta": review.score_delta,
        "score_threshold": review.score_threshold,
        "executability_score": review.executability_score,
        "safety_score": review.safety_score,
        "compliance_score": review.compliance_score,
        "missing_actions": list(review.missing_actions),
        "violated_constraints": list(review.violated_constraints),
        "risk_notes": list(review.risk_notes),
        "ineffective_revision_count": review.ineffective_revision_count,
    }


def _read_image_bytes(image_path: str | None) -> bytes | None:
    if not image_path:
        return None
    path = Path(image_path)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {path}")
    return path.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="诊断 G5 审查修订是否真实生效")
    parser.add_argument("--text", required=True, help="事故文本")
    parser.add_argument("--image", default="", help="图片路径，可相对项目根")
    args = parser.parse_args()

    os.environ.setdefault("TRAFFIC_AGENT_MODE", "multi_with_review")
    os.environ.setdefault("TRAFFIC_DEBUG", "1")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    trace: dict[str, Any] = {
        "generate": [],
        "revise": [],
        "review_inputs": [],
        "review_outputs": [],
    }

    orchestrator = PipelineOrchestrator()
    try:
        original_generate = orchestrator.commander.generate
        original_revise = orchestrator.commander.revise
        original_review = orchestrator.evaluator.review

        def wrapped_generate(*g_args: Any, **g_kwargs: Any) -> StrategyDraft:
            draft = original_generate(*g_args, **g_kwargs)
            trace["generate"].append(_draft_to_dict(draft))
            return draft

        def wrapped_revise(*r_args: Any, **r_kwargs: Any) -> StrategyDraft:
            # revise(incident, entities, context, current_draft, current_review)
            if len(r_args) >= 4 and isinstance(r_args[3], StrategyDraft):
                trace.setdefault("revise_input", []).append(_draft_to_dict(r_args[3]))
            revised = original_revise(*r_args, **r_kwargs)
            trace["revise"].append(_draft_to_dict(revised))
            return revised

        def wrapped_review(*rv_args: Any, **rv_kwargs: Any) -> ReviewResult:
            # review(incident, entities, context, draft, retry_count=...)
            if len(rv_args) >= 4 and isinstance(rv_args[3], StrategyDraft):
                trace["review_inputs"].append(_draft_to_dict(rv_args[3]))
            review = original_review(*rv_args, **rv_kwargs)
            trace["review_outputs"].append(_review_to_dict(review))
            return review

        orchestrator.commander.generate = wrapped_generate  # type: ignore[assignment]
        orchestrator.commander.revise = wrapped_revise  # type: ignore[assignment]
        orchestrator.evaluator.review = wrapped_review  # type: ignore[assignment]

        incident = IncidentInput(raw_text=args.text, image_bytes=_read_image_bytes(args.image or None))
        result = orchestrator.run_once(incident)

        initial_draft = trace["generate"][0] if trace["generate"] else None
        final_draft = _draft_to_dict(result.draft)
        final_equals_initial = initial_draft == final_draft if initial_draft is not None else False

        payload = {
            "input": {
                "text": args.text,
                "image": args.image,
                "agent_mode": os.getenv("TRAFFIC_AGENT_MODE", ""),
            },
            "summary": {
                "review_call_count": len(trace["review_outputs"]),
                "revise_call_count": len(trace["revise"]),
                "final_equals_initial": final_equals_initial,
                "final_review": _review_to_dict(result.review),
                "human_handoff": result.human_handoff,
            },
            "initial_draft": initial_draft,
            "final_draft": final_draft,
            "trace": trace,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()
