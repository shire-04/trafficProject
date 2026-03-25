from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import CommanderAgent, DispatcherAgent, EvaluatorAgent, RetrievalLogicAgent  # noqa: E402
from contracts import IncidentInput  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402


def fake_image_analyzer(_: bytes) -> dict:
    return {
        "incident_type": "",
        "weather": "雨天",
        "hazards": ["起火", "伤员"],
        "vehicles": ["货车"],
        "location_features": ["高速"],
        "casualties": {"deaths": 0, "injuries": 1, "missing": 0},
        "evidence": ["图片显示高速路段一辆货车前部起火，旁侧疑似有人受伤倒地。"],
        "confidence": 0.88,
    }


def main() -> None:
    dispatcher = DispatcherAgent(image_analyzer=fake_image_analyzer)
    orchestrator = PipelineOrchestrator(
        dispatcher=dispatcher,
        retrieval=RetrievalLogicAgent(),
        commander=CommanderAgent(),
        evaluator=EvaluatorAgent(),
    )
    try:
        incident = IncidentInput(
            raw_text="高速公路发生交通事故，现场具体情况待进一步确认。",
            image_bytes=b"fake-image-content",
        )
        result = orchestrator.run_once(incident)
        print(json.dumps({
            "incident_type": result.entities.incident_type,
            "weather": result.entities.weather,
            "hazards": result.entities.hazards,
            "vehicles": result.entities.vehicles,
            "location_features": result.entities.location_features,
            "casualty_estimate": {
                "deaths": result.entities.casualty_estimate.deaths,
                "injuries": result.entities.casualty_estimate.injuries,
                "missing": result.entities.casualty_estimate.missing,
                "unknown": result.entities.casualty_estimate.unknown,
            },
            "evidence_from_image": result.entities.evidence_from_image,
            "severity": result.context.severity,
            "severity_source": result.context.severity_source,
            "review_status": result.review.status,
            "human_handoff": result.human_handoff,
        }, ensure_ascii=False, indent=2))
    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()