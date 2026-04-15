import base64
import csv
import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Callable

from dataclasses import replace

from contracts import (
    CasualtyEstimate,
    ExtractedEntities,
    IncidentInput,
    MatchedNode,
    RetrievalContext,
    ReviewResult,
    RoutingDecision,
    StrategyDraft,
)
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

DIFFICULTY_ROUTING_PROMPT = """你是交通事故处置难度路由助手。请根据输入描述判断处置难度，只输出 JSON。
【输入说明】
你会收到 incident、rule_result 和 criteria：
- incident：原始事故描述与是否有图片。
- rule_result：规则路由器给出的初判（difficulty/reason/confidence/rule_hits）。
- criteria：维度与阈值定义。

【判定维度 Checklist】
请严格按以下 7 个维度判断是否命中（命中名称必须与下列文本完全一致）：
1. 信息完整性不足：关键字段缺失（如伤亡/泄漏/起火状态不明）。
2. 风险并发性：同时出现 2 类及以上风险（如“被困+泄漏”“火情+拥堵”）。
3. 约束复杂度：存在明显先后依赖或安全门槛（如先封控再处置、先评估再通行）。
4. 主体协同复杂度：至少需要 2 个及以上部门联动且职责边界不清晰。
5. 证据检索难度：需要跨专业知识或法规依据才能决策。
6. 表达噪声：口语化、错别字、矛盾描述、信息冗余明显。
7. 多模态依赖：纯文本不足以判断，疑似需要结合现场图片/视频。

【分级与路由标准】
- 命中 0~1 项 -> easy
- 命中 2~3 项 -> medium
- 命中 >=4 项 -> hard
- 若“信息完整性不足”与“风险并发性”同时命中：在上述结果基础上上调一级（easy->medium, medium->hard）。

【与规则结果冲突时的处理】
- 若与你判断一致，可直接输出。
- 若与你判断不一致（相对 rule_result.difficulty），必须在 reason 中给出明确反证。
- 只有在 confidence >= 冲突改判阈值（默认 0.78，可通过环境变量 TRAFFIC_ROUTER_LLM_OVERRIDE_MIN_CONFIDENCE 调整）且反证充分时，才允许与 rule_result 不同；否则保持与 rule_result 一致。

【confidence 参考标尺】
- 0.85~1.00：证据充分，命中维度清晰且无明显歧义。
- 0.70~0.84：依据较充分，但存在少量不确定。
- 0.50~0.69：边界样本或信息缺口较多。
- 0.00~0.49：证据不足，需保守判断。

输出格式：
{
    "difficulty": "easy|medium|hard",
    "reason": "简要理由",
    "confidence": 0.0,
    "hit_dimensions": [
        "信息完整性不足|风险并发性|约束复杂度|主体协同复杂度|证据检索难度|表达噪声|多模态依赖"
    ]
}

要求：
1. 必须从 easy|medium|hard 中选择。
2. confidence 取值范围 0~1。
3. hit_dimensions 只能从上述 7 个固定名称中选择，去重并按重要性排序；若无命中则返回 []。
4. 不要输出多余文本，只输出合法 JSON。
"""

STRATEGY_GENERATION_PROMPT = """你是交通应急处置指挥助手。请根据输入的事件信息、知识图谱逻辑链路和法规证据，生成可执行的单方案处置策略。
注意：方案服务对象是交通运输主管部门、路政/交警/应急联动单位，不是事故当事人或公众。
生成目标：在满足安全底线前提下，尽可能提升评委LLM三项评分（executability_score、safety_score、compliance_score）。
只输出 JSON，不要输出解释。
字段要求：
{
    "focus": "一句话概括处置焦点",
    "steps": ["按执行顺序给出处置步骤，每步一句中文，包含动作对象/执行单位/触发条件中的至少两项"],
    "required_resources": ["需要调用的资源名称"],
    "legal_references": ["引用的法规/预案文件名"]
}
要求：
1. 以“全策略有效性”为核心：优先确保闭环完整与可执行细节。
2. 策略整体需体现先控险、再救援、再恢复交通秩序的主线，可按场景灵活组织步骤。
3. 充分参考知识图谱和向量证据形成方案。
4. 禁止出现明显高风险或禁忌动作；若信息不足，可给出“需人工复核”的风险提示但仍需提供可执行策略。
5. 若输入含 `generation_objective`，优先按其维度与优先级组织方案。
6. 只输出合法 JSON。
"""

STRATEGY_REVISION_PROMPT = """你是交通应急处置修订助手。请根据原方案和审查意见，输出修订后的完整单方案。
注意：方案服务对象是交通运输主管部门、路政/交警/应急联动单位，不是事故当事人。
修订目标：优先提升评委LLM三项评分：executability_score、safety_score、compliance_score。
只输出 JSON，不要输出解释。
字段要求与原方案一致：
{
    "focus": "一句话概括处置焦点",
    "steps": ["按执行顺序给出处置步骤，每步一句中文，包含动作对象/执行单位/触发条件中的至少两项"],
    "required_resources": ["需要调用的资源名称"],
    "legal_references": ["引用的法规/预案文件名"]
}
要求：
1. 修订目标是提升“整套策略”的闭环质量与评委评分，不是机械逐条改写。
2. 必须优先修复审查指出的红线问题：禁忌动作、关键漏项、明显时序错误。
3. 采用“补丁式修订”：默认保留原有有效步骤与顺序，仅替换薄弱步骤或在相邻位置插入补充步骤，禁止整稿重写。
4. 在保留有效步骤的前提下补齐关键风险覆盖，避免引入与场景无关的冗余步骤。
5. 对于原方案中的模糊表述和口号式步骤需要改写为具体可执行的动作和明确的责任分工，禁止保留空泛措辞。
6. 参考图谱与证据完成策略重构，但不得照抄原文或仅做表面替换。
7. 严禁保留空泛步骤（如“启动处置”“加强联动”），必须替换为可执行动作和责任分工。
8. 修订后 steps 至少 5 步，且至少 3 步包含明确现场动作动词。
9. 优先根据输入中的 `missing_actions` 与 `risk_notes` 修复薄弱维度。
10. 若输入指出某维度较弱，修订后应在步骤中体现对应改进（可执行细化/风险控制/证据锚定）。
11. 方案调用的资源数量应当合乎事故的严重程度，不得出现大题小作或者小题大做的情况。
11. 只输出合法 JSON。
"""

STRATEGY_REVIEW_PROMPT = """你是交通应急策略模拟评委。请根据事件信息、图谱约束、法规证据和当前方案进行打分与诊断，只输出 JSON。
注意：评审对象是面向交通管理部门的应急策略，不是面向个人的事故处理建议。
字段要求：
{
    "executability_score": 0.0,
    "safety_score": 0.0,
    "compliance_score": 0.0,
    "overall_score": 0.0,
    "reason": "评分结论说明",
    "violated_constraints": ["违反的图谱约束或要求"],
    "missing_actions": ["缺失的关键动作"],
    "risk_notes": ["风险提示"],
    "improvement_actions": ["补丁式修订建议，最多6条；每条需包含：目标问题+建议插入/替换位置+具体动作"]
}
要求：
1. 按 0~1 区间给分。评分维度定义与最终评委一致：
    executability_score：方案是否清晰且可执行。
    safety_score：方案是否充分控制风险且不引入新风险。
    compliance_score：方案是否遵守相关法律法规和交通应急预案。
2. overall_score 与三项子分保持一致，可近似为平均值。
3. 优先检查红线：禁忌动作、关键漏项、明显时序错误，需在 violated_constraints/missing_actions 中给出。
4. 评估方案是否存在小题大做或者大题小做的情况，如果存在需要在风险提示中指出，并给出修订意见。
5. 评分仅基于输入，不得编造不存在的动作、约束或证据。
6. improvement_actions 必须是可执行“补丁指令”，避免空泛措辞（例如“在第2步后新增……由某单位执行……完成标准……”）。
7. 只输出合法 JSON。
"""

SINGLE_AGENT_UNIFIED_EXTRACT_PROMPT = """你是单智能体交通应急决策助手。请仅基于输入事件一次性完成结构化抽取。
只输出 JSON，不要输出解释。
字段要求：
{
    "incident_type": "事故类型短语，尽量保留原始语义",
    "severity": "特别重大|重大|较大|一般|UNKNOWN",
    "severity_reason": "简短定级依据",
    "weather": "天气或环境条件",
    "hazards": ["风险要素短语"],
    "vehicles": ["车辆类型短语"],
    "location_features": ["位置特征短语"],
    "casualties": {"deaths": null, "injuries": null, "missing": null},
    "extract_confidence": 0.0
}
要求：
1. 信息不足时用空字符串、空数组或 null，不编造。
2. 伤亡人数必须为非负整数或 null。
3. severity 仅能从给定集合选择。
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


def _semantic_similarity(left: str, right: str) -> float:
    """融合字符序列与 n-gram Jaccard，提供稳健中文短句相似度。"""
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return 0.0
    sequence_score = SequenceMatcher(a=left_text, b=right_text).ratio()
    jaccard_score = _jaccard_similarity(left_text, right_text)
    return (0.65 * sequence_score) + (0.35 * jaccard_score)


def _read_threshold(env_name: str, default_value: float) -> float:
    text = os.getenv(env_name, str(default_value)).strip()
    try:
        value = float(text)
    except ValueError:
        return default_value
    return min(max(value, 0.0), 1.0)


def _clamp_score(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 0.0), 1.0)


def _read_score_threshold() -> float:
    return _read_threshold("EVALUATOR_MIN_PASS_SCORE", 0.78)


def _read_router_llm_override_min_confidence() -> float:
    """读取 LLM 与规则冲突时的最小改判置信度阈值。"""
    return _read_threshold("TRAFFIC_ROUTER_LLM_OVERRIDE_MIN_CONFIDENCE", 0.78)


def _read_positive_int(env_name: str, default_value: int, min_value: int = 1, max_value: int = 64) -> int:
    text = os.getenv(env_name, str(default_value)).strip()
    try:
        value = int(text)
    except ValueError:
        return default_value
    return max(min_value, min(max_value, value))


def _read_csv_env(env_name: str, default_csv: str) -> list[str]:
    raw = str(os.getenv(env_name, default_csv) or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_review_mode() -> str:
    mode = str(os.getenv("EVALUATOR_REVIEW_MODE", "llm_only") or "").strip().lower()
    if mode in {"llm_only", "rules_only", "hybrid"}:
        return mode
    return "llm_only"


def _read_effective_agent_mode() -> str:
    """优先读取编排器注入的有效执行模式。"""
    mode = str(os.getenv("TRAFFIC_EFFECTIVE_AGENT_MODE", "") or "").strip().lower()
    if mode:
        return mode
    return str(os.getenv("TRAFFIC_AGENT_MODE", "auto") or "").strip().lower()


def _read_single_agent_retrieval_mode() -> str:
    """读取单智能体检索模式：inherit（默认）或 none（跳过双库检索）。"""
    mode = str(os.getenv("TRAFFIC_SINGLE_AGENT_RETRIEVAL_MODE", "inherit") or "").strip().lower()
    if mode in {"none", "off", "disabled", "skip", "no_retrieval"}:
        return "none"
    return "inherit"


def _read_prompt_profile() -> str:
    """读取提示词版本配置：支持全局覆盖与按链路模式自动选择。"""
    global_profile = str(os.getenv("COMMANDER_PROMPT_PROFILE", "") or "").strip().lower()
    if global_profile in {"baseline", "stable", "aggressive"}:
        return global_profile

    agent_mode = _read_effective_agent_mode()
    mode_defaults = {
        "single": "stable",
        "single_agent": "stable",
        "single_v2": "stable",
        "multi_no_review": "aggressive",
        "multi_with_review": "aggressive",
        "auto": "aggressive",
    }
    default_profile = mode_defaults.get(agent_mode, "stable")
    mode_env_name = f"COMMANDER_PROMPT_PROFILE_{agent_mode.upper()}" if agent_mode else ""
    mode_profile = str(os.getenv(mode_env_name, default_profile) or "").strip().lower() if mode_env_name else default_profile
    if mode_profile in {"baseline", "stable", "aggressive"}:
        return mode_profile
    return default_profile


def _read_force_g5_objective_as_g4() -> bool:
    """G5 目标对齐开关：默认开启，让 G5 生成目标默认复用 G4 的 one-pass 目标。"""
    return str(os.getenv("COMMANDER_FORCE_G5_OBJECTIVE_AS_G4", "1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _append_prompt_profile(base_prompt: str, profile: str, stage: str) -> str:
    """在基础提示词后附加版本化要求，便于小样本门禁切换。"""
    profile_notes = {
        "baseline": {
            "generation": "版本策略：baseline。优先保证事故处置闭环与现场真实可执行性，不刻意使用评分导向措辞。",
            "revision": "版本策略：baseline。优先修复硬缺陷与流程闭环，不主动扩写与场景弱相关内容。",
        },
        "stable": {
            "generation": "版本策略：stable。平衡可执行性、安全性、合规性，避免口号化与过度模板化。",
            "revision": "版本策略：stable。按失败维度补齐缺项，并保持步骤简洁与职责清晰。",
        },
        "aggressive": {
            "generation": "版本策略：aggressive。在满足安全底线下，优先强化关键动作细节、法规锚定和风险控制覆盖，并显式补齐高风险场景关键动作。",
            "revision": "版本策略：aggressive。针对低分维度做更强补齐，确保每步都有责任主体与执行动作；保留已高质量步骤，避免回归性改坏。",
        },
    }
    note = profile_notes.get(profile, profile_notes["stable"]).get(stage, "")
    return f"{base_prompt}\n\n{note}" if note else base_prompt


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


def _parse_weakness_tag(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    if "missing_action" in normalized:
        return "missing_action"
    if "risk_note" in normalized:
        return "risk_note"
    if "review_reason" in normalized:
        return "review_reason"
    return "other"


class RetrievalLogicAgent:
    """最小版检索智能体：负责统一调用 Neo4j 与 ChromaDB。"""

    def __init__(self, service: DualRetrievalService | None = None):
        self.name = "检索与逻辑专家"
        self.service = service

    def _ensure_service(self) -> DualRetrievalService:
        if self.service is None:
            self.service = DualRetrievalService()
        return self.service

    def retrieve(self, incident: IncidentInput, entities: ExtractedEntities) -> RetrievalContext:
        return self._ensure_service().retrieve(incident, entities)

    def close(self) -> None:
        if self.service is None:
            return
        self.service.close()
        self.service = None


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


class RouterAgent:
    """根据输入内容进行难度判定并输出链路路由决策。"""

    def __init__(self, llm_router: Callable[[str], dict] | None = None):
        self.name = "难度路由专家"
        self.llm_router = llm_router or self._route_with_llm

    @staticmethod
    def _read_flag(env_name: str, default_value: bool) -> bool:
        raw = str(os.getenv(env_name, "") or "").strip().lower()
        if not raw:
            return default_value
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _contains_any(text: str, patterns: list[str]) -> bool:
        if not text:
            return False
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _upgrade_difficulty(difficulty: str) -> str:
        if difficulty == "easy":
            return "medium"
        if difficulty == "medium":
            return "hard"
        return "hard"

    @staticmethod
    def _normalize_difficulty(value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"easy", "medium", "hard"}:
            return text

        if any(keyword in text for keyword in ["低", "简单", "easy"]):
            return "easy"
        if any(keyword in text for keyword in ["中", "一般复杂", "medium"]):
            return "medium"
        if any(keyword in text for keyword in ["高", "困难", "复杂", "hard"]):
            return "hard"
        return ""

    @classmethod
    def _rule_route(cls, incident: IncidentInput) -> dict:
        text = str(incident.raw_text or "").strip()
        has_image = bool(incident.image_bytes)

        uncertainty_patterns = [
            r"疑似",
            r"不明",
            r"未知",
            r"待确认",
            r"暂不清楚",
            r"尚未",
            r"无法确认",
            r"描述不一致",
            r"前后不一致",
            r"说法不一",
            r"具体情况待进一步确认",
        ]
        info_missing = cls._contains_any(text, uncertainty_patterns)

        risk_category_patterns = {
            "fire": [r"起火", r"燃烧", r"火情", r"冒烟", r"爆炸"],
            "leak": [r"泄漏", r"渗漏", r"危化", r"危险品", r"有毒", r"刺激性气味"],
            "casualty": [r"伤员", r"受伤", r"死亡", r"失联", r"被困"],
            "traffic_block": [r"中断", r"阻断", r"受阻", r"封闭", r"拥堵", r"滞留"],
            "secondary": [r"坍塌", r"塌方", r"落石", r"积水", r"滑坡"],
        }
        matched_risk_categories: list[str] = []
        for category, patterns in risk_category_patterns.items():
            if cls._contains_any(text, patterns):
                matched_risk_categories.append(category)
        risk_concurrency = len(matched_risk_categories) >= 2

        constraint_complexity = cls._contains_any(
            text,
            [
                r"先.+再",
                r"后.+方可",
                r"分阶段",
                r"同步",
                r"并行",
                r"解除后",
                r"恢复前",
                r"评估后",
                r"边.+边",
            ],
        )

        department_patterns = {
            "交警": [r"交警", r"公安交管"],
            "消防": [r"消防", r"消防救援"],
            "医疗": [r"医疗", r"急救", r"120"],
            "路政": [r"路政", r"养护", r"清障"],
            "应急": [r"应急", r"应急管理"],
            "环保": [r"环保", r"生态环境"],
            "气象": [r"气象"],
            "交通": [r"交通运输", r"运输主管"],
        }
        matched_departments: list[str] = []
        for department, patterns in department_patterns.items():
            if cls._contains_any(text, patterns):
                matched_departments.append(department)
        collaboration_complexity = len(matched_departments) >= 2 or cls._contains_any(
            text,
            [r"多部门", r"联合", r"联动", r"协同"],
        )

        high_risk_scene = cls._contains_any(
            text,
            [
                r"危化",
                r"危险品",
                r"隧道",
                r"桥梁",
                r"客运",
                r"校车",
                r"油罐",
                r"跨省",
                r"枢纽",
                r"夜间",
                r"恶劣天气",
            ],
        )
        legal_or_evidence_signal = cls._contains_any(
            text,
            [r"预案", r"法规", r"合规", r"是否可通行", r"恢复通行", r"专家评估"],
        )
        retrieval_difficulty = legal_or_evidence_signal or (
            high_risk_scene and (risk_concurrency or info_missing or collaboration_complexity or constraint_complexity)
        )

        expression_noise = cls._contains_any(
            text,
            [
                r"前后不一致",
                r"描述不一致",
                r"说法不一",
                r"好像",
                r"大概",
                r"可能",
                r"听说",
                r"断断续续",
                r"。{3,}",
                r"\?{2,}",
                r"！{2,}",
            ],
        )

        multimodal_dependency = has_image or cls._contains_any(
            text,
            [r"图片", r"照片", r"图像", r"监控", r"视频", r"画面"],
        )

        hit_dimensions: list[str] = []
        if info_missing:
            hit_dimensions.append("信息完整性不足")
        if risk_concurrency:
            hit_dimensions.append("风险并发性")
        if constraint_complexity:
            hit_dimensions.append("约束复杂度")
        if collaboration_complexity:
            hit_dimensions.append("主体协同复杂度")
        if retrieval_difficulty:
            hit_dimensions.append("证据检索难度")
        if expression_noise:
            hit_dimensions.append("表达噪声")
        if multimodal_dependency:
            hit_dimensions.append("多模态依赖")

        hit_count = len(hit_dimensions)
        if hit_count <= 1:
            difficulty = "easy"
        elif hit_count <= 3:
            difficulty = "medium"
        else:
            difficulty = "hard"

        if info_missing and risk_concurrency:
            difficulty = cls._upgrade_difficulty(difficulty)

        if hit_count in {0, 5, 6, 7}:
            confidence = 0.9
        elif hit_count in {1, 4}:
            confidence = 0.74
        else:
            confidence = 0.66
        if expression_noise:
            confidence -= 0.04
        if len(text) < 16:
            confidence -= 0.05
        if info_missing and hit_count <= 1:
            confidence -= 0.12
        confidence = _clamp_score(confidence, default=0.0)

        reason_fragments = hit_dimensions[:3] if hit_dimensions else ["未命中高复杂度维度"]
        if info_missing and risk_concurrency:
            reason_fragments.append("关键信息缺失与多风险并发触发上调")

        return {
            "difficulty": difficulty,
            "reason": "；".join(reason_fragments),
            "confidence": confidence,
            "rule_hit_count": hit_count,
            "rule_hits": hit_dimensions,
        }

    @classmethod
    def _build_llm_payload(cls, incident: IncidentInput, rule_result: dict) -> str:
        payload = {
            "incident": {
                "raw_text": incident.raw_text,
                "has_image": bool(incident.image_bytes),
            },
            "rule_result": {
                "difficulty": rule_result.get("difficulty", ""),
                "reason": rule_result.get("reason", ""),
                "confidence": rule_result.get("confidence", 0.0),
                "rule_hit_count": rule_result.get("rule_hit_count", 0),
                "rule_hits": rule_result.get("rule_hits", []),
            },
            "criteria": {
                "dimensions": [
                    "信息完整性不足",
                    "风险并发性",
                    "约束复杂度",
                    "主体协同复杂度",
                    "证据检索难度",
                    "表达噪声",
                    "多模态依赖",
                ],
                "threshold": {
                    "easy": "命中 0~1 项",
                    "medium": "命中 2~3 项",
                    "hard": "命中 >=4 项",
                },
                "upgrade_rule": "关键安全信息缺失 + 多风险并发 -> 上调一级",
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _route_with_llm(user_content: str) -> dict:
        if not user_content.strip():
            return {}

        model = os.getenv("ROUTER_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("ROUTER_TEXT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)
        return _chat_json(
            model=model,
            prompt=DIFFICULTY_ROUTING_PROMPT,
            user_content=user_content,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def _parse_llm_result(cls, payload: dict) -> dict:
        if not isinstance(payload, dict) or not payload:
            return {}

        difficulty = cls._normalize_difficulty(payload.get("difficulty", ""))
        if not difficulty:
            return {}

        confidence = _clamp_score(payload.get("confidence", 0.0), default=0.0)
        reason = str(payload.get("reason", "")).strip()
        hit_dimensions = _clean_string_list(payload.get("hit_dimensions"))
        return {
            "difficulty": difficulty,
            "confidence": confidence,
            "reason": reason,
            "hit_dimensions": hit_dimensions,
        }

    @staticmethod
    def _should_trigger_llm(rule_result: dict) -> bool:
        trigger_confidence = _read_threshold("TRAFFIC_ROUTER_LLM_TRIGGER_CONFIDENCE", 0.72)
        rule_confidence = _clamp_score(rule_result.get("confidence", 0.0), default=0.0)
        rule_hit_count = int(rule_result.get("rule_hit_count", 0) or 0)
        return rule_confidence < trigger_confidence or rule_hit_count in {1, 2, 3, 4}

    def decide(self, incident: IncidentInput) -> RoutingDecision:
        rule_result = self._rule_route(incident)
        difficulty = str(rule_result.get("difficulty", "medium"))
        reason = str(rule_result.get("reason", "")).strip()
        confidence = _clamp_score(rule_result.get("confidence", 0.0), default=0.0)
        rule_hits = [str(item).strip() for item in (rule_result.get("rule_hits", []) or []) if str(item).strip()]
        rule_hit_count = int(rule_result.get("rule_hit_count", len(rule_hits)) or len(rule_hits))

        used_llm = False
        enable_llm = self._read_flag("TRAFFIC_ROUTER_LLM_ENABLED", True)
        llm_override_min_confidence = _read_router_llm_override_min_confidence()
        if enable_llm and self._should_trigger_llm(rule_result):
            llm_payload = self.llm_router(self._build_llm_payload(incident, rule_result)) or {}
            llm_result = self._parse_llm_result(llm_payload)
            if llm_result:
                used_llm = True
                llm_difficulty = str(llm_result.get("difficulty", "")).strip()
                llm_reason = str(llm_result.get("reason", "")).strip()
                llm_confidence = _clamp_score(llm_result.get("confidence", 0.0), default=0.0)

                if llm_difficulty == difficulty:
                    confidence = max(confidence, llm_confidence)
                    if llm_reason:
                        reason = f"{reason}；LLM复核：{llm_reason}".strip("；")
                elif llm_confidence >= max(confidence + 0.08, llm_override_min_confidence):
                    difficulty = llm_difficulty
                    reason = llm_reason or reason
                    confidence = llm_confidence

        min_confidence = _read_threshold("TRAFFIC_ROUTER_MIN_CONFIDENCE", 0.62)
        fallback_to_g5 = False
        fallback_reason = ""

        if difficulty not in {"easy", "medium", "hard"}:
            fallback_to_g5 = True
            fallback_reason = "难度判定无效，回退到G5链路"
            difficulty = "medium"
            confidence = 0.0
        elif confidence < min_confidence:
            fallback_to_g5 = True
            fallback_reason = f"路由置信度不足({confidence:.2f}<{min_confidence:.2f})，回退到G5链路"

        if not fallback_to_g5 and difficulty == "easy":
            route_target = "single_agent"
        else:
            route_target = "multi_with_review"

        _debug_log(
            "router_decision",
            difficulty=difficulty,
            confidence=round(confidence, 4),
            route_target=route_target,
            fallback_to_g5=fallback_to_g5,
            fallback_reason=fallback_reason,
            used_llm=used_llm,
            rule_hit_count=rule_hit_count,
            rule_hits=rule_hits,
        )

        return RoutingDecision(
            requested_mode="auto",
            effective_mode=route_target,
            route_target=route_target,
            difficulty=difficulty,
            reason=reason,
            confidence=confidence,
            used_llm=used_llm,
            fallback_to_g5=fallback_to_g5,
            fallback_reason=fallback_reason,
            rule_hit_count=rule_hit_count,
            rule_hits=rule_hits,
        )


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

    @staticmethod
    def _default_fallback_steps() -> list[str]:
        return [
            "由交警先行封控事故影响路段并设置分流点，防止二次事故。",
            "由消防与救援力量进入现场处置显性危险源，优先控制火情或泄漏扩散。",
            "由医疗救援组对伤员实施分级救治并转运至就近定点医院。",
            "由路政与清障单位清理障碍物并持续评估路面通行条件。",
            "由现场指挥组按风险解除情况分阶段恢复交通并发布绕行信息。",
        ]

    @classmethod
    def _read_fallback_steps(cls) -> list[str]:
        env_steps = _read_csv_env("COMMANDER_FALLBACK_STEPS", "")
        return cls._unique_keep_order(env_steps) if env_steps else cls._default_fallback_steps()

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
        action_targets = cls._filter_action_hints(action_targets, limit=8)
        for action_name in action_targets[:4]:
            candidate_step = f"由现场指挥组立即组织执行“{action_name}”，明确作业区域、责任单位和完成时限。"
            if candidate_step not in steps:
                steps.append(candidate_step)

        if review:
            if review.failure_type not in {"llm_review_failed", "empty_strategy"}:
                for action in review.missing_actions[:3]:
                    candidate_step = f"由对应联动单位补充落实“{action}”现场动作，形成可核查的执行清单。"
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
            steps.extend(cls._read_fallback_steps())

        steps = cls._normalize_steps(steps)

        return StrategyDraft(
            focus=entities.incident_type or "交通事故应急处置",
            steps=steps,
            required_resources=resources,
            legal_references=references,
        )

    @classmethod
    def _read_agent_mode(cls) -> str:
        return _read_effective_agent_mode()

    @classmethod
    def _is_valid_action_hint(cls, action_name: str) -> bool:
        text = str(action_name or "").strip()
        if not text or len(text) < 2:
            return False

        banned_keywords = _read_csv_env(
            "COMMANDER_ACTION_HINT_BANNED_KEYWORDS",
            "驾驶员操作不当,驾驶员未观察路况,正面碰撞,包扎,报警,抛锚",
        )
        banned_patterns = _read_csv_env(
            "COMMANDER_ACTION_HINT_BANNED_PATTERNS",
            "^驾驶员.*(不当|未观察|疏忽).*$,^正面碰撞$,^包扎$,^报警$,^抛锚$",
        )

        if any(keyword and keyword in text for keyword in banned_keywords):
            return False
        if any(pattern and re.search(pattern, text) for pattern in banned_patterns):
            return False
        return True

    @classmethod
    def _select_preserve_steps(cls, steps: list[str], top_k: int = 4) -> list[str]:
        """优先保留动作明确的高质量步骤，降低修订回归风险。"""
        cleaned_steps = [str(item or "").strip() for item in steps if str(item or "").strip()]
        if not cleaned_steps:
            return []

        strong_tokens = _read_csv_env("COMMANDER_PRESERVE_STRONG_TOKENS", "")
        if not strong_tokens:
            return cleaned_steps[: max(1, top_k)]

        prioritized: list[str] = []
        for step in cleaned_steps:
            if any(token in step for token in strong_tokens) and step not in prioritized:
                prioritized.append(step)
        for step in cleaned_steps:
            if step not in prioritized:
                prioritized.append(step)

        return prioritized[: max(1, top_k)]

    @classmethod
    def _filter_action_hints(cls, hints: list[str], limit: int = 8) -> list[str]:
        cleaned = cls._unique_keep_order(
            [
                str(item or "").strip()
                for item in hints
                if cls._is_valid_action_hint(str(item or "").strip())
            ]
        )
        if not cleaned:
            return []
        return cleaned[: max(1, limit)]

    @classmethod
    def _is_actionable_step_text(cls, step_text: str) -> bool:
        strong_tokens = _read_csv_env("COMMANDER_NORMALIZE_STRONG_TOKENS", "")
        step_action_tokens = _read_csv_env("COMMANDER_STEP_ACTION_TOKENS", "")
        responsibility_markers = _read_csv_env("COMMANDER_STEP_RESPONSIBILITY_MARKERS", "")
        text = str(step_text or "").strip()
        if not text:
            return False

        has_action = any(token in text for token in (strong_tokens + step_action_tokens)) if (strong_tokens or step_action_tokens) else ("由" in text and "，" in text)
        has_responsible_party = any(marker in text for marker in responsibility_markers) if responsibility_markers else ("由" in text)
        return has_action and has_responsible_party and len(text) >= 16

    @classmethod
    def _is_weak_step_text(cls, step_text: str) -> bool:
        weak_step_patterns = _read_csv_env("COMMANDER_WEAK_STEP_PATTERNS", "")
        text = str(step_text or "").strip()
        if not text:
            return True
        if any(re.search(pattern, text) for pattern in weak_step_patterns):
            return True
        return not cls._is_actionable_step_text(text)

    @classmethod
    def _stabilize_revision_steps(
        cls,
        draft_steps: list[str],
        revised_steps: list[str],
        preserve_steps: list[str],
        max_rewrite_steps: int,
    ) -> list[str]:
        """限制修订改写幅度：优先保留有效步骤，只替换少量弱步骤。"""
        old_steps = cls._unique_keep_order([str(item or "").strip() for item in draft_steps if str(item or "").strip()])
        new_steps = cls._unique_keep_order([str(item or "").strip() for item in revised_steps if str(item or "").strip()])
        if not old_steps or not new_steps:
            return new_steps or old_steps

        rewrite_count = sum(1 for step in old_steps if step not in new_steps)
        if rewrite_count <= max_rewrite_steps:
            return new_steps

        preserved_set = set(cls._unique_keep_order([str(item or "").strip() for item in preserve_steps]))
        stabilized = old_steps[:]
        new_only_steps = [step for step in new_steps if step not in old_steps]
        if not new_only_steps:
            return old_steps

        weak_slots = [index for index, step in enumerate(stabilized) if cls._is_weak_step_text(step)]
        fallback_slots = [
            index
            for index, step in enumerate(stabilized)
            if index not in weak_slots and step not in preserved_set
        ]
        replace_slots = weak_slots + fallback_slots

        applied = 0
        for slot in replace_slots:
            if applied >= max_rewrite_steps or applied >= len(new_only_steps):
                break
            stabilized[slot] = new_only_steps[applied]
            applied += 1

        return cls._unique_keep_order(stabilized)

    @classmethod
    def _build_structured_weaknesses(
        cls,
        context: RetrievalContext,
        review: ReviewResult,
        suspected_weaknesses: list[str],
    ) -> dict:
        """将审查反馈映射为可执行修订目标，减少修订阶段的泛化改写。"""
        weakness_tags = cls._unique_keep_order([
            _parse_weakness_tag(item)
            for item in suspected_weaknesses
            if _parse_weakness_tag(item)
        ])

        mandatory_fix_actions = cls._unique_keep_order([str(item or "").strip() for item in review.missing_actions[:8] if str(item or "").strip()])[:6]

        graph_action_hints = cls._unique_keep_order(
            [
                item.target_node
                for item in context.neo4j_constraints
                if item.relation == "TRIGGERS" and str(item.target_node or "").strip()
            ]
        )
        graph_action_hints = cls._filter_action_hints(graph_action_hints, limit=8)

        step_patch_instructions = cls._build_step_patch_instructions(review, graph_action_hints)

        return {
            "failure_type": str(review.failure_type or "").strip(),
            "weakness_tags": weakness_tags,
            "mandatory_fix_actions": mandatory_fix_actions,
            "graph_action_hints": graph_action_hints,
            "step_patch_instructions": step_patch_instructions,
            "patch_mode": "patch_first",
            "revision_style": "targeted_repair",
        }

    @classmethod
    def _build_step_patch_instructions(cls, review: ReviewResult, graph_action_hints: list[str]) -> list[str]:
        """将审查结论转换为补丁式修订指令，避免整稿重写。"""
        patches: list[str] = []

        for action in [str(item or "").strip() for item in review.missing_actions[:4] if str(item or "").strip()]:
            patches.append(
                f"在现有步骤2~4之间新增一条动作补丁：由责任单位执行“{action}”，并写明触发条件与完成标准。"
            )

        for item in [str(item or "").strip() for item in review.violated_constraints[:3] if str(item or "").strip()]:
            patches.append(
                f"对相关步骤做局部替换以修复约束冲突：{item}；同时补充风险隔离与复核闭环。"
            )

        for note in [str(item or "").strip() for item in review.risk_notes[:3] if str(item or "").strip()]:
            if "笼统" in note or "重复" in note or "口号" in note:
                patches.append("仅替换最笼统的1~2条步骤为具体现场动作，其他步骤保持不变。")

        for hint in [str(item or "").strip() for item in graph_action_hints[:2] if str(item or "").strip()]:
            patches.append(f"若缺少对应动作，可新增一条“{hint}”补丁步骤，并锚定法规/证据来源。")

        return cls._unique_keep_order(patches)[:8]

    @classmethod
    def _normalize_steps(cls, steps: list[str]) -> list[str]:
        """对步骤做最小规范化，降低口号化表达。"""
        cleaned = cls._unique_keep_order([str(item or "").strip() for item in steps if str(item or "").strip()])
        if not cleaned:
            return cleaned

        enable_rewrite = str(os.getenv("COMMANDER_NORMALIZE_ENABLE_REWRITE", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if not enable_rewrite:
            return cleaned

        weak_tokens = _read_csv_env("COMMANDER_NORMALIZE_WEAK_TOKENS", "启动,复核,联动,协同,落实,处置")
        strong_tokens = _read_csv_env("COMMANDER_NORMALIZE_STRONG_TOKENS", "封控,分流,救治,灭火,封堵,转运,清障,排险,警戒,复通")
        rewrite_template = str(
            os.getenv(
                "COMMANDER_NORMALIZE_REWRITE_TEMPLATE",
                "步骤{index}：由现场责任单位执行具体现场动作，并记录完成条件与时限。",
            )
        ).strip()
        normalized: list[str] = []
        for idx, item in enumerate(cleaned, start=1):
            if any(token in item for token in strong_tokens):
                normalized.append(item)
                continue

            if any(token in item for token in weak_tokens):
                normalized.append(rewrite_template.format(index=idx))
                continue

            normalized.append(item)
        return normalized

    def generate(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
    ) -> StrategyDraft:
        llm_draft = self.generator(incident, entities, context)
        if llm_draft:
            llm_draft.steps = self._normalize_steps(llm_draft.steps)
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
        prompt_profile = _read_prompt_profile()
        agent_mode = _read_effective_agent_mode()

        objective_by_profile = {
            "baseline": {
                "target": "strategy_quality",
                "dimensions": ["executability_score", "safety_score", "compliance_score"],
                "priority_order": [
                    "先保证关键风险覆盖与禁忌动作规避",
                    "再保证步骤可执行与责任明确",
                    "最后补充法规和证据锚定",
                ],
            },
            "stable": {
                "target": "maximize_judge_score",
                "dimensions": ["executability_score", "safety_score", "compliance_score"],
                "priority_order": [
                    "先保证关键风险覆盖与禁忌动作规避",
                    "再提升步骤可执行性（责任单位+动作+条件）",
                    "最后强化法规和证据对关键动作的锚定",
                ],
            },
            "aggressive": {
                "target": "maximize_judge_score",
                "dimensions": ["executability_score", "safety_score", "compliance_score"],
                "priority_order": [
                    "先最大化关键风险覆盖和现场控险动作",
                    "再强化步骤粒度、执行时序与责任分工",
                    "最后强化法规依据与证据引用完整度",
                ],
            },
        }
        generation_objective = objective_by_profile.get(prompt_profile, objective_by_profile["stable"])

        if agent_mode == "multi_no_review":
            generation_objective = {
                "target": "one_pass_high_score",
                "dimensions": ["executability_score", "safety_score", "compliance_score"],
                "priority_order": [
                    "单轮成稿即需可落地：优先覆盖控险、救援、排险、复通四段闭环",
                    "每步尽量包含执行单位+现场动作+触发条件，减少口号化表达",
                    "关键步骤优先锚定法规或证据来源，避免泛化表述",
                    
                ],
                "min_action_steps": 4,
                "prefer_step_count": 6,
            }
        elif agent_mode == "multi_with_review":
            if _read_force_g5_objective_as_g4():
                generation_objective = {
                    "target": "one_pass_high_score",
                    "dimensions": ["executability_score", "safety_score", "compliance_score"],
                    "priority_order": [
                        "单轮成稿即需可落地：优先覆盖控险、救援、排险、复通四段闭环",
                        "每步尽量包含执行单位+现场动作+触发条件，减少口号化表达",
                        "关键步骤优先锚定法规或证据来源，避免泛化表述",
                    ],
                    "min_action_steps": 4,
                    "prefer_step_count": 6,
                }

        user_content = json.dumps(
            {
                "generation_objective": generation_objective,
                "context": json.loads(cls._format_context_payload(incident, entities, context)),
            },
            ensure_ascii=False,
            indent=2,
        )

        payload = _chat_json(
            model=model,
            prompt=_append_prompt_profile(STRATEGY_GENERATION_PROMPT, prompt_profile, "generation"),
            user_content=user_content,
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
            revised_draft.steps = self._normalize_steps(revised_draft.steps)
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
        prompt_profile = _read_prompt_profile()

        suspected_weaknesses: list[str] = []
        review_reason = str(review.reason or "").strip()
        if review_reason:
            suspected_weaknesses.append(f"review_reason: {review_reason}")
        for action in review.missing_actions[:6]:
            suspected_weaknesses.append(f"missing_action: {action}")
        for note in review.risk_notes[:6]:
            suspected_weaknesses.append(f"risk_note: {note}")

        preserve_steps = self._select_preserve_steps(draft.steps, top_k=4)
        agent_mode = self._read_agent_mode()
        max_rewrite_steps = _read_positive_int("COMMANDER_REVISION_MAX_REWRITE_STEPS", 2, min_value=1, max_value=6)

        revision_objective = {
            "target": "strategy_quality" if prompt_profile == "baseline" else "maximize_judge_score",
            "dimensions": ["executability_score", "safety_score", "compliance_score"],
            "priority_order": [
                "先修复关键漏项与禁忌风险",
                "再提升步骤可执行性与责任分工",
                "最后强化证据与法规锚定",
            ],
            "suspected_weaknesses": suspected_weaknesses,
            "preserve_effective_steps": preserve_steps,
            "avoid_regression": True,
            "structured_weaknesses": self._build_structured_weaknesses(context, review, suspected_weaknesses),
            "patch_first_policy": {
                "enabled": True,
                "max_targeted_edits": max_rewrite_steps,
                "prefer_insert_before_replace": True,
                "protect_non_weak_steps": True,
            },
            "minimal_rewrite_policy": {
                "keep_step_order": True,
                "max_rewrite_steps": max_rewrite_steps,
                "preserve_step_count": len(preserve_steps),
            },
        }

        if agent_mode == "multi_with_review":
            revision_objective.update(
                {
                    "target": "review_robust_repair",
                    "priority_order": [
                        "优先补齐审查明确漏项并修复红线风险",
                        "尽量保留有效步骤顺序，仅对薄弱步骤做定向增强",
                        "将新增关键动作与证据/法规锚定，避免无依据扩写",
                    ],
                    "prefer_step_count": 6,
                    "min_action_steps": 4,
                }
            )

        user_content = json.dumps(
            {
                "revision_objective": revision_objective,
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
            prompt=_append_prompt_profile(STRATEGY_REVISION_PROMPT, prompt_profile, "revision"),
            user_content=user_content,
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            return None

        steps = _clean_string_list(payload.get("steps"))
        if not steps:
            return None

        if agent_mode == "multi_with_review":
            steps = self._stabilize_revision_steps(
                draft_steps=draft.steps,
                revised_steps=steps,
                preserve_steps=preserve_steps,
                max_rewrite_steps=max_rewrite_steps,
            )

        references = _clean_string_list(payload.get("legal_references"))
        if not references and context.chroma_evidence:
            references = self._unique_keep_order([item.file_name for item in context.chroma_evidence])

        return StrategyDraft(
            focus=str(payload.get("focus", "")).strip() or draft.focus,
            steps=steps,
            required_resources=_clean_string_list(payload.get("required_resources")),
            legal_references=references,
        )


class SinglePipelineAgent:
    """单智能体入口：对外以单一 Agent 方式完成抽取、检索与生成。"""

    def __init__(
        self,
        retrieval_service: DualRetrievalService | None = None,
    ):
        self.name = "单智能体处置专家"
        self.retrieval_service = retrieval_service

    def _ensure_retrieval_service(self) -> DualRetrievalService:
        if self.retrieval_service is None:
            self.retrieval_service = DualRetrievalService()
        return self.retrieval_service

    @staticmethod
    def _build_no_retrieval_context(entities: ExtractedEntities) -> RetrievalContext:
        severity = entities.severity if entities.severity in {"特别重大", "重大", "较大", "一般"} else "UNKNOWN"
        return RetrievalContext(
            neo4j_constraints=[],
            chroma_evidence=[],
            severity=severity,
            severity_source="SINGLE_AGENT",
        )

    @staticmethod
    def _default_fallback_steps() -> list[str]:
        return [
            "由交警先行封控事故影响路段并设置分流点，防止二次事故。",
            "由消防与救援力量进入现场处置显性危险源，优先控制火情或泄漏扩散。",
            "由医疗救援组对伤员实施分级救治并转运至就近定点医院。",
            "由路政与清障单位清理障碍物并持续评估路面通行条件。",
            "由现场指挥组按风险解除情况分阶段恢复交通并发布绕行信息。",
        ]

    @staticmethod
    def _extract_with_single_llm(incident: IncidentInput) -> ExtractedEntities:
        text = str(incident.raw_text or "").strip()
        model = os.getenv("SINGLE_AGENT_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("SINGLE_AGENT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)

        payload = _chat_json(
            model=model,
            prompt=SINGLE_AGENT_UNIFIED_EXTRACT_PROMPT,
            user_content=text,
            timeout_seconds=timeout_seconds,
        )
        if not payload:
            payload = _fallback_extract_from_text(text)

        incident_type = str(payload.get("incident_type", "")).strip()
        weather = str(payload.get("weather", "")).strip()
        severity = _normalize_severity_label(payload.get("severity", "UNKNOWN"))
        severity_reason = str(payload.get("severity_reason", "")).strip()

        casualties = payload.get("casualties") or {}
        deaths = _parse_optional_int(casualties.get("deaths"))
        injuries = _parse_optional_int(casualties.get("injuries"))
        missing = _parse_optional_int(casualties.get("missing"))

        try:
            extract_confidence = max(0.0, min(float(payload.get("extract_confidence", 0.0) or 0.0), 1.0))
        except (TypeError, ValueError):
            extract_confidence = 0.0

        return ExtractedEntities(
            incident_type_raw=incident_type,
            incident_type=incident_type,
            matched_events=[],
            severity=severity,
            severity_reason=severity_reason,
            severity_confidence=extract_confidence,
            weather=weather,
            hazards=_clean_string_list(payload.get("hazards")),
            vehicles=_clean_string_list(payload.get("vehicles")),
            location_features=_clean_string_list(payload.get("location_features")),
            casualty_estimate=CasualtyEstimate(
                deaths=deaths,
                injuries=injuries,
                missing=missing,
                unknown=all(value is None for value in [deaths, injuries, missing]),
            ),
            evidence_from_image=["已提供现场图片，待单智能体融合推理"] if incident.image_bytes else [],
            extract_confidence=extract_confidence,
        )

    def _generate_strategy_with_single_llm(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
    ) -> StrategyDraft:
        model = os.getenv("SINGLE_AGENT_TEXT_MODEL", get_default_model())
        timeout_seconds = _read_timeout("SINGLE_AGENT_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS)

        user_content = _compact_json_dumps(
            {
                "incident": {
                    "raw_text": incident.raw_text,
                    "has_image": bool(incident.image_bytes),
                },
                "entities": {
                    "incident_type": entities.incident_type,
                    "severity": entities.severity,
                    "severity_reason": entities.severity_reason,
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
                "graph_constraints": [
                    {
                        "rule": item.rule,
                        "source_node": item.source_node,
                        "relation": item.relation,
                        "target_node": item.target_node,
                    }
                    for item in context.neo4j_constraints
                ],
                "evidence": [
                    {
                        "file_name": item.file_name,
                        "content": item.content,
                    }
                    for item in context.chroma_evidence[:8]
                ],
                "generation_objective": {
                    "style": "strict_single_agent",
                    "priority": ["executability", "safety", "compliance"],
                    "constraint": "必须输出可执行、可审计、可追责的交通应急处置方案",
                },
            }
        )

        payload = _chat_json(
            model=model,
            prompt=STRATEGY_GENERATION_PROMPT,
            user_content=user_content,
            timeout_seconds=timeout_seconds,
        )

        if not payload:
            return StrategyDraft(
                focus=f"围绕{entities.incident_type or '交通突发事件'}的应急处置",
                steps=self._default_fallback_steps(),
                required_resources=[],
                legal_references=[],
            )

        steps = _clean_string_list(payload.get("steps"))
        if not steps:
            steps = self._default_fallback_steps()

        legal_references = _clean_string_list(payload.get("legal_references"))
        if not legal_references and context.chroma_evidence:
            legal_references = []
            for item in context.chroma_evidence:
                file_name = str(item.file_name or "").strip()
                if file_name and file_name not in legal_references:
                    legal_references.append(file_name)

        return StrategyDraft(
            focus=str(payload.get("focus", "")).strip() or f"围绕{entities.incident_type or '交通突发事件'}的应急处置",
            steps=steps,
            required_resources=_clean_string_list(payload.get("required_resources")),
            legal_references=legal_references,
        )

    def solve(self, incident: IncidentInput) -> tuple[ExtractedEntities, RetrievalContext, StrategyDraft]:
        entities = self._extract_with_single_llm(incident)
        retrieval_mode = _read_single_agent_retrieval_mode()
        if retrieval_mode == "none":
            context = self._build_no_retrieval_context(entities)
        else:
            context = self._ensure_retrieval_service().retrieve(incident, entities)
        draft = self._generate_strategy_with_single_llm(incident, entities, context)
        _debug_log(
            "single_agent_solve_completed",
            incident_type=entities.incident_type,
            step_count=len(draft.steps),
            constraint_count=len(context.neo4j_constraints),
            evidence_count=len(context.chroma_evidence),
            retrieval_mode=retrieval_mode,
            retrieval_bypassed=retrieval_mode == "none",
            image_attached=bool(incident.image_bytes),
            strict_single_agent=True,
        )
        return entities, context, draft

    def close(self) -> None:
        if self.retrieval_service is None:
            return
        self.retrieval_service.close()
        self.retrieval_service = None


class EvaluatorAgent:
    """最小版推演评估智能体：基于 LLM 审查单方案草案。"""

    def __init__(
        self,
        reviewer: Callable[[IncidentInput, ExtractedEntities, RetrievalContext, StrategyDraft, int], ReviewResult | None] | None = None,
    ):
        self.name = "推演评估专家"
        self.reviewer = reviewer or self._review_with_llm

    @staticmethod
    def _merge_unique(items: list[str] | None) -> list[str]:
        result: list[str] = []
        for item in items or []:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def _best_match_score(target: str, texts: list[str]) -> float:
        if not target or not texts:
            return 0.0
        return max((_semantic_similarity(target, item) for item in texts), default=0.0)

    @classmethod
    def _contains_any_keyword(cls, text: str, keywords: list[str]) -> bool:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        return any(keyword in normalized for keyword in keywords)

    @staticmethod
    def _best_match_any(targets: list[str], texts: list[str]) -> float:
        if not targets or not texts:
            return 0.0
        return max((_semantic_similarity(target, text) for target in targets for text in texts), default=0.0)

    @classmethod
    def _ensure_revision_guidance(cls, review: ReviewResult) -> ReviewResult:
        """拒绝结果必须携带可执行修订意见，避免空反馈进入修订环节。"""
        if review.status == "APPROVED":
            return review

        guidance: list[str] = []
        for action in review.missing_actions[:3]:
            guidance.append(
                f"补丁指令：在现有第2~4步后新增“{action}”步骤，写明责任单位、触发条件和完成标准。"
            )
        for item in review.violated_constraints[:2]:
            guidance.append(
                f"补丁指令：局部替换冲突步骤以修复“{item}”，并补充风险隔离与复核动作。"
            )

        if review.executability_score < 0.75:
            guidance.append("补丁指令：仅替换最笼统的1~2条步骤，改为“执行单位+现场动作+完成条件”的具体表达。")
        if review.safety_score < 0.75:
            guidance.append("补丁指令：新增或细化现场隔离、二次风险防控和人员防护步骤，不改动已有效步骤。")
        if review.compliance_score < 0.75:
            guidance.append("补丁指令：为关键新增动作补充法规/预案锚定，避免与既有图谱约束冲突。")

        merged_risks = cls._merge_unique((review.risk_notes or []) + guidance)
        if not merged_risks:
            merged_risks = [
                "请按低分维度补齐修订：明确关键动作、责任分工、风险控制与法规锚定。",
            ]
        review.risk_notes = merged_risks
        return review

    def _review_with_rules(
        self,
        entities: ExtractedEntities,
        context: RetrievalContext,
        draft: StrategyDraft,
        retry_count: int,
    ) -> ReviewResult:
        step_texts = [str(item).strip() for item in draft.steps if str(item).strip()]
        corpus = [draft.focus, *step_texts]
        action_threshold = _read_threshold("EVALUATOR_RULE_ACTION_THRESHOLD", 0.42)

        candidate_actions = self._merge_unique(
            [
                item.target_node
                for item in context.neo4j_constraints
                if item.relation == "TRIGGERS" and item.target_node
            ]
        )

        missing_actions: list[str] = []
        for action in candidate_actions:
            if self._best_match_score(action, corpus) < action_threshold:
                missing_actions.append(action)

        violated_constraints: list[str] = []
        risk_notes: list[str] = []

        injury_threshold = _read_threshold("EVALUATOR_RULE_INJURY_THRESHOLD", 0.38)
        fire_threshold = _read_threshold("EVALUATOR_RULE_FIRE_THRESHOLD", 0.38)
        leak_threshold = _read_threshold("EVALUATOR_RULE_LEAK_THRESHOLD", 0.38)
        fire_keywords = _read_csv_env("EVALUATOR_RULE_FIRE_KEYWORDS", "起火,燃烧,火情")
        leak_keywords = _read_csv_env("EVALUATOR_RULE_LEAK_KEYWORDS", "泄漏,危化,油品")
        injury_actions = _read_csv_env("EVALUATOR_RULE_INJURY_ACTIONS", "医疗救治,伤员救治")
        fire_actions = _read_csv_env("EVALUATOR_RULE_FIRE_ACTIONS", "灭火处置,消防介入")
        leak_actions = _read_csv_env("EVALUATOR_RULE_LEAK_ACTIONS", "泄漏围控,封堵处置")

        has_injury = (entities.casualty_estimate.injuries or 0) > 0 or (entities.casualty_estimate.deaths or 0) > 0
        if has_injury:
            if self._best_match_any(injury_actions, corpus) < injury_threshold:
                missing_actions.append("医疗救治")
                risk_notes.append("存在伤亡信息但方案未明确医疗救治流程")

        hazard_text = " ".join(self._merge_unique(entities.hazards))
        if self._contains_any_keyword(hazard_text, fire_keywords):
            if self._best_match_any(fire_actions, corpus) < fire_threshold:
                missing_actions.append("灭火处置")
                violated_constraints.append("疑似起火但缺少灭火/消防步骤")

        if self._contains_any_keyword(hazard_text, leak_keywords):
            if self._best_match_any(leak_actions, corpus) < leak_threshold:
                missing_actions.append("泄漏围控")
                violated_constraints.append("疑似泄漏但缺少围控/封堵步骤")

        missing_actions = self._merge_unique(missing_actions)
        violated_constraints = self._merge_unique(violated_constraints)
        risk_notes = self._merge_unique(risk_notes)

        score_threshold = _read_score_threshold()
        rule_gap_penalty = min(0.6, 0.08 * len(missing_actions) + 0.1 * len(violated_constraints))
        overall_score = max(0.0, 0.88 - rule_gap_penalty)

        if missing_actions or violated_constraints:
            return ReviewResult(
                status="REJECTED",
                reason="规则审查发现关键动作缺失或高风险约束未满足",
                violated_constraints=violated_constraints,
                missing_actions=missing_actions,
                risk_notes=risk_notes,
                retry_count=retry_count,
                failure_type="rule_gap_detected",
                executability_score=round(max(0.0, overall_score - 0.08), 4),
                safety_score=round(max(0.0, overall_score - 0.1), 4),
                compliance_score=round(max(0.0, overall_score - 0.06), 4),
                overall_score=round(overall_score, 4),
                score_threshold=score_threshold,
            )

        return ReviewResult(
            status="APPROVED",
            reason="规则审查通过",
            violated_constraints=[],
            missing_actions=[],
            risk_notes=[],
            retry_count=retry_count,
            failure_type="",
            executability_score=0.86,
            safety_score=0.88,
            compliance_score=0.86,
            overall_score=0.8667,
            score_threshold=score_threshold,
        )

    def review(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        context: RetrievalContext,
        draft: StrategyDraft,
        retry_count: int = 0,
    ) -> ReviewResult:
        score_threshold = _read_score_threshold()
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
                score_threshold=score_threshold,
            )

        review_mode = _read_review_mode()

        if review_mode == "rules_only":
            rule_review = self._review_with_rules(entities, context, draft, retry_count)
            _debug_log("evaluator_review_mode_rules_only", retry_count=retry_count, status=rule_review.status)
            return self._ensure_revision_guidance(rule_review)

        llm_review = self.reviewer(incident, entities, context, draft, retry_count)

        if review_mode == "llm_only":
            if llm_review:
                _debug_log("evaluator_review_mode_llm_only", retry_count=retry_count, status=llm_review.status)
                return self._ensure_revision_guidance(llm_review)
            _debug_log("evaluator_review_mode_llm_only_failed", retry_count=retry_count)
            return ReviewResult(
                status="APPROVED",
                reason="LLM审查失败，已跳过审查并保留当前方案",
                violated_constraints=[],
                missing_actions=[],
                risk_notes=["LLM审查不可用，当前结果未经过评审打分"],
                retry_count=retry_count,
                failure_type="llm_review_failed",
                score_threshold=score_threshold,
            )

        if not llm_review:
            _debug_log(
                "evaluator_review_hybrid_llm_empty",
                retry_count=retry_count,
                draft_step_count=len(draft.steps),
            )
            return ReviewResult(
                status="APPROVED",
                reason="LLM审查失败，已跳过审查并保留当前方案",
                violated_constraints=[],
                missing_actions=[],
                risk_notes=["LLM审查不可用，当前结果未经过评审打分"],
                retry_count=retry_count,
                failure_type="llm_review_failed",
                score_threshold=score_threshold,
            )

        # hybrid 模式仅用于附加诊断信息，不参与最终通过/拒绝判决。
        rule_review = self._review_with_rules(entities, context, draft, retry_count)
        merged_missing = self._merge_unique(llm_review.missing_actions + rule_review.missing_actions)
        merged_violations = self._merge_unique(llm_review.violated_constraints + rule_review.violated_constraints)
        merged_risks = self._merge_unique(llm_review.risk_notes + rule_review.risk_notes)

        diagnostic_note = f"规则诊断状态: {rule_review.status}"
        if diagnostic_note not in merged_risks:
            merged_risks.append(diagnostic_note)

        merged = ReviewResult(
            status=llm_review.status,
            reason=llm_review.reason,
            violated_constraints=merged_violations,
            missing_actions=merged_missing,
            risk_notes=merged_risks,
            retry_count=retry_count,
            failure_type=llm_review.failure_type,
            executability_score=llm_review.executability_score,
            safety_score=llm_review.safety_score,
            compliance_score=llm_review.compliance_score,
            overall_score=llm_review.overall_score,
            score_threshold=llm_review.score_threshold,
        )
        _debug_log(
            "evaluator_review_hybrid_diagnostic",
            retry_count=retry_count,
            review_mode=review_mode,
            llm_status=llm_review.status,
            rule_status=rule_review.status,
            final_status=merged.status,
            missing_actions=merged.missing_actions,
        )
        return self._ensure_revision_guidance(merged)

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
        constraint_limit = _read_positive_int("EVALUATOR_LLM_CONSTRAINT_LIMIT", 12)
        evidence_limit = _read_positive_int("EVALUATOR_LLM_EVIDENCE_LIMIT", 6)
        score_threshold = _read_score_threshold()
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
                    for item in context.neo4j_constraints[:constraint_limit]
                ],
                "evidence": [
                    {
                        "file_name": item.file_name,
                        "content": _short_text(item.content, limit=120),
                    }
                    for item in context.chroma_evidence[:evidence_limit]
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

        executability_score = _clamp_score(payload.get("executability_score", 0.0))
        safety_score = _clamp_score(payload.get("safety_score", 0.0))
        compliance_score = _clamp_score(payload.get("compliance_score", 0.0))
        overall_score = _clamp_score(
            payload.get("overall_score", (executability_score + safety_score + compliance_score) / 3.0)
        )

        violated_constraints = _clean_string_list(payload.get("violated_constraints"))
        missing_actions = _clean_string_list(payload.get("missing_actions"))
        risk_notes = _clean_string_list(payload.get("risk_notes"))
        improvement_actions = _clean_string_list(payload.get("improvement_actions"))
        normalized_improvements: list[str] = []
        for item in improvement_actions[:6]:
            text = str(item or "").strip()
            if not text:
                continue
            if not text.startswith("补丁指令"):
                text = f"补丁指令：{text}"
            normalized_improvements.append(text)
        risk_notes = self._merge_unique(risk_notes + normalized_improvements)

        is_score_pass = overall_score >= score_threshold
        status = "APPROVED" if is_score_pass else "REJECTED"

        failure_type = str(payload.get("failure_type", "")).strip()
        if status != "APPROVED":
            if overall_score < score_threshold:
                failure_type = failure_type or "score_below_threshold"
            elif missing_actions or violated_constraints:
                failure_type = failure_type or "critical_gap_detected"
            else:
                failure_type = failure_type or "llm_review_rejected"
        else:
            failure_type = ""

        return ReviewResult(
            status=status,
            reason=str(payload.get("reason", "")).strip() or "LLM 模拟评审已完成",
            violated_constraints=violated_constraints,
            missing_actions=missing_actions,
            risk_notes=risk_notes,
            retry_count=retry_count,
            failure_type=failure_type,
            executability_score=round(executability_score, 4),
            safety_score=round(safety_score, 4),
            compliance_score=round(compliance_score, 4),
            overall_score=round(overall_score, 4),
            score_threshold=score_threshold,
        )

