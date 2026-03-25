from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contracts import IncidentInput  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402


def main() -> None:
    orchestrator = PipelineOrchestrator()
    try:
        incident = IncidentInput(
            raw_text="雨天高速公路发生公路交通突发事件，现场起火并造成2人受伤，需要按预案启动响应并组织处置。"
        )
        result = orchestrator.run_once(incident)
        print(json.dumps({
            'incident_type_raw': result.entities.incident_type_raw,
            'incident_type': result.entities.incident_type,
            'matched_events': [
                {
                    'normalized_name': item.normalized_name,
                    'node_id': item.node_id,
                    'match_confidence': item.match_confidence,
                }
                for item in result.entities.matched_events
            ],
            'entity_severity': result.entities.severity,
            'entity_severity_reason': result.entities.severity_reason,
            'entity_severity_confidence': result.entities.severity_confidence,
            'severity': result.context.severity,
            'severity_source': result.context.severity_source,
            'draft_focus': result.draft.focus,
            'step_count': len(result.draft.steps),
            'resource_count': len(result.draft.required_resources),
            'review_status': result.review.status,
            'review_reason': result.review.reason,
            'retry_count': result.review.retry_count,
            'missing_actions': result.review.missing_actions,
            'human_handoff': result.human_handoff,
            'final_strategy_preview': result.final_strategy[:200],
        }, ensure_ascii=False, indent=2))
    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()