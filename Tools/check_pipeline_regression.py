from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contracts import IncidentInput  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402


REGRESSION_CASES = [
    {
        "id": "case_01_basic_injury",
        "title": "普通事故伤员场景",
        "raw_text": "城区主干道发生两车碰撞事故，造成1人受伤，需要立即组织现场处置和交通疏导。",
    },
    {
        "id": "case_02_highway_fire",
        "title": "雨天高速起火场景",
        "raw_text": "雨天高速公路发生公路交通突发事件，现场起火并造成2人受伤，需要按预案启动响应并组织处置。",
    },
    {
        "id": "case_03_hazmat_leak",
        "title": "危化品泄漏场景",
        "raw_text": "高速公路上一辆危化品运输车发生泄漏，现场伴随刺鼻气味，需要立即启动应急处置。",
    },
    {
        "id": "case_04_tunnel_multi_vehicle",
        "title": "隧道多车追尾场景",
        "raw_text": "隧道内发生多车追尾事故，部分人员被困，现场通行中断，需要组织救援和交通管制。",
    },
    {
        "id": "case_05_bridge_bus",
        "title": "桥梁客车事故场景",
        "raw_text": "跨江大桥上一辆客车与货车相撞，造成多人受伤，桥面交通严重受阻，需要多部门联动处置。",
    },
]


def summarize_result(case_id: str, title: str, result) -> dict:
    return {
        "case_id": case_id,
        "title": title,
        "incident_type_raw": result.entities.incident_type_raw,
        "incident_type": result.entities.incident_type,
        "matched_event_count": len(result.entities.matched_events),
        "entity_severity": result.entities.severity,
        "context_severity": result.context.severity,
        "constraint_count": len(result.context.neo4j_constraints),
        "evidence_count": len(result.context.chroma_evidence),
        "draft_focus": result.draft.focus,
        "step_count": len(result.draft.steps),
        "resource_count": len(result.draft.required_resources),
        "review_status": result.review.status,
        "review_reason": result.review.reason,
        "retry_count": result.review.retry_count,
        "human_handoff": result.human_handoff,
        "final_strategy_preview": result.final_strategy[:200],
    }



def main() -> None:
    orchestrator = PipelineOrchestrator()
    reports: list[dict] = []
    try:
        for case in REGRESSION_CASES:
            try:
                incident = IncidentInput(raw_text=case["raw_text"])
                result = orchestrator.run_once(incident)
                reports.append(summarize_result(case["id"], case["title"], result))
            except Exception as exc:
                reports.append(
                    {
                        "case_id": case["id"],
                        "title": case["title"],
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
    finally:
        orchestrator.close()

    success_reports = [item for item in reports if "error" not in item]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(REGRESSION_CASES),
        "success_count": len(success_reports),
        "error_count": len(reports) - len(success_reports),
        "non_empty_incident_type_raw_count": sum(1 for item in success_reports if item.get("incident_type_raw")),
        "matched_event_case_count": sum(1 for item in success_reports if item.get("matched_event_count", 0) > 0),
        "non_empty_strategy_case_count": sum(1 for item in success_reports if item.get("step_count", 0) > 0),
        "approved_case_count": sum(1 for item in success_reports if item.get("review_status") == "APPROVED"),
        "handoff_case_count": sum(1 for item in success_reports if item.get("human_handoff")),
    }

    print(json.dumps({"summary": summary, "cases": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
