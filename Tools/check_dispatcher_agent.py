from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import DispatcherAgent  # noqa: E402
from contracts import IncidentInput  # noqa: E402


def fake_image_analyzer(_: bytes) -> dict:
    return {
        'incident_type': '',
        'weather': '雨天',
        'hazards': ['起火'],
        'vehicles': ['货车'],
        'location_features': ['高速'],
        'casualties': {'deaths': 0, 'injuries': 1, 'missing': 0},
        'evidence': ['图片显示一辆货车车头受损并伴有明火。'],
        'confidence': 0.82,
    }


def fake_severity_analyzer(_: str) -> dict:
    return {
        'severity': '一般',
        'severity_reason': '存在道路事故和伤员，但未体现跨省影响或长时间中断。',
        'severity_confidence': 0.86,
    }


def main() -> None:
    agent = DispatcherAgent(severity_analyzer=fake_severity_analyzer)
    incident = IncidentInput(
        raw_text="雨天高速公路上一辆危化品运输车与货车追尾后发生泄漏并起火，造成2人受伤。"
    )
    entities = agent.extract(incident)
    print(json.dumps({
        'incident_type': entities.incident_type,
        'severity': entities.severity,
        'severity_reason': entities.severity_reason,
        'severity_confidence': entities.severity_confidence,
        'weather': entities.weather,
        'hazards': entities.hazards,
        'vehicles': entities.vehicles,
        'location_features': entities.location_features,
        'casualty_estimate': {
            'deaths': entities.casualty_estimate.deaths,
            'injuries': entities.casualty_estimate.injuries,
            'missing': entities.casualty_estimate.missing,
            'unknown': entities.casualty_estimate.unknown,
        },
        'extract_confidence': entities.extract_confidence,
    }, ensure_ascii=False, indent=2))

    image_agent = DispatcherAgent(
        image_analyzer=fake_image_analyzer,
        severity_analyzer=fake_severity_analyzer,
    )
    image_incident = IncidentInput(
        raw_text="高速公路发生交通事故，现场情况待确认。",
        image_bytes=b"fake-image-content",
    )
    image_entities = image_agent.extract(image_incident)
    print(json.dumps({
        'case': 'image_assisted',
        'incident_type': image_entities.incident_type,
        'severity': image_entities.severity,
        'severity_reason': image_entities.severity_reason,
        'severity_confidence': image_entities.severity_confidence,
        'weather': image_entities.weather,
        'hazards': image_entities.hazards,
        'vehicles': image_entities.vehicles,
        'location_features': image_entities.location_features,
        'casualty_estimate': {
            'deaths': image_entities.casualty_estimate.deaths,
            'injuries': image_entities.casualty_estimate.injuries,
            'missing': image_entities.casualty_estimate.missing,
            'unknown': image_entities.casualty_estimate.unknown,
        },
        'evidence_from_image': image_entities.evidence_from_image,
        'extract_confidence': image_entities.extract_confidence,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()