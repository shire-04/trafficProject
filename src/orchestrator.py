import json
import logging
import os
import re
import time

from agents import CommanderAgent, DispatcherAgent, EntityMatcherAgent, EvaluatorAgent, RetrievalLogicAgent, RouterAgent, SinglePipelineAgent
from contracts import IncidentInput, PipelineResult, RetrievalContext, ReviewResult, RoutingDecision


logger = logging.getLogger(__name__)


def _is_enabled(env_name: str) -> bool:
    return str(os.getenv(env_name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _debug_log(event: str, **payload: object) -> None:
    if not _is_enabled("TRAFFIC_DEBUG"):
        return
    logger.info("%s | %s", event, json.dumps(payload, ensure_ascii=False, default=str))


def _read_csv_env(env_name: str, default_csv: str) -> list[str]:
    raw = str(os.getenv(env_name, default_csv) or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_agent_mode() -> str:
    mode = str(os.getenv("TRAFFIC_AGENT_MODE", "auto")).strip().lower()
    if mode in {"auto", "auto_adaptive", "adaptive"}:
        return "auto"
    if mode in {"single", "single_agent", "single_v2"}:
        return "single_agent"
    if mode in {"multi_no_review", "multi-without-review", "multi_without_review"}:
        return "multi_no_review"
    return "multi_with_review"


def _read_max_revision_rounds() -> int:
    raw = str(os.getenv("TRAFFIC_MAX_REVISION_ROUNDS", "2")).strip()
    try:
        return max(0, min(5, int(raw)))
    except ValueError:
        return 2


def _resolve_max_revision_rounds(agent_mode: str) -> int:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_MAX_REVISION_ROUNDS_MULTI_WITH_REVIEW", "3")).strip()
        try:
            return max(0, min(5, int(raw)))
        except ValueError:
            return 3
    if mode == "multi_no_review":
        return 0
    return _read_max_revision_rounds()


def _read_revision_stagnation_patience() -> int:
    raw = str(os.getenv("TRAFFIC_REVISION_STAGNATION_PATIENCE", "2")).strip()
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 2


def _read_revision_stagnation_patience_max() -> int:
    raw = str(os.getenv("TRAFFIC_REVISION_STAGNATION_PATIENCE_MAX", "4")).strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 4


def _resolve_dynamic_stagnation_patience(
    base_patience: int,
    max_patience: int,
    current_score: float,
    min_score_limit: float,
    retry_round: int,
    max_revision_rounds: int,
    missing_actions_count: int,
    violated_constraints_count: int,
) -> int:
    """根据当前修订状态动态放宽停滞阈值，减少低分阶段的过早停机。"""
    patience = max(1, base_patience)

    # 分数仍低于通过线时，给额外修订机会。
    if current_score < min_score_limit:
        patience += 1

    # 审查仍有缺失项/违规项时，说明仍有可优化空间。
    if missing_actions_count > 0:
        patience += 1
    if violated_constraints_count > 0:
        patience += 1

    # 前半程优先探索，后半程再收敛。
    if max_revision_rounds > 0 and retry_round <= max(1, max_revision_rounds // 2):
        patience += 1

    return min(max(1, max_patience), patience)


def _read_quality_guard_margin(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_QUALITY_GUARD_MARGIN_MULTI_WITH_REVIEW", "0.02")).strip()
        default_value = 0.02
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_QUALITY_GUARD_MARGIN", "0.03")).strip()
        default_value = 0.03

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(0.2, value))


def _read_rewrite_guard_ratio(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_REWRITE_GUARD_RATIO_MULTI_WITH_REVIEW", "0.7")).strip()
        default_value = 0.7
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_REWRITE_GUARD_RATIO", "0.8")).strip()
        default_value = 0.8

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(1.0, value))


def _read_min_score_limit(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_MIN_SCORE_LIMIT_MULTI_WITH_REVIEW", "0.78")).strip()
        default_value = 0.78
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_MIN_SCORE_LIMIT", "0.75")).strip()
        default_value = 0.75

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(1.0, value))


def _read_min_effective_improvement(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_MIN_EFFECTIVE_IMPROVEMENT_MULTI_WITH_REVIEW", "0.01")).strip()
        default_value = 0.01
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_MIN_EFFECTIVE_IMPROVEMENT", "0.01")).strip()
        default_value = 0.01

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(0.2, value))


def _read_max_ineffective_revisions(agent_mode: str) -> int:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_MAX_INEFFECTIVE_MULTI_WITH_REVIEW", "2")).strip()
        default_value = 2
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_MAX_INEFFECTIVE", "2")).strip()
        default_value = 2

    try:
        value = int(raw)
    except ValueError:
        value = default_value
    return max(1, min(6, value))


def _read_direct_pass_score(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_DIRECT_PASS_SCORE_MULTI_WITH_REVIEW", "0.95")).strip()
        default_value = 0.95
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_DIRECT_PASS_SCORE", "0.95")).strip()
        default_value = 0.95

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(1.0, value))


def _read_revision_accept_min_delta(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_ACCEPT_MIN_DELTA_MULTI_WITH_REVIEW", "0.002")).strip()
        default_value = 0.002
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_ACCEPT_MIN_DELTA", "0.003")).strip()
        default_value = 0.003

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(0.05, value))


def _read_revision_accept_score_drop_tolerance(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_ACCEPT_SCORE_DROP_TOLERANCE_MULTI_WITH_REVIEW", "0.01")).strip()
        default_value = 0.01
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_ACCEPT_SCORE_DROP_TOLERANCE", "0.01")).strip()
        default_value = 0.01

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(0.1, value))


def _read_revision_accept_dim_drop_tolerance(agent_mode: str) -> float:
    mode = str(agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        raw = str(os.getenv("TRAFFIC_REVISION_ACCEPT_DIM_DROP_TOLERANCE_MULTI_WITH_REVIEW", "0.02")).strip()
        default_value = 0.02
    else:
        raw = str(os.getenv("TRAFFIC_REVISION_ACCEPT_DIM_DROP_TOLERANCE", "0.02")).strip()
        default_value = 0.02

    try:
        value = float(raw)
    except ValueError:
        value = default_value
    return max(0.0, min(0.15, value))


def _is_revision_candidate_acceptable(
    current_review: ReviewResult,
    revised_review: ReviewResult,
    current_score: float,
    revised_score: float,
    min_accept_delta: float,
    score_drop_tolerance: float,
    dim_drop_tolerance: float,
) -> tuple[bool, str]:
    score_delta = revised_score - current_score

    # 明显收益直接接收
    if score_delta >= min_accept_delta:
        return True, "score_improved"

    # 子维度与硬缺陷必须不退化
    if len(revised_review.missing_actions) > len(current_review.missing_actions):
        return False, "missing_actions_worsened"
    if len(revised_review.violated_constraints) > len(current_review.violated_constraints):
        return False, "violated_constraints_worsened"

    if revised_review.executability_score + dim_drop_tolerance < current_review.executability_score:
        return False, "executability_worsened"
    if revised_review.safety_score + dim_drop_tolerance < current_review.safety_score:
        return False, "safety_worsened"
    if revised_review.compliance_score + dim_drop_tolerance < current_review.compliance_score:
        return False, "compliance_worsened"

    # 通用能力型门控：避免“可执行性+安全性”同时退化。
    if (
        revised_review.executability_score < current_review.executability_score
        and revised_review.safety_score < current_review.safety_score
        and revised_review.compliance_score <= current_review.compliance_score
    ):
        return False, "core_capability_joint_worsened"

    # 小幅波动可接收，超过分数容忍度拒收
    if score_delta < -score_drop_tolerance:
        return False, "overall_score_worsened"

    return True, "within_tolerance"


def _step_rewrite_ratio(old_steps: list[str], new_steps: list[str]) -> float:
    old_clean = [str(item or "").strip() for item in old_steps if str(item or "").strip()]
    new_clean = [str(item or "").strip() for item in new_steps if str(item or "").strip()]
    if not old_clean:
        return 0.0

    unchanged = sum(1 for step in old_clean if step in new_clean)
    return 1.0 - (unchanged / max(len(old_clean), 1))


class PipelineOrchestrator:
    """最小编排器：串联 Agent 链路并按模式切换执行。"""

    def __init__(
        self,
        dispatcher: DispatcherAgent | None = None,
        matcher: EntityMatcherAgent | None = None,
        retrieval: RetrievalLogicAgent | None = None,
        commander: CommanderAgent | None = None,
        evaluator: EvaluatorAgent | None = None,
        router: RouterAgent | None = None,
        single_agent: SinglePipelineAgent | None = None,
    ):
        self.dispatcher = dispatcher or DispatcherAgent()
        self.matcher = matcher or EntityMatcherAgent()
        self.retrieval = retrieval or RetrievalLogicAgent()
        self.commander = commander or CommanderAgent()
        self.evaluator = evaluator or EvaluatorAgent()
        self.router = router or RouterAgent()
        self.single_agent = single_agent or SinglePipelineAgent(
            retrieval_service=self.retrieval.service,
        )
        self._active_routing: RoutingDecision | None = None

    def _finalize_result(self, incident: IncidentInput, entities, context, draft, review, initial_draft=None, routing: RoutingDecision | None = None) -> PipelineResult:
        final_strategy = "\n".join(draft.steps)
        human_handoff = review.status != "APPROVED"
        base_initial_draft = initial_draft or draft
        active_routing = routing or self._active_routing

        if active_routing and entities is not None:
            entities.difficulty = active_routing.difficulty
            entities.difficulty_reason = active_routing.reason
            entities.difficulty_confidence = active_routing.confidence

        return PipelineResult(
            incident=incident,
            entities=entities,
            context=context,
            draft=draft,
            initial_draft=base_initial_draft,
            routing=active_routing,
            review=review,
            final_strategy=final_strategy,
            human_handoff=human_handoff,
        )

    @staticmethod
    def _draft_signature(draft) -> str:
        payload = {
            "focus": str(draft.focus or "").strip(),
            "steps": [str(item or "").strip() for item in draft.steps],
            "required_resources": [str(item or "").strip() for item in draft.required_resources],
            "legal_references": [str(item or "").strip() for item in draft.legal_references],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _draft_quality_score(draft) -> float:
        """启发式草案质量分，避免修订把可执行方案改差。"""
        steps = [str(item or "").strip() for item in draft.steps if str(item or "").strip()]
        if not steps:
            return 0.0

        weak_patterns = _read_csv_env("TRAFFIC_DRAFT_QUALITY_WEAK_PATTERNS", "")

        explicit_step_hits = sum(1 for step in steps if "由" in step and "，" in step and len(step) >= 16)
        weak_hits = sum(1 for step in steps if any(re.search(pattern, step) for pattern in weak_patterns))
        duplicate_hits = len(steps) - len(set(steps))

        step_count_score = min(len(steps) / 6.0, 1.0)
        action_ratio_score = explicit_step_hits / max(len(steps), 1)
        weak_penalty = (weak_hits + duplicate_hits) / max(len(steps), 1)
        reference_score = min(len(getattr(draft, "legal_references", []) or []) / 3.0, 1.0)

        score = 0.35 * step_count_score + 0.4 * action_ratio_score + 0.25 * reference_score - 0.2 * weak_penalty
        return max(0.0, min(score, 1.0))

    def run_once(self, incident: IncidentInput) -> PipelineResult:
        started_at = time.perf_counter()
        requested_mode = _read_agent_mode()

        if requested_mode == "auto":
            routing = self.router.decide(incident)
            effective_mode = str(routing.effective_mode or routing.route_target or "multi_with_review").strip().lower()
        else:
            effective_mode = requested_mode
            routing = RoutingDecision(
                requested_mode=requested_mode,
                effective_mode=effective_mode,
                route_target=effective_mode,
                difficulty="UNKNOWN",
                reason="固定模式执行，未启用auto路由",
                confidence=1.0,
                used_llm=False,
                fallback_to_g5=False,
                fallback_reason="",
                rule_hit_count=0,
                rule_hits=[],
            )

        if effective_mode not in {"single_agent", "multi_no_review", "multi_with_review"}:
            routing.fallback_to_g5 = True
            routing.fallback_reason = f"无效路由目标({effective_mode})，回退到G5链路"
            effective_mode = "multi_with_review"

        routing.effective_mode = effective_mode
        if not routing.route_target:
            routing.route_target = effective_mode

        self._active_routing = routing
        os.environ["TRAFFIC_EFFECTIVE_AGENT_MODE"] = effective_mode

        _debug_log(
            "pipeline_mode_selected",
            requested_mode=requested_mode,
            effective_mode=effective_mode,
            route_target=routing.route_target,
            difficulty=routing.difficulty,
            route_confidence=round(routing.confidence, 4),
            fallback_to_g5=routing.fallback_to_g5,
        )

        max_revision_rounds = _resolve_max_revision_rounds(effective_mode)
        stagnation_patience = _read_revision_stagnation_patience()
        stagnation_patience_max = _read_revision_stagnation_patience_max()
        quality_guard_margin = _read_quality_guard_margin(effective_mode)
        rewrite_guard_ratio = _read_rewrite_guard_ratio(effective_mode)
        min_score_limit = _read_min_score_limit(effective_mode)
        direct_pass_score = _read_direct_pass_score(effective_mode)
        revision_accept_min_delta = _read_revision_accept_min_delta(effective_mode)
        revision_accept_score_drop_tolerance = _read_revision_accept_score_drop_tolerance(effective_mode)
        revision_accept_dim_drop_tolerance = _read_revision_accept_dim_drop_tolerance(effective_mode)
        min_effective_improvement = _read_min_effective_improvement(effective_mode)
        max_ineffective_revisions = _read_max_ineffective_revisions(effective_mode)

        if effective_mode == "single_agent":
            entities, context, draft = self.single_agent.solve(incident)
            review = ReviewResult(
                status="APPROVED",
                reason="实验模式：single_agent 已执行单智能体一体化处置，跳过评审",
                violated_constraints=[],
                missing_actions=[],
                risk_notes=["single_agent 未执行审查与修订，请人工复核高风险步骤"],
                retry_count=0,
                failure_type="",
            )
            _debug_log(
                "pipeline_completed_single_agent",
                agent_mode=effective_mode,
                elapsed_seconds=round(time.perf_counter() - started_at, 3),
                image_attached=bool(incident.image_bytes),
            )
            return self._finalize_result(incident, entities, context, draft, review)

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
        initial_draft = draft
        _debug_log(
            "pipeline_after_generate",
            agent_mode=effective_mode,
            focus=draft.focus,
            step_count=len(draft.steps),
        )

        if effective_mode == "multi_no_review":
            review = ReviewResult(
                status="APPROVED",
                reason="实验模式：已跳过评审",
                violated_constraints=[],
                missing_actions=[],
                risk_notes=["实验模式下未执行审查，请人工复核"],
                retry_count=0,
                failure_type="",
            )
            _debug_log(
                "pipeline_completed_no_review",
                agent_mode=effective_mode,
                elapsed_seconds=round(time.perf_counter() - started_at, 3),
            )
            return self._finalize_result(incident, entities, context, draft, review, initial_draft=initial_draft)

        current_draft = draft
        current_review = self.evaluator.review(incident, entities, context, current_draft, retry_count=0)
        if current_review.failure_type == "llm_review_failed":
            current_review.status = "APPROVED"
            current_review.reason = "LLM审查失败，已跳过审查并返回原始方案"
            current_review.missing_actions = []
            current_review.violated_constraints = []
            if current_review.overall_score <= 0:
                current_review.overall_score = round(self._draft_quality_score(current_draft), 4)
            _debug_log(
                "pipeline_review_skip_on_llm_failure",
                retry_round=0,
                fallback_score=round(current_review.overall_score, 4),
                elapsed_seconds=round(time.perf_counter() - started_at, 3),
            )
            return self._finalize_result(incident, entities, context, current_draft, current_review, initial_draft=initial_draft)

        current_score = current_review.overall_score if current_review.overall_score > 0 else self._draft_quality_score(current_draft)
        best_draft = current_draft
        best_review = current_review
        best_score = current_score
        last_signature = self._draft_signature(current_draft)
        stagnation_count = 0
        ineffective_count = 0
        retry_round = 0

        while True:
            _debug_log(
                "pipeline_after_review",
                agent_mode=effective_mode,
                status=current_review.status,
                retry_count=current_review.retry_count,
                failure_type=current_review.failure_type,
                missing_actions=current_review.missing_actions,
                overall_score=round(current_score, 4),
                score_threshold=round(current_review.score_threshold or min_score_limit, 4),
                direct_pass_score=round(direct_pass_score, 4),
            )

            if current_score > best_score:
                best_score = current_score
                best_draft = current_draft
                best_review = current_review

            if current_score >= direct_pass_score:
                current_review.status = "APPROVED"
                _debug_log(
                    "pipeline_completed_direct_pass",
                    status=current_review.status,
                    elapsed_seconds=round(time.perf_counter() - started_at, 3),
                    revision_round=retry_round,
                    overall_score=round(current_score, 4),
                    direct_pass_score=round(direct_pass_score, 4),
                )
                return self._finalize_result(incident, entities, context, current_draft, current_review, initial_draft=initial_draft)

            # 纯打分制：首轮仅允许高分直通，避免方案一遍过。
            if retry_round > 0 and current_score >= min_score_limit:
                current_review.status = "APPROVED"
                _debug_log(
                    "pipeline_completed",
                    status=current_review.status,
                    elapsed_seconds=round(time.perf_counter() - started_at, 3),
                    revision_round=retry_round,
                    overall_score=round(current_score, 4),
                )
                return self._finalize_result(incident, entities, context, current_draft, current_review, initial_draft=initial_draft)

            if retry_round >= max_revision_rounds:
                final_review = current_review
                final_review.reason = (
                    f"{final_review.reason}；达到最大修订次数({max_revision_rounds})，停止继续修订".strip("；")
                )
                final_review.failure_type = final_review.failure_type or "revision_exhausted"
                final_review.ineffective_revision_count = ineffective_count
                _debug_log(
                    "pipeline_revision_exhausted",
                    final_status=final_review.status,
                    revision_round=retry_round,
                    ineffective_count=ineffective_count,
                    elapsed_seconds=round(time.perf_counter() - started_at, 3),
                )
                if best_score > current_score:
                    _debug_log(
                        "pipeline_use_best_draft_on_exhausted",
                        best_score=round(best_score, 4),
                        current_score=round(current_score, 4),
                    )
                    best_review.ineffective_revision_count = ineffective_count
                    return self._finalize_result(incident, entities, context, best_draft, best_review, initial_draft=initial_draft)
                return self._finalize_result(incident, entities, context, current_draft, final_review, initial_draft=initial_draft)

            revised_draft = self.commander.revise(incident, entities, context, current_draft, current_review)
            current_quality_score = self._draft_quality_score(current_draft)
            revised_quality_score = self._draft_quality_score(revised_draft)
            rewrite_ratio = _step_rewrite_ratio(current_draft.steps, revised_draft.steps)

            # 防止修订把可执行草案改差：若明显劣化则保留当前草案。
            if revised_quality_score + quality_guard_margin < current_quality_score:
                _debug_log(
                    "pipeline_revision_quality_guard",
                    retry_round=retry_round + 1,
                    current_quality_score=round(current_quality_score, 4),
                    revised_quality_score=round(revised_quality_score, 4),
                    rewrite_ratio=round(rewrite_ratio, 4),
                    quality_guard_margin=round(quality_guard_margin, 4),
                    action="keep_current_draft",
                )
                revised_draft = current_draft

            # 防止大幅重写但质量未变好：优先保留已验证可执行的当前草案。
            if (
                rewrite_ratio >= rewrite_guard_ratio
                and revised_quality_score <= current_quality_score
            ):
                _debug_log(
                    "pipeline_revision_rewrite_guard",
                    retry_round=retry_round + 1,
                    rewrite_ratio=round(rewrite_ratio, 4),
                    rewrite_guard_ratio=round(rewrite_guard_ratio, 4),
                    current_quality_score=round(current_quality_score, 4),
                    revised_quality_score=round(revised_quality_score, 4),
                    action="keep_current_draft",
                )
                revised_draft = current_draft

            signature = self._draft_signature(revised_draft)
            signature_unchanged = signature == last_signature and bool(signature)
            last_signature = signature

            revised_review = self.evaluator.review(
                incident,
                entities,
                context,
                revised_draft,
                retry_count=retry_round + 1,
            )
            if revised_review.failure_type == "llm_review_failed":
                revised_review.status = "APPROVED"
                revised_review.reason = "LLM审查失败，已跳过审查并返回修订前原方案"
                revised_review.missing_actions = []
                revised_review.violated_constraints = []
                if revised_review.overall_score <= 0:
                    revised_review.overall_score = round(current_score, 4)
                _debug_log(
                    "pipeline_review_skip_on_llm_failure",
                    retry_round=retry_round + 1,
                    fallback_score=round(revised_review.overall_score, 4),
                    elapsed_seconds=round(time.perf_counter() - started_at, 3),
                )
                return self._finalize_result(incident, entities, context, current_draft, revised_review, initial_draft=initial_draft)

            revised_score = revised_review.overall_score if revised_review.overall_score > 0 else self._draft_quality_score(revised_draft)
            score_delta = revised_score - current_score

            candidate_accepted, candidate_decision = _is_revision_candidate_acceptable(
                current_review=current_review,
                revised_review=revised_review,
                current_score=current_score,
                revised_score=revised_score,
                min_accept_delta=revision_accept_min_delta,
                score_drop_tolerance=revision_accept_score_drop_tolerance,
                dim_drop_tolerance=revision_accept_dim_drop_tolerance,
            )

            if not candidate_accepted:
                _debug_log(
                    "pipeline_revision_candidate_rejected",
                    retry_round=retry_round + 1,
                    decision=candidate_decision,
                    current_score=round(current_score, 4),
                    revised_score=round(revised_score, 4),
                    score_delta=round(score_delta, 4),
                )
                revised_draft = current_draft
                revised_review = current_review
                revised_score = current_score
                score_delta = 0.0
                signature_unchanged = True
            else:
                _debug_log(
                    "pipeline_revision_candidate_accepted",
                    retry_round=retry_round + 1,
                    decision=candidate_decision,
                    current_score=round(current_score, 4),
                    revised_score=round(revised_score, 4),
                    score_delta=round(score_delta, 4),
                )

            # 仅当“文本未变化且得分几乎不动”时才累计停滞，避免无效早停。
            if signature_unchanged and abs(score_delta) < (min_effective_improvement / 2.0):
                stagnation_count += 1
            else:
                stagnation_count = 0

            dynamic_stagnation_patience = _resolve_dynamic_stagnation_patience(
                base_patience=stagnation_patience,
                max_patience=stagnation_patience_max,
                current_score=revised_score,
                min_score_limit=min_score_limit,
                retry_round=retry_round + 1,
                max_revision_rounds=max_revision_rounds,
                missing_actions_count=len(revised_review.missing_actions),
                violated_constraints_count=len(revised_review.violated_constraints),
            )

            if abs(score_delta) < min_effective_improvement:
                ineffective_count += 1
            else:
                ineffective_count = 0

            revised_review.score_delta = round(score_delta, 4)
            revised_review.ineffective_revision_count = ineffective_count

            _debug_log(
                "pipeline_after_retry",
                revised_step_count=len(revised_draft.steps),
                retry_round=retry_round + 1,
                stagnation_count=stagnation_count,
                dynamic_stagnation_patience=dynamic_stagnation_patience,
                ineffective_count=ineffective_count,
                score_delta=round(score_delta, 4),
                overall_score=round(revised_score, 4),
                min_effective_improvement=round(min_effective_improvement, 4),
            )

            current_draft = revised_draft
            current_review = revised_review
            current_score = revised_score
            retry_round += 1

            if stagnation_count >= dynamic_stagnation_patience:
                current_review.reason = (
                    f"{current_review.reason}；修订内容连续未变化，已停止继续迭代".strip("；")
                )
                current_review.failure_type = current_review.failure_type or "revision_stagnation"
                current_review.ineffective_revision_count = ineffective_count
                _debug_log(
                    "pipeline_revision_stagnation",
                    status=current_review.status,
                    retry_round=retry_round,
                    elapsed_seconds=round(time.perf_counter() - started_at, 3),
                )
                if best_score > current_score:
                    best_review.ineffective_revision_count = ineffective_count
                    return self._finalize_result(incident, entities, context, best_draft, best_review, initial_draft=initial_draft)
                return self._finalize_result(incident, entities, context, current_draft, current_review, initial_draft=initial_draft)

            if ineffective_count >= max_ineffective_revisions:
                current_review.reason = (
                    f"{current_review.reason}；连续无效修订达到阈值({max_ineffective_revisions})，停止继续修订".strip("；")
                )
                current_review.failure_type = current_review.failure_type or "revision_ineffective"
                current_review.ineffective_revision_count = ineffective_count
                _debug_log(
                    "pipeline_revision_ineffective_stop",
                    status=current_review.status,
                    retry_round=retry_round,
                    ineffective_count=ineffective_count,
                    elapsed_seconds=round(time.perf_counter() - started_at, 3),
                )
                if best_score > current_score:
                    best_review.ineffective_revision_count = ineffective_count
                    return self._finalize_result(incident, entities, context, best_draft, best_review, initial_draft=initial_draft)
                return self._finalize_result(incident, entities, context, current_draft, current_review, initial_draft=initial_draft)

    def close(self) -> None:
        shared_service = (
            self.retrieval.service is not None
            and self.single_agent.retrieval_service is self.retrieval.service
        )
        self.retrieval.close()
        if shared_service:
            self.single_agent.retrieval_service = None
            return
        self.single_agent.close()
