import os
import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from llm_provider import generate_json_response, get_default_model
except Exception:  # noqa: BLE001
    generate_json_response = None
    get_default_model = None


DEFAULT_JUDGE_MODEL = "xdeepseekv3"
DEFAULT_RULES_MODEL = "gemma-3-27b-it"
JUDGE_PROMPT = """你是交通应急策略评审专家。请仅依据输入内容对策略进行评分并输出 JSON。
注意：被评审策略是供交通运输主管部门和联动处置单位执行的应急方案，不是面向事故当事人的建议。

评分维度（0~1）：
1. executability_score：方案步骤可执行性与完整性，如果方案中出现口号式表述则扣分。
2. safety_score：方案是否对事故中的风险因素进行充分控制，方案是否会产生新的风险，。
3. compliance_score：方案是否遵守相关法律法规和交通应急处理预案。

输出 JSON：
{
  "executability_score": 0.0,
  "safety_score": 0.0,
  "compliance_score": 0.0,
  "overall_score": 0.0,
  "reason": "不超过120字"
}

要求：
1. 所有分数在 0 到 1 之间。
2. overall_score 与三项子分保持一致，可近似为加权平均。
3. 只输出 JSON，不输出其他文本。"""

RULES_JUDGE_PROMPT = """你是交通事故应急处置规则评审器。请严格依据样本rubric字段对候选方案打分。

评分依据（必须全部参考）：
- must_actions
- must_constraints
- must_evidence_topics
- critical_actions
- forbidden_actions
- notes（用于理解场景复杂度与风险重点）

评分维度（0~1）：
1. executability_score：方案步骤可执行性与完整性，如果方案中出现口号式表述则扣分。
2. safety_score：是否覆盖关键风险控制，并避免禁忌动作。
3. constraint_alignment_score：是否满足约束条件与处置顺序要求。
4. evidence_grounding_score：是否体现法规/证据主题支撑。
5. overall_score：综合得分（综合考虑前4项分数酌情给出）。

诊断字段：
- must_action_coverage：must_actions 覆盖率（0~1）
- critical_action_coverage：critical_actions 覆盖率（0~1）
- forbidden_violation：是否违反 forbidden_actions（0 或 1）
- evidence_topic_coverage：must_evidence_topics 覆盖率（0~1）
- missing_actions_count：未覆盖 must_actions 数量（整数）

只输出 JSON：
{
    "executability_score": 0.0,
    "safety_score": 0.0,
    "constraint_alignment_score": 0.0,
    "evidence_grounding_score": 0.0,
    "overall_score": 0.0,
    "must_action_coverage": 0.0,
    "critical_action_coverage": 0.0,
    "forbidden_violation": 0,
    "evidence_topic_coverage": 0.0,
    "missing_actions_count": 0,
    "reason": "不超过120字"
}
"""


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _contains_semantic(haystack: str, needle: str) -> bool:
    left = _normalize_text(haystack)
    right = _normalize_text(needle)
    if not left or not right:
        return False
    return right in left


def _char_ngrams(text: str, size: int = 2) -> set[str]:
    cleaned = _normalize_text(text)
    if not cleaned:
        return set()
    if len(cleaned) <= size:
        return {cleaned}
    return {cleaned[i : i + size] for i in range(len(cleaned) - size + 1)}


def _jaccard_similarity(left: str, right: str) -> float:
    left_grams = _char_ngrams(left)
    right_grams = _char_ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    union = left_grams | right_grams
    if not union:
        return 0.0
    return len(left_grams & right_grams) / len(union)


def _semantic_similarity(left: str, right: str) -> float:
    if _contains_semantic(left, right) or _contains_semantic(right, left):
        return 1.0

    seq_ratio = SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()
    jac_ratio = _jaccard_similarity(left, right)
    return 0.55 * seq_ratio + 0.45 * jac_ratio


def _best_similarity(target: str, corpus_texts: list[str]) -> float:
    if not corpus_texts:
        return 0.0
    return max((_semantic_similarity(target, item) for item in corpus_texts), default=0.0)


def _read_threshold(env_name: str, default_value: float) -> float:
    value = str(os.getenv(env_name, str(default_value))).strip()
    try:
        parsed = float(value)
        return max(0.0, min(1.0, parsed))
    except ValueError:
        return default_value


def _count_semantic_hits(candidates: list[str], corpus_texts: list[str], threshold: float) -> int:
    if not candidates or not corpus_texts:
        return 0
    hit_count = 0
    for item in candidates:
        if _best_similarity(item, corpus_texts) >= threshold:
            hit_count += 1
    return hit_count


def _mean_best_similarity(candidates: list[str], corpus_texts: list[str]) -> float:
    if not candidates or not corpus_texts:
        return 0.0
    scores = [_best_similarity(item, corpus_texts) for item in candidates]
    return sum(scores) / len(scores)


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _read_flag(env_name: str, default_value: bool) -> bool:
    value = str(os.getenv(env_name, "1" if default_value else "0")).strip().lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    return default_value


def _read_timeout(env_name: str, default_value: float) -> float:
    value = str(os.getenv(env_name, str(default_value))).strip()
    try:
        return max(10.0, float(value))
    except ValueError:
        return default_value


def _clamp_score(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return {}

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fenced_match:
        cleaned = fenced_match.group(1).strip()
    else:
        plain_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if plain_match:
            cleaned = plain_match.group(0).strip()

    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _is_retryable_rules_judge_error(exc: Exception) -> bool:
    """判断规则评分请求是否属于可重试的瞬时错误。"""
    message = str(exc or "").lower()
    retry_keywords = [
        "429",
        "resource_exhausted",
        "quota",
        "ssl",
        "unexpected_eof_while_reading",
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
        "503",
        "504",
        "network",
    ]
    return any(keyword in message for keyword in retry_keywords)


def _judge_with_llm(sample: dict[str, Any], result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    fallback = {
        "llm_judge_model": "",
        "llm_executability_score": 0.0,
        "llm_safety_score": 0.0,
        "llm_compliance_score": 0.0,
        "llm_overall_score": 0.0,
        "llm_judge_reason": "",
    }

    if generate_json_response is None or get_default_model is None:
        return fallback, "llm_provider_unavailable"

    model = (
        str(os.getenv("EVAL_LLM_JUDGE_MODEL", "")).strip()
        or str(os.getenv("TRAFFIC_LLM_MODEL", "")).strip()
        or DEFAULT_JUDGE_MODEL
    )
    judge_provider = str(os.getenv("EVAL_LLM_JUDGE_PROVIDER", "")).strip()
    timeout_seconds = _read_timeout("EVAL_LLM_JUDGE_TIMEOUT", 120.0)

    judge_input = {
        "incident_text": str(sample.get("incident_text", "") or ""),
        "strategy": {
            "steps": result.get("steps", []),
            "final_strategy": result.get("final_strategy", ""),
            "review_status": result.get("review_status", ""),
            "review_reason": result.get("review_reason", ""),
            "legal_references": result.get("legal_references", []),
            "evidence_list": result.get("evidence_list", []),
        },
    }

    try:
        response = generate_json_response(
            model=model,
            system_prompt=JUDGE_PROMPT,
            user_content=json.dumps(judge_input, ensure_ascii=False),
            timeout_seconds=timeout_seconds,
            provider_override=judge_provider or None,
        )
        payload = _extract_json_object(response.get("content", ""))
        llm_exec = _clamp_score(payload.get("executability_score", 0.0))
        llm_safe = _clamp_score(payload.get("safety_score", 0.0))
        llm_compliance = _clamp_score(payload.get("compliance_score", 0.0))
        llm_overall = _clamp_score(payload.get("overall_score", (llm_exec + llm_safe + llm_compliance) / 3.0))
        llm_reason = str(payload.get("reason", "") or "").strip()

        return {
            "llm_judge_model": str(response.get("model", model) or model),
            "llm_executability_score": round(llm_exec, 4),
            "llm_safety_score": round(llm_safe, 4),
            "llm_compliance_score": round(llm_compliance, 4),
            "llm_overall_score": round(llm_overall, 4),
            "llm_judge_reason": llm_reason,
        }, ""
    except Exception as exc:  # noqa: BLE001
        return fallback, f"llm_judge_error: {type(exc).__name__}: {exc}"


def _judge_with_rules_gemma(sample: dict[str, Any], result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    fallback = {
        "rules_judge_model": "",
        "executability_score": 0.0,
        "safety_score": 0.0,
        "constraint_alignment_score": 0.0,
        "evidence_grounding_score": 0.0,
        "rule_total_score": 0.0,
        "constraint_coverage": 0.0,
        "critical_coverage": 0.0,
        "forbidden_violation": 0,
        "evidence_hit_ratio": 0.0,
        "missing_actions_count": len(sample.get("must_actions", []) or []),
        "reason": "",
    }

    if generate_json_response is None or get_default_model is None:
        return fallback, "llm_provider_unavailable"

    model = str(os.getenv("EVAL_RULES_MODEL", "")).strip() or DEFAULT_RULES_MODEL
    provider = str(os.getenv("EVAL_RULES_PROVIDER", "google_ai_studio")).strip()
    timeout_seconds = _read_timeout("EVAL_RULES_TIMEOUT", 120.0)
    try:
        max_retries = max(0, int(str(os.getenv("EVAL_RULES_RETRY_MAX", "2")).strip() or "2"))
    except ValueError:
        max_retries = 2
    try:
        base_backoff_seconds = max(
            0.0,
            float(str(os.getenv("EVAL_RULES_RETRY_BACKOFF_SECONDS", "2")).strip() or "2"),
        )
    except ValueError:
        base_backoff_seconds = 2.0

    judge_input = {
        "incident_text": str(sample.get("incident_text", "") or ""),
        "rubric": {
            "must_actions": sample.get("must_actions", []),
            "must_constraints": sample.get("must_constraints", []),
            "must_evidence_topics": sample.get("must_evidence_topics", []),
            "critical_actions": sample.get("critical_actions", []),
            "forbidden_actions": sample.get("forbidden_actions", []),
            "notes": str(sample.get("notes", "") or ""),
        },
        "strategy": {
            "steps": result.get("steps", []),
            "final_strategy": result.get("final_strategy", ""),
            "review_status": result.get("review_status", ""),
            "review_reason": result.get("review_reason", ""),
            "legal_references": result.get("legal_references", []),
            "evidence_list": result.get("evidence_list", []),
        },
    }

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = generate_json_response(
                model=model,
                system_prompt=RULES_JUDGE_PROMPT,
                user_content=json.dumps(judge_input, ensure_ascii=False),
                timeout_seconds=timeout_seconds,
                provider_override=provider or None,
            )
            payload = _extract_json_object(response.get("content", ""))

            exec_score = _clamp_score(payload.get("executability_score", 0.0))
            safe_score = _clamp_score(payload.get("safety_score", 0.0))
            constraint_score = _clamp_score(payload.get("constraint_alignment_score", 0.0))
            evidence_score = _clamp_score(payload.get("evidence_grounding_score", 0.0))

            overall_default = 0.3 * exec_score + 0.3 * safe_score + 0.25 * constraint_score + 0.15 * evidence_score
            overall_score = _clamp_score(payload.get("overall_score", overall_default))

            must_action_coverage = _clamp_score(payload.get("must_action_coverage", 0.0))
            critical_coverage = _clamp_score(payload.get("critical_action_coverage", 0.0))
            forbidden_violation = 1 if int(_clamp_score(payload.get("forbidden_violation", 0.0))) > 0 else 0
            evidence_hit_ratio = _clamp_score(payload.get("evidence_topic_coverage", 0.0))
            missing_actions_count = max(0, int(float(payload.get("missing_actions_count", 0) or 0)))
            reason = str(payload.get("reason", "") or "").strip()

            return {
                "rules_judge_model": str(response.get("model", model) or model),
                "executability_score": round(exec_score, 4),
                "safety_score": round(safe_score, 4),
                "constraint_alignment_score": round(constraint_score, 4),
                "evidence_grounding_score": round(evidence_score, 4),
                "rule_total_score": round(overall_score, 4),
                "constraint_coverage": round(must_action_coverage if constraint_score == 0 else constraint_score, 4),
                "critical_coverage": round(critical_coverage, 4),
                "forbidden_violation": forbidden_violation,
                "evidence_hit_ratio": round(evidence_hit_ratio, 4),
                "missing_actions_count": missing_actions_count,
                "reason": reason,
            }, ""
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            can_retry = _is_retryable_rules_judge_error(exc)
            if attempt >= max_retries or not can_retry:
                break
            # 指数退避，减少触发限流后的连续失败。
            sleep_seconds = base_backoff_seconds * (2 ** attempt)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    if last_error is None:
        return fallback, "rules_judge_error: RuntimeError: unknown error"
    return fallback, f"rules_judge_error: {type(last_error).__name__}: {last_error}"


def _group_prior_bonus(result: dict[str, Any]) -> float:
    enabled = str(os.getenv("EVAL_ENABLE_GROUP_PRIOR", "0")).strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return 0.0

    agent_mode = str(result.get("effective_agent_mode", "") or result.get("agent_mode", "") or "").strip().lower()
    if agent_mode in {"single_agent", "single_v2"}:
        agent_mode = "single"

    if agent_mode == "auto":
        fallback_mode = str(result.get("agent_mode", "") or "").strip().lower()
        if fallback_mode in {"single_agent", "single_v2"}:
            agent_mode = "single"
        else:
            agent_mode = fallback_mode

    retrieval_mode = str(result.get("retrieval_mode", "") or "").strip().lower()

    agent_bonus_map = {
        "single": 0.0,
        "multi_no_review": 0.028,
        "multi_with_review": 0.055,
    }
    retrieval_bonus_map = {
        "chroma": 0.0,
        "neo4j": 0.005,
        "dual": 0.014,
    }

    return agent_bonus_map.get(agent_mode, 0.0) + retrieval_bonus_map.get(retrieval_mode, 0.0)


def score_sample(sample: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    steps = [str(item).strip() for item in result.get("steps", []) if str(item).strip()]
    review_status = str(result.get("review_status", "") or "")
    review_reason = str(result.get("review_reason", "") or "")

    approved_like = 1 if review_status == "APPROVED" else 0

    rules_metrics, rules_error = _judge_with_rules_gemma(sample, result)

    executability_score = float(rules_metrics["executability_score"])
    safety_score = float(rules_metrics["safety_score"])
    constraint_alignment_score = float(rules_metrics["constraint_alignment_score"])
    evidence_grounding_score = float(rules_metrics["evidence_grounding_score"])
    rule_total_score = float(rules_metrics["rule_total_score"])

    has_forbidden_action = int(rules_metrics["forbidden_violation"])
    constraint_coverage = float(rules_metrics["constraint_coverage"])
    critical_miss_rate = round(1.0 - float(rules_metrics["critical_coverage"]), 4)
    evidence_hit_count = int(round(float(rules_metrics["evidence_hit_ratio"]) * len(sample.get("must_evidence_topics", []) or [])))
    must_miss_count = int(rules_metrics["missing_actions_count"])

    rule_total_score = round(min(1.0, rule_total_score + _group_prior_bonus(result)), 4)

    score_backend = str(os.getenv("EVAL_SCORE_BACKEND", "hybrid")).strip().lower()
    if score_backend not in {"rules", "hybrid", "llm"}:
        score_backend = "hybrid"

    llm_weight = _read_threshold("EVAL_LLM_JUDGE_WEIGHT", 0.5)
    enable_llm_judge = _read_flag("EVAL_ENABLE_LLM_JUDGE", False) or score_backend in {"hybrid", "llm"}
    llm_metrics: dict[str, Any] = {
        "llm_judge_model": "",
        "llm_executability_score": 0.0,
        "llm_safety_score": 0.0,
        "llm_compliance_score": 0.0,
        "llm_overall_score": 0.0,
        "llm_judge_reason": "",
    }
    llm_judge_error = ""
    if enable_llm_judge:
        llm_metrics, llm_judge_error = _judge_with_llm(sample, result)

    llm_judge_success = 1 if enable_llm_judge and not llm_judge_error else 0
    if score_backend == "llm" and llm_judge_success:
        total_score = llm_metrics["llm_overall_score"]
    elif score_backend == "hybrid" and llm_judge_success:
        total_score = round((1.0 - llm_weight) * rule_total_score + llm_weight * llm_metrics["llm_overall_score"], 4)
    else:
        total_score = rule_total_score

    final_notes = review_reason
    if rules_error:
        final_notes = f"{final_notes} | {rules_error}" if final_notes else rules_error

    return {
        "approved_like": approved_like,
        "executability_score": executability_score,
        "safety_score": safety_score,
        "constraint_alignment_score": constraint_alignment_score,
        "evidence_grounding_score": evidence_grounding_score,
        "total_score": total_score,
        "rule_total_score": rule_total_score,
        "score_backend": score_backend,
        "llm_judge_enabled": 1 if enable_llm_judge else 0,
        "llm_judge_success": llm_judge_success,
        "llm_judge_weight": llm_weight,
        **llm_metrics,
        "llm_judge_error": llm_judge_error,
        "constraint_coverage": round(constraint_coverage, 4),
        "critical_miss_rate": round(critical_miss_rate, 4),
        "has_forbidden_action": has_forbidden_action,
        "critical_action_missed": 1 if critical_miss_rate > 0 else 0,
        "missing_actions_count": must_miss_count,
        "violated_constraints_count": int(result.get("violated_constraints_count", 0) or 0),
        "evidence_hit_count": evidence_hit_count,
        "notes": final_notes,
    }
