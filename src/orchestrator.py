import json
import logging
import os
import time

from agents import CommanderAgent, DispatcherAgent, EntityMatcherAgent, EvaluatorAgent, RetrievalLogicAgent
from contracts import IncidentInput, PipelineResult


logger = logging.getLogger(__name__)


def _is_enabled(env_name: str) -> bool:
    return str(os.getenv(env_name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _debug_log(event: str, **payload: object) -> None:
    if not _is_enabled("TRAFFIC_DEBUG"):
        return
    logger.info("%s | %s", event, json.dumps(payload, ensure_ascii=False, default=str))


class PipelineOrchestrator:
    """最小编排器：串联四个新 Agent，先做单次生成与单次审查。"""

    def __init__(
        self,
        dispatcher: DispatcherAgent | None = None,
        matcher: EntityMatcherAgent | None = None,
        retrieval: RetrievalLogicAgent | None = None,
        commander: CommanderAgent | None = None,
        evaluator: EvaluatorAgent | None = None,
    ):
        self.dispatcher = dispatcher or DispatcherAgent()
        self.matcher = matcher or EntityMatcherAgent()
        self.retrieval = retrieval or RetrievalLogicAgent()
        self.commander = commander or CommanderAgent()
        self.evaluator = evaluator or EvaluatorAgent()

    def _finalize_result(self, incident: IncidentInput, entities, context, draft, review) -> PipelineResult:
        final_strategy = "\n".join(draft.steps)
        human_handoff = review.status != "APPROVED"

        return PipelineResult(
            incident=incident,
            entities=entities,
            context=context,
            draft=draft,
            review=review,
            final_strategy=final_strategy,
            human_handoff=human_handoff,
        )

    def run_once(self, incident: IncidentInput) -> PipelineResult:
        started_at = time.perf_counter()
        entities = self.dispatcher.extract(incident)
        _debug_log(
            "pipeline_after_dispatch",
            incident_type_raw=entities.incident_type_raw,
            severity=entities.severity,
            extract_confidence=entities.extract_confidence,
        )
        entities = self.matcher.match(incident, entities)
        _debug_log(
            "pipeline_after_match",
            incident_type=entities.incident_type,
            matched_event_count=len(entities.matched_events),
        )
        context = self.retrieval.retrieve(incident, entities)
        _debug_log(
            "pipeline_after_retrieve",
            constraint_count=len(context.neo4j_constraints),
            evidence_count=len(context.chroma_evidence),
            severity=context.severity,
        )
        draft = self.commander.generate(incident, entities, context)
        _debug_log(
            "pipeline_after_generate",
            focus=draft.focus,
            step_count=len(draft.steps),
        )
        review = self.evaluator.review(incident, entities, context, draft, retry_count=0)
        _debug_log(
            "pipeline_after_review",
            status=review.status,
            retry_count=review.retry_count,
            failure_type=review.failure_type,
        )

        if review.status != "APPROVED":
            revised_draft = self.commander.revise(incident, entities, context, draft, review)
            revised_review = self.evaluator.review(incident, entities, context, revised_draft, retry_count=1)
            _debug_log(
                "pipeline_after_retry",
                revised_step_count=len(revised_draft.steps),
                revised_status=revised_review.status,
                elapsed_seconds=round(time.perf_counter() - started_at, 3),
            )
            return self._finalize_result(incident, entities, context, revised_draft, revised_review)

        _debug_log(
            "pipeline_completed",
            status=review.status,
            elapsed_seconds=round(time.perf_counter() - started_at, 3),
        )
        return self._finalize_result(incident, entities, context, draft, review)

    def close(self) -> None:
        self.retrieval.close()
