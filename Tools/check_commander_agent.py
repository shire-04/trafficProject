from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import CommanderAgent, DispatcherAgent, RetrievalLogicAgent  # noqa: E402
from contracts import IncidentInput  # noqa: E402


def main() -> None:
    dispatcher = DispatcherAgent()
    retrieval = RetrievalLogicAgent()
    commander = CommanderAgent()
    try:
        incident = IncidentInput(
            raw_text="公路交通突发事件发生后，应按照国家交通应急预案启动预警和应急响应，并组织相关部门开展应急处置。"
        )
        entities = dispatcher.extract(incident)
        context = retrieval.retrieve(incident, entities)
        draft = commander.generate(incident, entities, context)
        print(json.dumps({
            'focus': draft.focus,
            'step_count': len(draft.steps),
            'steps': draft.steps,
            'resource_count': len(draft.required_resources),
            'legal_references': draft.legal_references,
        }, ensure_ascii=False, indent=2))
    finally:
        retrieval.close()


if __name__ == "__main__":
    main()