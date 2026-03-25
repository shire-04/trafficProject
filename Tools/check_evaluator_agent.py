from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import CommanderAgent, DispatcherAgent, EvaluatorAgent, RetrievalLogicAgent  # noqa: E402
from contracts import IncidentInput  # noqa: E402


def main() -> None:
    dispatcher = DispatcherAgent()
    retrieval = RetrievalLogicAgent()
    commander = CommanderAgent()
    evaluator = EvaluatorAgent()
    try:
        incident = IncidentInput(
            raw_text="雨天高速公路发生公路交通突发事件，现场有起火并造成2人受伤，需要按预案启动响应并组织处置。"
        )
        entities = dispatcher.extract(incident)
        context = retrieval.retrieve(incident, entities)
        draft = commander.generate(incident, entities, context)
        review = evaluator.review(incident, entities, context, draft, retry_count=0)
        print(json.dumps({
            'draft_focus': draft.focus,
            'step_count': len(draft.steps),
            'resource_count': len(draft.required_resources),
            'review_status': review.status,
            'review_reason': review.reason,
            'missing_actions': review.missing_actions,
            'risk_notes': review.risk_notes[:5],
        }, ensure_ascii=False, indent=2))
    finally:
        retrieval.close()


if __name__ == "__main__":
    main()