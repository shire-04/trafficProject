from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import RetrievalLogicAgent  # noqa: E402
from contracts import CasualtyEstimate, ExtractedEntities, IncidentInput  # noqa: E402


def main() -> None:
    agent = RetrievalLogicAgent()
    try:
        incident = IncidentInput(
            raw_text="根据国家交通应急预案，公路交通突发事件发生后需要按级别启动预警与响应。"
        )
        entities = ExtractedEntities(
            incident_type="公路交通突发事件",
            casualty_estimate=CasualtyEstimate(unknown=True),
            extract_confidence=0.9,
        )
        context = agent.retrieve(incident, entities)
        print(json.dumps({
            'severity_candidates': context.severity_candidates,
            'severity': context.severity,
            'severity_source': context.severity_source,
            'neo4j_constraints': len(context.neo4j_constraints),
            'chroma_evidence': len(context.chroma_evidence),
        }, ensure_ascii=False, indent=2))
    finally:
        agent.close()


if __name__ == "__main__":
    main()