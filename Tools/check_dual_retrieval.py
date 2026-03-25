from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contracts import CasualtyEstimate, ExtractedEntities, IncidentInput  # noqa: E402
from retrieval_logic import DualRetrievalService  # noqa: E402


def main() -> None:
    service = DualRetrievalService(
        chroma_db_path=str(PROJECT_ROOT / "chroma_data"),
    )
    try:
        incident = IncidentInput(
            raw_text="国家交通应急预案指出，公路交通突发事件发生后应按级别启动预警和应急响应。"
        )
        entities = ExtractedEntities(
            incident_type="公路交通突发事件",
            casualty_estimate=CasualtyEstimate(unknown=True),
            extract_confidence=0.8,
        )
        context = service.retrieve(incident, entities)
        payload = {
            'severity_candidates': context.severity_candidates,
            'severity': context.severity,
            'severity_source': context.severity_source,
            'constraint_count': len(context.neo4j_constraints),
            'evidence_count': len(context.chroma_evidence),
            'constraint_preview': [item.rule for item in context.neo4j_constraints[:5]],
            'evidence_preview': [item.content[:80] for item in context.chroma_evidence[:2]],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        casualty_incident = IncidentInput(
            raw_text="雨天高速公路发生公路交通突发事件，现场起火并造成2人受伤，需要按预案启动响应并组织处置。"
        )
        casualty_entities = ExtractedEntities(
            incident_type="公路交通突发事件",
            weather="雨天",
            hazards=["起火", "伤员"],
            casualty_estimate=CasualtyEstimate(injuries=2, unknown=False),
            extract_confidence=0.9,
        )
        casualty_context = service.retrieve(casualty_incident, casualty_entities)
        print(json.dumps({
            'case': 'casualty_inference',
            'severity_candidates': casualty_context.severity_candidates,
            'severity': casualty_context.severity,
            'severity_source': casualty_context.severity_source,
        }, ensure_ascii=False, indent=2))
    finally:
        service.close()


if __name__ == "__main__":
    main()