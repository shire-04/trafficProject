import base64
import csv
import json
import logging
import os
import re
from typing import Callable

from dataclasses import replace

from contracts import CasualtyEstimate, ExtractedEntities, IncidentInput, MatchedNode, RetrievalContext, ReviewResult, StrategyDraft
from entity_aliases import EventAliasStore
from llm_provider import generate_json_response, get_default_model
from retrieval_logic import DualRetrievalService

IMAGE_ANALYSIS_PROMPT = """你是交通事故现场图像研判助手。请只输出 JSON，不要输出任何解释。
字段要求：
{
  "incident_type": "若无法判断则为空字符串",
  "weather": "若无法判断则为空字符串",
    "hazards": ["图像中可辨认的风险/危害要素短语，如起火、泄漏、冒烟、伤员、道路受阻等"],
    "vehicles": ["图像中可辨认的车辆类型短语，如货车、槽罐车、客车、轿车等"],
    "location_features": ["图像中可辨认的道路或位置特征，如高速、隧道、收费站、桥梁、服务区等"],
    "casualties": {"deaths": 0, "injuries": 0, "missing": 0},
  "evidence": ["简短中文证据描述"],
  "confidence": 0.0
}
如果无法判断，返回空字符串、空数组和较低置信度。"""

DEFAULT_VISION_TIMEOUT_SECONDS = 600.0
DEFAULT_LLM_TIMEOUT_SECONDS = 600.0
DEFAULT_MATCHER_SHORTLIST_LIMIT = 12

logger = logging.getLogger(__name__)

TEXT_ANALYSIS_PROMPT = """你是交通事故接警解析助手。请基于用户描述提取结构化实体，只输出 JSON，不要输出任何解释。
字段要求：
{
    "incident_type": "用简洁中文概括用户描述中的突发事件表达，尽量保留用户语义，不要求与知识图谱节点完全一致；若不明确则为空字符串",
    "weather": "文本中明确提到的天气或环境条件，若不明确则为空字符串",
    "hazards": ["文本中明确提到的风险/危害要素短语，如起火、泄漏、爆炸、伤员、道路中断等"],
    "vehicles": ["文本中明确提到的车辆类型短语，如危化品车、货车、客车、轿车等"],
    "location_features": ["文本中明确提到的道路或位置特征，如高速、隧道、桥梁、收费站、枢纽等"],
    "casualties": {"deaths": 0, "injuries": 0, "missing": 0},
    "extract_confidence": 0.0
}
要求：
1. 如果字段无法判断，返回空字符串、空数组或 null。
2. 伤亡人数必须是非负整数，若文本未提及则返回 null。
3. 只输出合法 JSON。
"""

TEXT_ANALYSIS_RETRY_PROMPT = """你是交通事故接警解析助手。上一轮抽取失败了，这一轮请直接给出结果，禁止返回空对象。
只输出 JSON，不要输出解释。
字段固定为：
{
    "incident_type": "尽量保留用户原始语义的简洁事故表达；若无法判断则为空字符串",
    "weather": "若明确提到天气则填写，否则为空字符串",
    "hazards": ["明确提到的风险要素"],
    "vehicles": ["明确提到的车辆类型"],
    "location_features": ["明确提到的道路/位置特征"],
    "casualties": {"deaths": null, "injuries": null, "missing": null},
    "extract_confidence": 0.0
}
规则：
1. 只填写描述中明确出现的信息。
2. 不知道就留空，不要编造。
3. 不允许返回空 JSON 对象 `{}`。
4. 只输出合法 JSON。
"""

SEVERITY_CLASSIFICATION_PROMPT = """你是公路交通突发事件定级助手。你的任务不是抽取实体，而是根据事件描述和已提取的事故信息，判断本次事件的预警/响应级别。
你必须重点参考以下《国家交通应急预案》分级依据：

公路交通突发事件预警级别描述分为以下四个方面：
1.预警级别
2.级别描述
3.颜色标示
4.事件情形

I级
特别严重
红色
* 因突发事件可能导致国家干线公路交通毁坏、中断、阻塞或者大量车辆积压、人员滞留，通行能力影响周边省份，抢修、处置时间预计在24小时以上时
* 因突发事件可能导致重要客运枢纽运行中断，造成大量旅客滞留，恢复运行及人员疏散预计在48小时以上时 
* 发生因重要物资缺乏、价格大幅波动可能严重影响全国或者大片区经济整体运行和人民正常生活，超出省级交通运输主管部门运力组织能力时
* 其他可能需要由交通运输部提供应急保障时

II级
严重
橙色
* 因突发事件可能导致国家干线公路交通毁坏、中断、阻塞或者大量车辆积压、人员滞留，抢修、处置时间预计在12小时以上时
* 因突发事件可能导致重要客运枢纽运行中断，造成大量旅客滞留，恢复运行及人员疏散预计在24小时以上时 
* 发生因重要物资缺乏、价格大幅波动可能严重影响省域内经济整体运行和人民正常生活时
* 其他可能需要由省级交通运输主管部门提供应急保障时

III级
较重
黄色
* Ⅲ级预警分级条件由省级交通运输主管部门负责参照Ⅰ级和Ⅱ级预警等级，结合地方特点确定

IV级
一般
蓝色
* Ⅳ级预警分级条件由省级交通运输主管部门负责参照Ⅰ级、Ⅱ级和Ⅲ级预警等级，结合地方特点确定

输出要求：只输出 JSON，不要输出解释。
字段要求：
{
    "severity": "从以下集合中选择一个：特别重大, 重大, 较大, 一般, UNKNOWN",
    "severity_reason": "简要说明定级依据，若无法判断则为空字符串",
    "severity_confidence": 0.0
}
要求：
1. 仅当信息足以支撑时再给出明确等级，否则返回 UNKNOWN。
2. 如果描述只体现普通事故、局部影响、少量伤员，优先考虑 `一般` 或 `UNKNOWN`，不要夸大等级。
3. 不要依赖知识图谱候选等级。
4. 只能输出合法 JSON。
"""

STRATEGY_GENERATION_PROMPT = """你是交通应急处置指挥助手。请根据输入的事件信息、知识图谱逻辑链路和法规证据，生成可执行的单方案处置策略。
只输出 JSON，不要输出解释。
字段要求：
{
    "focus": "一句话概括处置焦点",
    "steps": ["按执行顺序给出处置步骤，每步一句中文"],
    "required_resources": ["需要调用的资源名称"],
    "legal_references": ["引用的法规/预案文件名"]
}
要求：
1. 步骤必须体现先控险、再救援、再恢复交通秩序的逻辑。
2. 优先使用知识图谱给出的动作、主体和资源，不得凭空编造明显不存在的实体。
3. 若存在伤员，方案必须体现医疗救治；若存在起火，必须体现灭火处置；若存在泄漏，必须体现围堵或封堵。
4. 只输出合法 JSON。
"""

STRATEGY_REVISION_PROMPT = """你是交通应急处置修订助手。请根据原方案和审查意见，输出修订后的完整单方案。
只输出 JSON，不要输出解释。
字段要求与原方案一致：
{
    "focus": "一句话概括处置焦点",
    "steps": ["按执行顺序给出处置步骤，每步一句中文"],
    "required_resources": ["需要调用的资源名称"],
    "legal_references": ["引用的法规/预案文件名"]
}
要求：
1. 必须补齐审查指出的缺失动作。
2. 保留原方案中合理的步骤，不要无谓删减。
3. 只输出合法 JSON。
"""

STRATEGY_REVIEW_PROMPT = """你是交通应急处置审查助手。请根据事件信息、图谱约束、法规证据和当前方案进行审查，只输出 JSON。
字段要求：
{
    "status": "APPROVED 或 REJECTED",
    "reason": "审查结论说明",
    "violated_constraints": ["违反的图谱约束或要求"],
    "missing_actions": ["缺失的关键动作"],
    "risk_notes": ["风险提示"],
    "failure_type": "若拒绝则填写 failure_type，否则为空字符串"
}
要求：
1. 仅基于输入信息判断，不要编造不存在的动作或约束。
2. 若存在明确关键风险但方案未覆盖，可拒绝并指出缺失动作。
3. 若信息不足但方案基本可执行，可批准并在 risk_notes 中提示人工复核。
4. 只输出合法 JSON。
"""

EVENT_MATCH_PROMPT = """你是交通突发事件节点规范化匹配助手。请从给定候选事件节点中选择最贴近用户描述的规范节点。
只输出 JSON。
字段要求：
{
    "matches": [
        {
            "surface_form": "用户原始事件表达",
            "entity_type": "突发事件",
            "normalized_name": "候选表中的规范节点名，若无法匹配则为空字符串",
            "node_id": "候选表中的节点 ID，若无法匹配则为空字符串",
            "match_confidence": 0.0,
            "match_reason": "简短说明匹配原因"
        }
    ]
}
要求：
1. 只能在提供的候选节点中选择，不要编造新节点。
2. 重点参考事故语义、场景特征、用户原始描述以及候选中的 aliases。
3. 若同时存在上位概括节点和更具体的场景节点，优先选择信息量更高、更贴近事故机制或场景特征的具体节点。
4. 若没有可靠匹配，返回空数组。
5. 最多返回 3 个匹配，按置信度降序排列。
6. 只输出合法 JSON。
"""

def _is_enabled(env_name: str) -> bool:
    return str(os.getenv(env_name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _short_text(value: object, limit: int = 240) -> str:
    text = str(value or "").strip().replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _debug_log(event: str, **payload: object) -> None:
    if not _is_enabled("TRAFFIC_DEBUG"):
        return

    safe_payload = {key: value for key, value in payload.items()}
    logger.info("%s | %s", event, json.dumps(safe_payload, ensure_ascii=False, default=str))


def _extract_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
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


def _clean_string_list(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        cleaned = str(value).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _compact_json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _char_ngrams(text: str, size: int = 2) -> set[str]:
    cleaned = re.sub(r"\s+", "", str(text or "").strip())
    if not cleaned:
        return set()
    if len(cleaned) <= size:
        return {cleaned}
    return {cleaned[index:index + size] for index in range(len(cleaned) - size + 1)}


def _jaccard_similarity(left: str, right: str) -> float:
    left_ngrams = _char_ngrams(left)
    right_ngrams = _char_ngrams(right)
    if not left_ngrams or not right_ngrams:
        return 0.0
    union = left_ngrams | right_ngrams
    if not union:
        return 0.0
    return len(left_ngrams & right_ngrams) / len(union)


def _parse_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _fallback_incident_surface(text: str) -> str:
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        return ""

    first_clause = re.split(r"[，。；,;\n]", cleaned_text, maxsplit=1)[0].strip()
    return first_clause[:40]


def _fallback_extract_from_text(text: str) -> dict:
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        return {}

    fallback_payload = {
        "incident_type": _fallback_incident_surface(cleaned_text),
        "weather": "",
        "hazards": [],
        "vehicles": [],
        "location_features": [],
        "casualties": {
            "deaths": None,
            "injuries": None,
            "missing": None,
        },
        "extract_confidence": 0.1 if cleaned_text else 0.0,
    }
    return fallback_payload


def _read_timeout(env_name: str, default_value: float) -> float:
    timeout_text = os.getenv(env_name, str(default_value))
    try:
        return max(5.0, float(timeout_text))
    except ValueError:
        return default_value


def _normalize_severity_label(value: object) -> str:
    cleaned = str(value or "").strip()
    severity_map = {
        "特别重大": ["特别重大", "Ⅰ级", "一级"],
        "重大": ["重大", "Ⅱ级", "二级"],
        "较大": ["较大", "Ⅲ级", "三级"],
        "一般": ["一般", "Ⅳ级", "四级"],
        "UNKNOWN": ["UNKNOWN", "未知", "无法判断", "不确定"],
    }
    for label, keywords in severity_map.items():
        if cleaned == label or any(keyword in cleaned for keyword in keywords):
            return label
    return "UNKNOWN"


def _chat_json(
    model: str,
    prompt: str,
    user_content: str,
    timeout_seconds: float,
    image_base64: str | None = None,
    image_mime_type: str | None = None,
) -> dict:
    try:
        response = generate_json_response(
            model=model,
            system_prompt=prompt,
            user_content=user_content,
            timeout_seconds=timeout_seconds,
            image_base64=image_base64,
            image_mime_type=image_mime_type,
        )
    except Exception as exc:
        _debug_log(
            "llm_chat_exception",
            model=model,
            timeout_seconds=timeout_seconds,
            error_type=type(exc).__name__,
            error=str(exc),
            user_content_preview=_short_text(user_content),
        )
        return {}

    response_payload = response.get("response_payload", {}) if isinstance(response, dict) else {}
    content = response.get("content", "") if isinstance(response, dict) else ""
    parsed = _extract_json_object(content)

    candidates = response_payload.get("candidates", []) if isinstance(response_payload, dict) else []
    candidate = candidates[0] if candidates else {}
    content_node = candidate.get("content", {}) if isinstance(candidate, dict) else {}
    content_parts = content_node.get("parts", []) if isinstance(content_node, dict) else []

    debug_payload = {
        "model": model,
        "timeout_seconds": timeout_seconds,
        "provider": response.get("provider", "unknown") if isinstance(response, dict) else "unknown",
        "response_type": response.get("response_type", type(response).__name__) if isinstance(response, dict) else type(response).__name__,
        "response_keys": sorted(response_payload.keys()) if isinstance(response_payload, dict) else [],
        "candidate_keys": sorted(candidate.keys()) if isinstance(candidate, dict) else [],
        "content_part_count": len(content_parts) if isinstance(content_parts, list) else 0,
        "content_length": len(str(content or "")),
        "parsed": bool(parsed),
        "parsed_keys": sorted(parsed.keys()) if isinstance(parsed, dict) else [],
        "user_content_preview": _short_text(user_content),
    }
    if _is_enabled("TRAFFIC_LOG_RAW_RESPONSE"):
        debug_payload["content_preview"] = _short_text(content, limit=800)
    _debug_log("llm_chat_result", **debug_payload)

    return parsed


class RetrievalLogicAgent:
    """最小版检索智能体：负责统一调用 Neo4j 与 ChromaDB。"""

    def __init__(self, service: DualRetrievalService | None = None):
        self.name = "检索与逻辑专家"
        self.service = service or DualRetrievalService()

    def retrieve(self, incident: IncidentInput, entities: ExtractedEntities) -> RetrievalContext:
        return self.service.retrieve(incident, entities)

    def close(self) -> None:
        self.service.close()


class EntityMatcherAgent:
    """将抽取到的事件表达对齐到突发事件节点表。"""

    def __init__(
        self,
        matcher: Callable[[str], dict] | None = None,
    ):
        self.name = "实体匹配专家"
        self.matcher = matcher or self._match_events_with_llm
        self.alias_store = EventAliasStore()
        self.event_catalog = self.alias_store.build_matcher_index()
        self.event_catalog_by_id = {item.get("node_id", ""): item for item in self.event_catalog}
        self.shortlist_limit = max(3, int(os.getenv("MATCHER_SHORTLIST_LIMIT", str(DEFAULT_MATCHER_SHORTLIST_LIMIT))))

    @staticmethod
    def _get_candidate_texts(item: dict) -> list[str]:
        name = str(item.get("name", "")).strip()
        aliases = [str(alias).strip() for alias in (item.get("aliases", []) or []) if str(alias).strip()]
        return [text for text in [name, *aliases] if text]

    @classmethod
    def _count_literal_hits(cls, cue_texts: list[str], candidate_texts: list[str]) -> int:
        hit_count = 0
        for cue in cue_texts:
            cleaned_cue = str(cue or "").strip()
            if not cleaned_cue:
                continue
            if any(cleaned_cue in candidate or candidate in cleaned_cue for candidate in candidate_texts if candidate):
                hit_count += 1
        return hit_count

    def _build_catalog_profile(self, incident: IncidentInput, entities: ExtractedEntities, item: dict) -> dict:
        incident_query = entities.incident_type_raw or entities.incident_type or ""
        candidate_texts = self._get_candidate_texts(item)
        raw_text = str(incident.raw_text or "").strip()
        cue_texts = [
            entities.weather,
            *entities.hazards,
            *entities.vehicles,
            *entities.location_features,
        ]

        incident_score = max((_jaccard_similarity(incident_query, candidate) for candidate in candidate_texts), default=0.0)
        raw_score = max((_jaccard_similarity(raw_text, candidate) for candidate in candidate_texts), default=0.0)

        cue_similarities: list[float] = []
        for cue in cue_texts:
            cleaned_cue = str(cue or "").strip()
            if not cleaned_cue:
                continue
            cue_similarities.append(
                max((_jaccard_similarity(cleaned_cue, candidate) for candidate in candidate_texts), default=0.0)
            )

        cue_avg_score = sum(cue_similarities) / len(cue_similarities) if cue_similarities else 0.0
        cue_max_score = max(cue_similarities, default=0.0)
        incident_literal_hit_count = self._count_literal_hits([incident_query], candidate_texts)
        scene_literal_hit_count = self._count_literal_hits(cue_texts, candidate_texts)
        literal_hit_count = incident_literal_hit_count + scene_literal_hit_count
        literal_bonus = min((0.04 * incident_literal_hit_count) + (0.06 * scene_literal_hit_count), 0.22)

        node_id = str(item.get("node_id", "")).strip()
        candidate_name = str(item.get("name", "")).strip()
        hierarchy_depth = self.alias_store.get_hierarchy_depth(node_id)
        child_count = len(self.alias_store.get_child_ids(node_id))
        is_generic_name = any(keyword in candidate_name for keyword in ["突发事件", "灾害", "事件"])
        is_generic_node = is_generic_name or child_count > 0

        severity_rank = {
            "UNKNOWN": 0,
            "一般": 1,
            "较大": 2,
            "重大": 3,
            "特别重大": 4,
        }
        candidate_severity_rank = 0
        if any(keyword in candidate_name for keyword in ["特别重大", "重特大"]):
            candidate_severity_rank = 4
        elif any(keyword in candidate_name for keyword in ["重大", "恶性"]):
            candidate_severity_rank = 3
        elif "较大" in candidate_name:
            candidate_severity_rank = 2

        generic_penalty = 0.0
        if is_generic_node and scene_literal_hit_count == 0 and cue_max_score < 0.35:
            generic_penalty = 0.07

        severity_penalty = 0.0
        entity_severity_rank = severity_rank.get(entities.severity, 0)
        if candidate_severity_rank and entity_severity_rank:
            if candidate_severity_rank > entity_severity_rank:
                severity_penalty = min(0.12 * (candidate_severity_rank - entity_severity_rank), 0.24)
        elif candidate_severity_rank and entities.severity == "UNKNOWN":
            severity_penalty = 0.08

        specificity_bonus = 0.0
        if not is_generic_node:
            specificity_bonus = min(0.03 * hierarchy_depth, 0.12)

        dual_signal_bonus = 0.0
        if incident_literal_hit_count > 0 and scene_literal_hit_count > 0:
            dual_signal_bonus = 0.08

        semantic_score = (
            (incident_score * 0.50)
            + (cue_avg_score * 0.15)
            + (cue_max_score * 0.10)
            + (raw_score * 0.15)
            + literal_bonus
            + specificity_bonus
            + dual_signal_bonus
            - generic_penalty
            - severity_penalty
        )

        return {
            "node_id": node_id,
            "candidate_name": candidate_name,
            "incident_score": incident_score,
            "raw_score": raw_score,
            "cue_avg_score": cue_avg_score,
            "cue_max_score": cue_max_score,
            "incident_literal_hit_count": incident_literal_hit_count,
            "scene_literal_hit_count": scene_literal_hit_count,
            "literal_hit_count": literal_hit_count,
            "hierarchy_depth": hierarchy_depth,
            "child_count": child_count,
            "is_generic_node": is_generic_node,
            "candidate_severity_rank": candidate_severity_rank,
            "severity_penalty": severity_penalty,
            "dual_signal_bonus": dual_signal_bonus,
            "semantic_score": max(0.0, min(semantic_score, 1.0)),
        }

    def _score_catalog_item(self, incident: IncidentInput, entities: ExtractedEntities, item: dict) -> float:
        profile = self._build_catalog_profile(incident, entities, item)
        return float(profile.get("semantic_score", 0.0))

    def _rank_event_catalog(self, incident: IncidentInput, entities: ExtractedEntities) -> list[tuple[float, dict]]:
        scored_items: list[tuple[float, dict]] = []
        for item in self.event_catalog:
            score = self._score_catalog_item(incident, entities, item)
            scored_items.append((score, item))

        scored_items.sort(key=lambda pair: (-pair[0], pair[1].get("name", "")))
        return scored_items

    def _build_candidate_shortlist(self, incident: IncidentInput, entities: ExtractedEntities) -> list[dict]:
        if len(self.event_catalog) <= self.shortlist_limit:
            return self.event_catalog

        scored_items = self._rank_event_catalog(incident, entities)
        shortlist = [item for _, item in scored_items[: self.shortlist_limit]]
        return shortlist or self.event_catalog[: self.shortlist_limit]

    def _rerank_matches(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        matches: list[MatchedNode],
    ) -> list[MatchedNode]:
        if len(matches) <= 1:
            return matches

        profiles_by_id = {
            match.node_id: self._build_catalog_profile(incident, entities, self.event_catalog_by_id.get(match.node_id, {}))
            for match in matches
        }

        def hierarchy_adjustment(match: MatchedNode) -> float:
            current_profile = profiles_by_id.get(match.node_id, {})
            current_semantic = float(current_profile.get("semantic_score", 0.0))
            current_incident = float(current_profile.get("incident_score", 0.0))
            current_depth = int(current_profile.get("hierarchy_depth", 0) or 0)
            adjustment = 0.0

            for other in matches:
                if other.node_id == match.node_id:
                    continue

                other_profile = profiles_by_id.get(other.node_id, {})
                other_semantic = float(other_profile.get("semantic_score", 0.0))
                other_incident = float(other_profile.get("incident_score", 0.0))
                other_depth = int(other_profile.get("hierarchy_depth", 0) or 0)
                other_is_generic = bool(other_profile.get("is_generic_node", False))
                current_is_generic = bool(current_profile.get("is_generic_node", False))

                if self.alias_store.is_ancestor(match.node_id, other.node_id):
                    if current_is_generic and other_semantic >= current_semantic - 0.12 and other_incident >= current_incident - 0.08:
                        depth_gap = max(other_depth - current_depth, 1)
                        adjustment -= min(0.10 + (0.03 * depth_gap), 0.18)

                if self.alias_store.is_ancestor(other.node_id, match.node_id):
                    if other_is_generic and current_semantic >= other_semantic - 0.12 and current_incident >= other_incident - 0.08:
                        depth_gap = max(current_depth - other_depth, 1)
                        adjustment += min(0.10 + (0.03 * depth_gap), 0.18)

            return adjustment

        def combined_score(match: MatchedNode) -> tuple[float, float, float, str]:
            catalog_item = self.event_catalog_by_id.get(match.node_id, {})
            profile = profiles_by_id.get(match.node_id) or self._build_catalog_profile(incident, entities, catalog_item)
            semantic_score = float(profile.get("semantic_score", 0.0))
            incident_score = float(profile.get("incident_score", 0.0))
            blended_score = (match.match_confidence * 0.5) + (semantic_score * 0.35) + (incident_score * 0.15)
            blended_score += hierarchy_adjustment(match)
            return (blended_score, semantic_score, incident_score, match.normalized_name)

        reranked = sorted(
            matches,
            key=lambda match: (
                -combined_score(match)[0],
                -combined_score(match)[1],
                -combined_score(match)[2],
                match.normalized_name,
            ),
        )
        return reranked

    def _build_matcher_payload(self, incident: IncidentInput, entities: ExtractedEntities) -> str:
        candidate_shortlist = self._build_candidate_shortlist(incident, entities)
        payload = {
            "raw_text": incident.raw_text,
            "extracted_incident_type": entities.incident_type_raw or entities.incident_type,
            "severity": entities.severity,
            "hazards": entities.hazards,
            "weather": entities.weather,
            "location_features": entities.location_features,
            "event_candidates": candidate_shortlist,
        }
        _debug_log(
            "entity_match_payload",
            catalog_size=len(self.event_catalog),
            candidate_size=len(candidate_shortlist),
            alias_total=sum(len(item.get("aliases", []) or []) for item in candidate_shortlist),
            extracted_incident_type=entities.incident_type_raw or entities.incident_type,
        )
        return _compact_json_dumps(payload)

    @staticmethod
    def _match_events_with_llm(user_content: str) -> dict:
        if not user_content.strip():
            return {}

        model = os.getenv("MATCHER_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("MATCHER_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        return _chat_json(
            model=model,
            prompt=EVENT_MATCH_PROMPT,
            user_content=user_content,
            timeout_seconds=timeout_seconds,
        )

    def match(self, incident: IncidentInput, entities: ExtractedEntities) -> ExtractedEntities:
        payload = self.matcher(self._build_matcher_payload(incident, entities)) or {}
        matches: list[MatchedNode] = []
        for item in payload.get("matches", []) or []:
            normalized_name = str(item.get("normalized_name", "")).strip()
            node_id = str(item.get("node_id", "")).strip()
            if not normalized_name or not node_id:
                continue
            try:
                confidence = max(0.0, min(float(item.get("match_confidence", 0.0) or 0.0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            matches.append(
                MatchedNode(
                    surface_form=str(item.get("surface_form", entities.incident_type)).strip(),
                    entity_type=str(item.get("entity_type", "突发事件")).strip() or "突发事件",
                    normalized_name=normalized_name,
                    node_id=node_id,
                    match_confidence=confidence,
                    match_reason=str(item.get("match_reason", "")).strip(),
                )
            )

        if not matches:
            _debug_log(
                "entity_match_empty",
                incident_type_raw=entities.incident_type_raw,
                incident_type=entities.incident_type,
                catalog_size=len(self.event_catalog),
            )
            return entities

        matches = self._rerank_matches(incident, entities, matches)

        primary_event = matches[0].normalized_name
        _debug_log(
            "entity_match_success",
            incident_type_raw=entities.incident_type_raw,
            primary_event=primary_event,
            match_count=len(matches),
            top_confidence=matches[0].match_confidence,
        )
        return replace(entities, incident_type=primary_event, matched_events=matches)


class DispatcherAgent:
    """最小版接警解析智能体：负责开放式抽取事件与环境信息。"""

    def __init__(
        self,
        image_analyzer: Callable[[bytes], dict] | None = None,
        text_analyzer: Callable[[str], dict] | None = None,
        severity_analyzer: Callable[[str], dict] | None = None,
    ):
        self.name = "接警解析专家"
        self.image_analyzer = image_analyzer or self._analyze_image_with_llm
        self.text_analyzer = text_analyzer or self._analyze_text_with_llm
        self.severity_analyzer = severity_analyzer or self._classify_severity_with_llm

    @staticmethod
    def _merge_values(primary: list[str], secondary: list[str]) -> list[str]:
        result: list[str] = []
        for value in primary + secondary:
            cleaned = value.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
        return result

    @staticmethod
    def _analyze_image_with_llm(image_bytes: bytes) -> dict:
        if not image_bytes:
            return {}

        model = os.getenv("DISPATCHER_VISION_MODEL", get_default_model())
        timeout_seconds = _read_timeout("DISPATCHER_VISION_TIMEOUT_SECONDS", DEFAULT_VISION_TIMEOUT_SECONDS)

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = _chat_json(
            model=model,
            prompt=IMAGE_ANALYSIS_PROMPT,
            user_content="请分析这张交通事故现场图片，并按要求输出 JSON。",
            timeout_seconds=timeout_seconds,
            image_base64=image_base64,
        )
        if not payload:
            return {}

        confidence = payload.get("confidence", 0.0)
        try:
            normalized_confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            normalized_confidence = 0.0

        return {
            "incident_type": str(payload.get("incident_type", "")).strip(),
            "weather": str(payload.get("weather", "")).strip(),
            "hazards": _clean_string_list(payload.get("hazards")),
            "vehicles": _clean_string_list(payload.get("vehicles")),
            "location_features": _clean_string_list(payload.get("location_features")),
            "casualties": {
                "deaths": _parse_optional_int((payload.get("casualties") or {}).get("deaths")),
                "injuries": _parse_optional_int((payload.get("casualties") or {}).get("injuries")),
                "missing": _parse_optional_int((payload.get("casualties") or {}).get("missing")),
            },
            "evidence": _clean_string_list(payload.get("evidence")),
            "confidence": normalized_confidence,
        }

    @staticmethod
    def _analyze_text_with_llm(text: str) -> dict:
        if not text.strip():
            return {}

        model = os.getenv("DISPATCHER_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("DISPATCHER_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        payload = _chat_json(
            model=model,
            prompt=TEXT_ANALYSIS_PROMPT,
            user_content=f"请解析以下交通事件描述：\n{text}",
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            payload = _chat_json(
                model=model,
                prompt=TEXT_ANALYSIS_RETRY_PROMPT,
                user_content=text,
                timeout_seconds=timeout_seconds,
            )
            if payload:
                _debug_log("dispatcher_text_retry_success", text_preview=_short_text(text))
        if not payload:
            payload = _fallback_extract_from_text(text)
            if payload:
                _debug_log("dispatcher_text_fallback_used", text_preview=_short_text(text), payload=payload)
        if not payload:
            return {}

        confidence = payload.get("extract_confidence", 0.0)
        try:
            normalized_confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            normalized_confidence = 0.0

        return {
            "incident_type": str(payload.get("incident_type", "")).strip(),
            "weather": str(payload.get("weather", "")).strip(),
            "hazards": _clean_string_list(payload.get("hazards")),
            "vehicles": _clean_string_list(payload.get("vehicles")),
            "location_features": _clean_string_list(payload.get("location_features")),
            "casualties": {
                "deaths": _parse_optional_int((payload.get("casualties") or {}).get("deaths")),
                "injuries": _parse_optional_int((payload.get("casualties") or {}).get("injuries")),
                "missing": _parse_optional_int((payload.get("casualties") or {}).get("missing")),
            },
            "extract_confidence": normalized_confidence,
        }

    @staticmethod
    def _classify_severity_with_llm(user_content: str) -> dict:
        if not user_content.strip():
            return {}

        model = os.getenv("SEVERITY_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("SEVERITY_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        payload = _chat_json(
            model=model,
            prompt=SEVERITY_CLASSIFICATION_PROMPT,
            user_content=user_content,
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            return {}

        try:
            confidence = max(0.0, min(float(payload.get("severity_confidence", 0.0) or 0.0), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0

        return {
            "severity": _normalize_severity_label(payload.get("severity", "UNKNOWN")),
            "severity_reason": str(payload.get("severity_reason", "")).strip(),
            "severity_confidence": confidence,
        }

    def extract(self, incident: IncidentInput) -> ExtractedEntities:
        text = incident.raw_text.strip()

        llm_text_findings = self.text_analyzer(text) if text else {}

        incident_type = str(llm_text_findings.get("incident_type") or "").strip()
        weather = str(llm_text_findings.get("weather") or "").strip()
        hazards = _clean_string_list(llm_text_findings.get("hazards"))
        vehicles = _clean_string_list(llm_text_findings.get("vehicles"))
        location_features = _clean_string_list(llm_text_findings.get("location_features"))

        llm_casualties = llm_text_findings.get("casualties") or {}
        deaths = _parse_optional_int(llm_casualties.get("deaths"))
        injuries = _parse_optional_int(llm_casualties.get("injuries"))
        missing = _parse_optional_int(llm_casualties.get("missing"))

        image_findings: dict = {}
        evidence_from_image: list[str] = []
        if incident.image_bytes:
            image_findings = self.image_analyzer(incident.image_bytes) or {}
            evidence_from_image = _clean_string_list(image_findings.get("evidence"))
            if not evidence_from_image:
                evidence_from_image.append("已提供现场图片，待多模态分析")

        image_casualties = image_findings.get("casualties") or {}
        if deaths is None:
            deaths = _parse_optional_int(image_casualties.get("deaths"))
        if injuries is None:
            injuries = _parse_optional_int(image_casualties.get("injuries"))
        if missing is None:
            missing = _parse_optional_int(image_casualties.get("missing"))

        casualty_estimate = CasualtyEstimate(
            deaths=deaths,
            injuries=injuries,
            missing=missing,
            unknown=all(value is None for value in [deaths, injuries, missing]),
        )

        if not incident_type:
            incident_type = str(image_findings.get("incident_type", "")).strip()
        if not weather:
            weather = str(image_findings.get("weather", "")).strip()

        hazards = self._merge_values(hazards, _clean_string_list(image_findings.get("hazards")))
        vehicles = self._merge_values(vehicles, _clean_string_list(image_findings.get("vehicles")))
        location_features = self._merge_values(location_features, _clean_string_list(image_findings.get("location_features")))

        severity_payload = self.severity_analyzer(
            json.dumps(
                {
                    "raw_text": incident.raw_text,
                    "incident_type": incident_type,
                    "weather": weather,
                    "hazards": hazards,
                    "vehicles": vehicles,
                    "location_features": location_features,
                    "casualty_estimate": {
                        "deaths": casualty_estimate.deaths,
                        "injuries": casualty_estimate.injuries,
                        "missing": casualty_estimate.missing,
                        "unknown": casualty_estimate.unknown,
                    },
                    "evidence_from_image": evidence_from_image,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        severity = _normalize_severity_label(severity_payload.get("severity", "UNKNOWN"))
        severity_reason = str(severity_payload.get("severity_reason", "")).strip()
        try:
            severity_confidence = max(0.0, min(float(severity_payload.get("severity_confidence", 0.0) or 0.0), 1.0))
        except (TypeError, ValueError):
            severity_confidence = 0.0

        extracted_fields = [incident_type, weather] + hazards + vehicles + location_features
        confidence = max(
            float(llm_text_findings.get("extract_confidence", 0.0) or 0.0),
            0.0,
        )
        confidence += min(0.1 * len([item for item in extracted_fields if item]), 0.55)
        if not casualty_estimate.unknown:
            confidence += 0.1
        if incident.image_bytes:
            confidence += 0.05
            confidence += min(float(image_findings.get("confidence", 0.0)) * 0.15, 0.15)

        result = ExtractedEntities(
            incident_type_raw=incident_type,
            incident_type=incident_type,
            severity=severity,
            severity_reason=severity_reason,
            severity_confidence=severity_confidence,
            weather=weather,
            hazards=hazards,
            vehicles=vehicles,
            location_features=location_features,
            casualty_estimate=casualty_estimate,
            evidence_from_image=evidence_from_image,
            extract_confidence=min(confidence, 0.95),
        )
        _debug_log(
            "dispatcher_extract_result",
            incident_type_raw=result.incident_type_raw,
            severity=result.severity,
            weather=result.weather,
            hazards=result.hazards,
            vehicles=result.vehicles,
            location_features=result.location_features,
            casualty_unknown=result.casualty_estimate.unknown,
            extract_confidence=result.extract_confidence,
        )
        return result


class CommanderAgent:
    """最小版指挥调度智能体：将检索结果整合为单方案草案。"""

    def __init__(
        self,
        generator: Callable[[IncidentInput, ExtractedEntities, RetrievalContext], StrategyDraft | None] | None = None,
        reviser: Callable[[IncidentInput, ExtractedEntities, RetrievalContext, StrategyDraft, ReviewResult], StrategyDraft | None] | None = None,
    ):
        self.name = "指挥调度专家"
        self.generator = generator or self._generate_with_llm
        self.reviser = reviser or self._revise_with_llm

    @staticmethod
    def _unique_keep_order(items: list[str]) -> list[str]:
        result: list[str] = []
        for item in items:
            cleaned = item.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
        return result

    @classmethod
    def _build_fallback_draft(
        cls,
        entities: ExtractedEntities,
        context: RetrievalContext,
        review: ReviewResult | None = None,
    ) -> StrategyDraft:
        steps: list[str] = []

        action_targets = cls._unique_keep_order(
            [
                item.target_node
                for item in context.neo4j_constraints
                if item.relation == "TRIGGERS" and item.target_node
            ]
        )
        for action_name in action_targets[:4]:
            candidate_step = f"按预案立即执行“{action_name}”，并同步协调相关部门落实。"
            if candidate_step not in steps:
                steps.append(candidate_step)

        if review:
            if review.failure_type not in {"llm_review_failed", "empty_strategy"}:
                for action in review.missing_actions[:3]:
                    candidate_step = f"补充落实“{action}”相关处置动作，确保方案完整可执行。"
                    if candidate_step not in steps:
                        steps.append(candidate_step)

        resources = cls._unique_keep_order(
            [
                item.target_node
                for item in context.neo4j_constraints
                if item.relation == "REQUIRES" and item.target_node
            ]
        )[:6]

        references = cls._unique_keep_order([item.file_name for item in context.chroma_evidence])
        if not steps:
            steps.append("根据知识图谱返回的处置链条和法规证据进行人工复核，并组织相关部门启动先期处置。")

        return StrategyDraft(
            focus=entities.incident_type or "交通事故应急处置",
            steps=steps,
            required_resources=resources,
            legal_references=references,
        )

    def generate(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
    ) -> StrategyDraft:
        llm_draft = self.generator(incident, entities, context)
        if llm_draft:
            _debug_log(
                "commander_generate_success",
                focus=llm_draft.focus,
                step_count=len(llm_draft.steps),
                resource_count=len(llm_draft.required_resources),
            )
            return llm_draft

        _debug_log(
            "commander_generate_empty",
            incident_type=entities.incident_type,
            evidence_count=len(context.chroma_evidence),
            constraint_count=len(context.neo4j_constraints),
        )
        fallback_draft = self._build_fallback_draft(entities, context)
        _debug_log(
            "commander_generate_fallback",
            focus=fallback_draft.focus,
            step_count=len(fallback_draft.steps),
            resource_count=len(fallback_draft.required_resources),
        )
        return fallback_draft

    @staticmethod
    def _format_context_payload(incident: IncidentInput, entities: ExtractedEntities, context: RetrievalContext) -> str:
        payload = {
            "incident": {
                "raw_text": incident.raw_text,
            },
            "entities": {
                "incident_type": entities.incident_type,
                "weather": entities.weather,
                "hazards": entities.hazards,
                "vehicles": entities.vehicles,
                "location_features": entities.location_features,
                "casualty_estimate": {
                    "deaths": entities.casualty_estimate.deaths,
                    "injuries": entities.casualty_estimate.injuries,
                    "missing": entities.casualty_estimate.missing,
                    "unknown": entities.casualty_estimate.unknown,
                },
                "evidence_from_image": entities.evidence_from_image,
            },
            "retrieval": {
                "severity": context.severity,
                "severity_source": context.severity_source,
                "neo4j_constraints": [
                    {
                        "rule": item.rule,
                        "source_node": item.source_node,
                        "relation": item.relation,
                        "target_node": item.target_node,
                    }
                    for item in context.neo4j_constraints[:8]
                ],
                "chroma_evidence": [
                    {
                        "file_name": item.file_name,
                        "chunk_id": item.chunk_id,
                        "distance": item.distance,
                        "content": _short_text(item.content, limit=180),
                    }
                    for item in context.chroma_evidence[:3]
                ],
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @classmethod
    def _generate_with_llm(
        cls,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
    ) -> StrategyDraft | None:
        model = os.getenv("COMMANDER_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("COMMANDER_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        payload = _chat_json(
            model=model,
            prompt=STRATEGY_GENERATION_PROMPT,
            user_content=cls._format_context_payload(incident, entities, context),
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            return None

        focus = str(payload.get("focus", "")).strip()
        steps = _clean_string_list(payload.get("steps"))
        resources = _clean_string_list(payload.get("required_resources"))
        references = _clean_string_list(payload.get("legal_references"))
        if not steps:
            return None
        if not references and context.chroma_evidence:
            references = cls._unique_keep_order([item.file_name for item in context.chroma_evidence])

        return StrategyDraft(
            focus=focus or entities.incident_type or "交通事故应急处置",
            steps=steps,
            required_resources=resources,
            legal_references=references,
        )

    def revise(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
        draft: StrategyDraft,
        review: ReviewResult,
    ) -> StrategyDraft:
        revised_draft = self.reviser(incident, entities, context, draft, review)
        if revised_draft:
            _debug_log(
                "commander_revise_success",
                focus=revised_draft.focus,
                step_count=len(revised_draft.steps),
            )
            return revised_draft

        _debug_log(
            "commander_revise_empty",
            review_status=review.status,
            failure_type=review.failure_type,
        )
        fallback_draft = self._build_fallback_draft(entities, context, review)
        _debug_log(
            "commander_revise_fallback",
            focus=fallback_draft.focus,
            step_count=len(fallback_draft.steps),
        )
        return fallback_draft

    def _revise_with_llm(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
        draft: StrategyDraft,
        review: ReviewResult,
    ) -> StrategyDraft | None:
        model = os.getenv("COMMANDER_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("COMMANDER_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        user_content = json.dumps(
            {
                "context": json.loads(self._format_context_payload(incident, entities, context)),
                "current_draft": {
                    "focus": draft.focus,
                    "steps": draft.steps,
                    "required_resources": draft.required_resources,
                    "legal_references": draft.legal_references,
                },
                "review": {
                    "status": review.status,
                    "reason": review.reason,
                    "missing_actions": review.missing_actions,
                    "risk_notes": review.risk_notes,
                    "failure_type": review.failure_type,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        payload = _chat_json(
            model=model,
            prompt=STRATEGY_REVISION_PROMPT,
            user_content=user_content,
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            return None

        steps = _clean_string_list(payload.get("steps"))
        if not steps:
            return None

        references = _clean_string_list(payload.get("legal_references"))
        if not references and context.chroma_evidence:
            references = self._unique_keep_order([item.file_name for item in context.chroma_evidence])

        return StrategyDraft(
            focus=str(payload.get("focus", "")).strip() or draft.focus,
            steps=steps,
            required_resources=_clean_string_list(payload.get("required_resources")),
            legal_references=references,
        )


class EvaluatorAgent:
    """最小版推演评估智能体：基于 LLM 审查单方案草案。"""

    def __init__(
        self,
        reviewer: Callable[[IncidentInput, ExtractedEntities, RetrievalContext, StrategyDraft, int], ReviewResult | None] | None = None,
    ):
        self.name = "推演评估专家"
        self.reviewer = reviewer or self._review_with_llm

    def review(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
        draft: StrategyDraft,
        retry_count: int = 0,
    ) -> ReviewResult:
        if not draft.steps:
            _debug_log(
                "evaluator_review_short_circuit",
                retry_count=retry_count,
                reason="empty_strategy",
            )
            return ReviewResult(
                status="REJECTED",
                reason="当前方案为空，无法进入审查通过",
                violated_constraints=[],
                missing_actions=["未生成有效处置步骤"],
                risk_notes=["请先修复生成阶段或人工补充处置方案"],
                retry_count=retry_count,
                failure_type="empty_strategy",
            )

        llm_review = self.reviewer(incident, entities, context, draft, retry_count)
        if llm_review:
            _debug_log(
                "evaluator_review_success",
                status=llm_review.status,
                retry_count=retry_count,
                missing_actions=llm_review.missing_actions,
            )
            return llm_review

        _debug_log(
            "evaluator_review_empty",
            retry_count=retry_count,
            draft_step_count=len(draft.steps),
        )
        return ReviewResult(
            status="REJECTED",
            reason="LLM 审查未返回有效结果，当前不使用本地硬编码规则替代审查",
            violated_constraints=[],
            missing_actions=["LLM 审查失败，需人工复核当前方案"],
            risk_notes=["请结合知识图谱逻辑链条与向量证据进行人工审核"],
            retry_count=retry_count,
            failure_type="llm_review_failed",
        )

    def _review_with_llm(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
        draft: StrategyDraft,
        retry_count: int,
    ) -> ReviewResult | None:
        model = os.getenv("EVALUATOR_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("EVALUATOR_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        payload_body = {
            "incident": {"raw_text": incident.raw_text},
            "entities": {
                "incident_type": entities.incident_type,
                "weather": entities.weather,
                "hazards": entities.hazards,
                "vehicles": entities.vehicles,
                "location_features": entities.location_features,
                "casualty_estimate": {
                    "deaths": entities.casualty_estimate.deaths,
                    "injuries": entities.casualty_estimate.injuries,
                    "missing": entities.casualty_estimate.missing,
                    "unknown": entities.casualty_estimate.unknown,
                },
            },
            "retrieval": {
                "severity": context.severity,
                "constraints": [
                    f"{item.source_node}-{item.relation}-{item.target_node}"
                    for item in context.neo4j_constraints[:6]
                ],
                "evidence": [
                    {
                        "file_name": item.file_name,
                        "content": _short_text(item.content, limit=120),
                    }
                    for item in context.chroma_evidence[:2]
                ],
            },
            "draft": {
                "focus": draft.focus,
                "steps": draft.steps,
                "required_resources": draft.required_resources,
                "legal_references": draft.legal_references,
            },
        }
        payload = _chat_json(
            model=model,
            prompt=STRATEGY_REVIEW_PROMPT,
            user_content=_compact_json_dumps(payload_body),
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            return None

        status = str(payload.get("status", "")).strip().upper()
        if status not in {"APPROVED", "REJECTED"}:
            return None

        return ReviewResult(
            status=status,
            reason=str(payload.get("reason", "")).strip() or "LLM 审查已完成",
            violated_constraints=_clean_string_list(payload.get("violated_constraints")),
            missing_actions=_clean_string_list(payload.get("missing_actions")),
            risk_notes=_clean_string_list(payload.get("risk_notes")),
            retry_count=retry_count,
            failure_type=str(payload.get("failure_type", "")).strip(),
        )

