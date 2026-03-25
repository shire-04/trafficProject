from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import DispatcherAgent, EntityMatcherAgent  # noqa: E402
from contracts import IncidentInput  # noqa: E402


def fake_text_analyzer(_: str) -> dict:
    return {
        "incident_type": "危化品车泄漏",
        "weather": "雨天",
        "hazards": ["泄漏"],
        "vehicles": ["危化品运输车"],
        "location_features": ["高速"],
        "casualties": {"deaths": 0, "injuries": 1, "missing": 0},
        "extract_confidence": 0.88,
    }


def fake_severity_analyzer(_: str) -> dict:
    return {
        "severity": "一般",
        "severity_reason": "当前仅体现局部事故与少量伤员。",
        "severity_confidence": 0.76,
    }


def fake_matcher(_: str) -> dict:
    return {
        "matches": [
            {
                "surface_form": "危化品车泄漏",
                "entity_type": "突发事件",
                "normalized_name": "危险化学品运输泄漏事件",
                "node_id": "EVT_HAZMAT_LEAK",
                "match_confidence": 0.93,
                "match_reason": "语义与标准事件节点高度一致。",
            }
        ]
    }


def main() -> None:
    dispatcher = DispatcherAgent(
        text_analyzer=fake_text_analyzer,
        severity_analyzer=fake_severity_analyzer,
    )
    matcher = EntityMatcherAgent(matcher=fake_matcher)

    incident = IncidentInput(raw_text="雨天高速上一辆危化品车发生泄漏并有1人受伤。")
    entities = dispatcher.extract(incident)
    matched_entities = matcher.match(incident, entities)

    print(json.dumps({
        "incident_type_raw": matched_entities.incident_type_raw,
        "incident_type_before_match": entities.incident_type,
        "incident_type_after_match": matched_entities.incident_type,
        "matched_events": [
            {
                "normalized_name": item.normalized_name,
                "node_id": item.node_id,
                "match_confidence": item.match_confidence,
                "match_reason": item.match_reason,
            }
            for item in matched_entities.matched_events
        ],
        "severity": matched_entities.severity,
        "severity_reason": matched_entities.severity_reason,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()