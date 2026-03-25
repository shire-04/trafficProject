import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from entity_aliases import EventAliasStore  # noqa: E402
from llm_provider import generate_json_response, get_default_model  # noqa: E402


DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data_clean" / "case_extract_output"
NODE_CSV_PATH = PROJECT_ROOT / "data_clean" / "neo4j_import" / "国家交通应急预案_neo4j_nodes.csv"
ALLOWED_EXTRACTION_ENTITY_TYPES = {"Event", "Action", "Department", "Resource"}
ALLOWED_EXTRACTION_RELATION_TYPES = {"CAUSES", "IMPLEMENTED_BY", "REQUIRES", "TRIGGERS"}
MIN_ENTITY_CONFIDENCE = 0.55
MIN_RELATION_CONFIDENCE = 0.55
GENERIC_EVENT_NAMES = {
    "事故",
    "交通事故",
    "道路交通事故",
    "公路交通事故",
    "车辆事故",
    "道路事故",
}
ACTION_HINT_KEYWORDS = {
    "启动", "协调", "封闭", "设置", "疏导", "扑灭", "救援", "解救", "转运", "监测", "围堵", "堵漏", "清理", "排查", "排险", "排涝", "抽排", "拖移", "修复", "加固", "评估", "调查", "发布", "处置", "整改", "报警", "撤离", "分流", "检测", "采样",
}
DEPARTMENT_HINT_KEYWORDS = {"部门", "队", "局", "委", "办", "中心", "大队", "支队", "政府", "单位", "管理处", "指挥部", "社区", "医院", "公司"}
RESOURCE_HINT_KEYWORDS = {"装备", "器材", "设备", "车辆", "吊车", "拖车", "消防车", "救护车", "清障车", "除冰车", "泵", "冲锋舟", "救生衣", "工具", "扩张器", "泡沫", "融雪剂", "围油栏", "防化服"}
CIVILIAN_VEHICLE_KEYWORDS = {"轿车", "私家车", "货车", "客车", "越野车", "电动自行车", "施工机械"}
EMERGENCY_VEHICLE_HINTS = {"救援", "消防", "救护", "清障", "应急", "除冰"}
ENTITY_ID_PREFIX = {
    "Event": "EVT_CASE",
    "Action": "MEA_CASE",
    "Resource": "RES_CASE",
    "Department": "ORG_CASE",
}
ENTITY_TYPE_CN_MAP = {
    "Event": "突发事件",
    "Action": "措施",
    "Resource": "应急资源",
    "Department": "部门",
}
ENTITY_LABELS_MAP = {
    "Event": ["PlanEntity", "Event"],
    "Action": ["PlanEntity", "Action"],
    "Resource": ["PlanEntity", "Resource"],
    "Department": ["PlanEntity", "Department"],
}


@dataclass
class CaseRecord:
    case_id: str
    title: str
    accident_text: str
    consequence_text: str
    measure_text: str
    raw_block: str
    source_file: str


def extract_json_object(text: str) -> dict:
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


def clean_string_list(values: Iterable[Any] | None) -> List[str]:
    result: List[str] = []
    for value in values or []:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def make_case_id(title: str, block_text: str, index: int) -> str:
    normalized_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_")
    digest = hashlib.md5(block_text.encode("utf-8")).hexdigest()[:8]
    title_part = normalized_title[:24] if normalized_title else f"case_{index:03d}"
    return f"{index:03d}_{title_part}_{digest}"


def make_entity_temp_id(case_id: str, entity_type: str, entity_name: str, index: int) -> str:
    digest = hashlib.md5(f"{case_id}|{entity_type}|{entity_name}|{index}".encode("utf-8")).hexdigest()[:10].upper()
    prefix = str(entity_type or "ENT").upper()[:4]
    return f"TMP_{prefix}_{digest}"


def make_generated_node_id(entity_type: str, entity_name: str) -> str:
    normalized_type = str(entity_type or "Entity").strip() or "Entity"
    normalized_name = str(entity_name or "").strip() or normalized_type
    prefix = ENTITY_ID_PREFIX.get(normalized_type, f"{normalized_type.upper()}_CASE")
    digest = hashlib.md5(f"{normalized_type}|{normalized_name}".encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}_{digest}"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def is_generic_event_name(name: str) -> bool:
    normalized = normalize_text(name)
    if not normalized:
        return False
    return normalized in {normalize_text(item) for item in GENERIC_EVENT_NAMES}


def _contains_any_keyword(text: str, keywords: set[str]) -> bool:
    raw_text = str(text or "")
    return any(keyword in raw_text for keyword in keywords)


def evaluate_entity_role_quality(entity_type: str, name: str, evidence: str) -> tuple[bool, str]:
    normalized_type = str(entity_type or "").strip()
    entity_name = str(name or "").strip()
    entity_evidence = str(evidence or "").strip()
    merged = f"{entity_name} {entity_evidence}".strip()

    if normalized_type == "Event":
        return True, ""

    if normalized_type == "Action":
        if _contains_any_keyword(merged, ACTION_HINT_KEYWORDS):
            return True, ""
        return False, "action_weak_semantics"

    if normalized_type == "Department":
        if _contains_any_keyword(merged, DEPARTMENT_HINT_KEYWORDS):
            return True, ""
        return False, "department_weak_semantics"

    if normalized_type == "Resource":
        has_resource_hint = _contains_any_keyword(merged, RESOURCE_HINT_KEYWORDS)
        has_civilian_vehicle = _contains_any_keyword(merged, CIVILIAN_VEHICLE_KEYWORDS)
        has_emergency_hint = _contains_any_keyword(merged, EMERGENCY_VEHICLE_HINTS)
        if has_resource_hint:
            return True, ""
        if has_civilian_vehicle and not has_emergency_hint:
            return False, "resource_civilian_vehicle"
        return False, "resource_weak_semantics"

    return True, ""


def evaluate_relation_quality(source_name: str, target_name: str, relation_type: str, evidence: str, confidence: float) -> tuple[bool, str, str]:
    source = str(source_name or "").strip()
    target = str(target_name or "").strip()
    relation = str(relation_type or "").strip().upper()
    relation_evidence = str(evidence or "").strip()
    relation_confidence = float(confidence or 0.0)

    if source and target and normalize_text(source) == normalize_text(target):
        return False, "HIGH", "relation_self_loop"

    if relation_confidence < MIN_RELATION_CONFIDENCE:
        return False, "HIGH", "relation_low_confidence"

    if relation == "CAUSES" and source and target and normalize_text(source) == normalize_text(target):
        return False, "HIGH", "causes_self_reference"

    if relation_evidence:
        evidence_hit = source in relation_evidence or target in relation_evidence
        if not evidence_hit and relation_confidence < 0.85:
            return False, "MEDIUM", "relation_evidence_weak_binding"

    return True, "", ""


def parse_multi_value(value: Any) -> List[str]:
    if isinstance(value, list):
        return clean_string_list(value)
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[|,，;；]+", text)
    return clean_string_list(parts)


def normalize_extraction_relation(
    raw_relation_type: str,
    source_type: str,
    target_type: str,
) -> tuple[str, bool]:
    relation_type = str(raw_relation_type or "").strip().upper()
    normalized_source = str(source_type or "").strip()
    normalized_target = str(target_type or "").strip()

    if relation_type == "CAUSES":
        if normalized_source == "Event" and normalized_target == "Event":
            return "CAUSES", False
        return "", False

    if relation_type == "TRIGGERS":
        if normalized_source == "Event" and normalized_target == "Action":
            return "TRIGGERS", False
        return "", False

    if relation_type == "REQUIRES":
        if normalized_source == "Action" and normalized_target == "Resource":
            return "REQUIRES", False
        if normalized_source == "Resource" and normalized_target == "Action":
            return "REQUIRES", True
        return "", False

    if relation_type == "IMPLEMENTED_BY":
        if normalized_source == "Action" and normalized_target == "Department":
            return "IMPLEMENTED_BY", False
        return "", False

    return "", False


def parse_case_blocks(text: str, source_file: str) -> List[CaseRecord]:
    normalized_text = str(text or "").replace("\r\n", "\n").strip()
    if not normalized_text:
        return []

    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized_text) if block.strip()]

    def is_title_paragraph(paragraph: str) -> bool:
        cleaned = str(paragraph or "").strip()
        if not cleaned:
            return False
        if any(cleaned.startswith(prefix) for prefix in ["特定事故：", "后果：", "措施："]):
            return False
        return True

    assembled_blocks: List[str] = []
    index = 0
    while index < len(raw_blocks):
        current_block = raw_blocks[index]
        normalized_block = current_block.strip()
        if normalized_block in {"经典交通应急处置案例", "交通应急处理案例", "经典交通事故案例"}:
            index += 1
            continue

        if is_title_paragraph(normalized_block):
            next_accident = raw_blocks[index + 1].strip() if index + 1 < len(raw_blocks) else ""
            next_consequence = raw_blocks[index + 2].strip() if index + 2 < len(raw_blocks) else ""
            next_measure = raw_blocks[index + 3].strip() if index + 3 < len(raw_blocks) else ""
            if next_accident.startswith("特定事故：") and next_consequence.startswith("后果：") and next_measure.startswith("措施："):
                assembled_blocks.append("\n".join([normalized_block, next_accident, next_consequence, next_measure]))
                index += 4
                continue

        assembled_blocks.append(normalized_block)
        index += 1

    cases: List[CaseRecord] = []
    for index, block in enumerate(assembled_blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        title = lines[0]
        accident_text = ""
        consequence_text = ""
        measure_text = ""

        for line in lines[1:]:
            if line.startswith("特定事故："):
                accident_text = line.replace("特定事故：", "", 1).strip()
            elif line.startswith("后果："):
                consequence_text = line.replace("后果：", "", 1).strip()
            elif line.startswith("措施："):
                measure_text = line.replace("措施：", "", 1).strip()

        case_id = make_case_id(title, block, index)
        cases.append(
            CaseRecord(
                case_id=case_id,
                title=title,
                accident_text=accident_text,
                consequence_text=consequence_text,
                measure_text=measure_text,
                raw_block=block,
                source_file=source_file,
            )
        )

    return cases


def load_case_records(txt_path: Path) -> List[CaseRecord]:
    content = txt_path.read_text(encoding="utf-8", errors="ignore")
    return parse_case_blocks(content, source_file=txt_path.name)


class OntologyCatalog:
    """读取当前正式图谱节点目录，供抽取后做建议性规范化。"""

    def __init__(self, node_csv_path: Path | None = None):
        self.node_csv_path = node_csv_path or NODE_CSV_PATH
        self.event_alias_store = EventAliasStore()
        self.nodes_by_type: Dict[str, List[Dict[str, str]]] = {}
        self.nodes_by_id: Dict[str, Dict[str, str]] = {}
        self.nodes_by_name: Dict[str, List[Dict[str, str]]] = {}
        self.event_items_by_id: Dict[str, Dict[str, Any]] = {}
        self.event_items_by_name: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.node_csv_path.exists():
            return

        with self.node_csv_path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                node_id = str(row.get("id:ID") or "").strip()
                name = str(row.get("name") or "").strip()
                entity_type_cn = str(row.get("entity_type_cn") or "").strip()
                labels = str(row.get(":LABEL") or "")
                if not node_id or not name:
                    continue

                if "Event" in labels:
                    entity_type = "Event"
                elif "Action" in labels:
                    entity_type = "Action"
                elif "Resource" in labels:
                    entity_type = "Resource"
                elif "Department" in labels:
                    entity_type = "Department"
                else:
                    continue

                self.nodes_by_type.setdefault(entity_type, []).append(
                    {
                        "node_id": node_id,
                        "name": name,
                        "entity_type_cn": entity_type_cn,
                    }
                )
                node_item = {
                    "node_id": node_id,
                    "name": name,
                    "entity_type": entity_type,
                    "entity_type_cn": entity_type_cn,
                }
                self.nodes_by_id[node_id] = node_item
                self.nodes_by_name.setdefault(normalize_text(name), []).append(node_item)

        for item in self.event_alias_store.build_matcher_index():
            self.event_items_by_id[item.get("node_id", "")] = item
            self.event_items_by_name[normalize_text(item.get("name", ""))] = item

    def _score_text(self, query: str, candidate: str) -> float:
        normalized_query = re.sub(r"\s+", "", str(query or "").lower())
        normalized_candidate = re.sub(r"\s+", "", str(candidate or "").lower())
        if not normalized_query or not normalized_candidate:
            return 0.0
        if normalized_query == normalized_candidate:
            return 1.0

        score = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
        if normalized_query in normalized_candidate or normalized_candidate in normalized_query:
            score += 0.15

        query_chars = set(normalized_query)
        candidate_chars = set(normalized_candidate)
        overlap = len(query_chars & candidate_chars)
        if query_chars and candidate_chars:
            score += 0.1 * overlap / max(len(query_chars | candidate_chars), 1)
        return min(score, 1.0)

    def rank_event_candidates(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        query_text = str(query or "").strip()
        if not query_text:
            return []

        results: List[Dict[str, Any]] = []
        for item in self.event_alias_store.build_matcher_index():
            best_score = self._score_text(query_text, item.get("name", ""))
            best_alias = item.get("name", "")
            for alias in item.get("aliases", []):
                alias_score = self._score_text(query_text, alias)
                if alias_score > best_score:
                    best_score = alias_score
                    best_alias = alias
            results.append(
                {
                    "node_id": item.get("node_id", ""),
                    "name": item.get("name", ""),
                    "entity_type": item.get("entity_type", "Event"),
                    "matched_alias": best_alias,
                    "score": round(best_score, 4),
                }
            )

        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return results[:top_k]

    def suggest_node(self, entity_name: str, entity_type: str) -> Dict[str, Any]:
        normalized_type = str(entity_type or "").strip()
        query_text = str(entity_name or "").strip()
        if not normalized_type or not query_text:
            return {}

        if normalized_type == "Event":
            candidates = self.rank_event_candidates(query_text, top_k=1)
            return candidates[0] if candidates else {}

        best_match: Dict[str, Any] = {}
        best_score = 0.0
        for node in self.nodes_by_type.get(normalized_type, []):
            score = self._score_text(query_text, node.get("name", ""))
            if score > best_score:
                best_score = score
                best_match = {
                    "node_id": node.get("node_id", ""),
                    "name": node.get("name", ""),
                    "entity_type": normalized_type,
                    "matched_alias": node.get("name", ""),
                    "score": round(score, 4),
                }
        return best_match

    def get_node_by_id(self, node_id: str) -> Dict[str, str]:
        return dict(self.nodes_by_id.get(str(node_id or "").strip(), {}))

    def get_node_name(self, node_id: str) -> str:
        return self.get_node_by_id(node_id).get("name", "")

    def lookup_existing_node(self, entity_name: str, entity_type: str) -> Dict[str, str]:
        normalized_name = normalize_text(entity_name)
        normalized_type = str(entity_type or "").strip()
        for item in self.nodes_by_name.get(normalized_name, []):
            if item.get("entity_type") == normalized_type:
                return dict(item)
        return {}

    def allocate_node_id(self, entity_name: str, entity_type: str) -> str:
        existing = self.lookup_existing_node(entity_name, entity_type)
        if existing.get("node_id"):
            return existing["node_id"]
        return make_generated_node_id(entity_type, entity_name)

    def event_alias_exists(self, entity_id: str, alias: str) -> bool:
        normalized_alias = normalize_text(alias)
        if not normalized_alias:
            return False
        aliases = self.event_alias_store.get_aliases(entity_id=entity_id, entity_name=self.get_node_name(entity_id))
        return normalized_alias in {normalize_text(item) for item in aliases}

    def _get_event_candidate(self, node_id: str = "", node_name: str = "") -> Dict[str, Any]:
        normalized_id = str(node_id or "").strip()
        if normalized_id and normalized_id in self.event_items_by_id:
            return dict(self.event_items_by_id[normalized_id])
        normalized_name = normalize_text(node_name)
        if normalized_name and normalized_name in self.event_items_by_name:
            return dict(self.event_items_by_name[normalized_name])
        return {}

    def resolve_event_hits(
        self,
        event_name: str,
        resolution_payload: Dict[str, Any] | None = None,
        fallback_candidates: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        payload = resolution_payload or {}
        query_name = str(event_name or "").strip()
        fallback_candidates = fallback_candidates or self.rank_event_candidates(query_name, top_k=10)

        primary_candidate = self._get_event_candidate(
            node_id=str(payload.get("primary_event_candidate_id") or "").strip(),
            node_name=str(payload.get("primary_event_candidate_name") or "").strip(),
        )
        if not primary_candidate and fallback_candidates:
            top_candidate = fallback_candidates[0]
            if float(top_candidate.get("score", 0.0) or 0.0) >= 0.90:
                primary_candidate = dict(top_candidate)

        expanded_candidates: List[Dict[str, Any]] = []
        seen_expanded_ids: set[str] = set()
        expanded_ids = parse_multi_value(payload.get("expanded_event_candidate_ids"))
        expanded_names = parse_multi_value(payload.get("expanded_event_candidate_names"))
        for expanded_id in expanded_ids:
            candidate = self._get_event_candidate(node_id=expanded_id)
            if candidate.get("node_id") and candidate["node_id"] not in seen_expanded_ids:
                expanded_candidates.append(candidate)
                seen_expanded_ids.add(candidate["node_id"])
        for expanded_name in expanded_names:
            candidate = self._get_event_candidate(node_name=expanded_name)
            if candidate.get("node_id") and candidate["node_id"] not in seen_expanded_ids:
                expanded_candidates.append(candidate)
                seen_expanded_ids.add(candidate["node_id"])

        should_create_new_event = bool(payload.get("should_create_new_event"))
        new_event_name = str(payload.get("new_event_name") or query_name).strip()
        if not new_event_name:
            new_event_name = query_name

        if not primary_candidate and not should_create_new_event and fallback_candidates:
            top_candidate = fallback_candidates[0]
            aliases = self.event_alias_store.get_aliases(
                entity_id=top_candidate.get("node_id", ""),
                entity_name=top_candidate.get("name", ""),
            )
            if normalize_text(query_name) in {normalize_text(alias) for alias in aliases + [top_candidate.get("name", "")]}:
                primary_candidate = dict(top_candidate)

        primary_event_id = ""
        primary_event_name = ""
        matched_alias = ""
        primary_match_score = 0.0
        is_new_event = False
        alias_patch_target_ids: List[str] = []
        resolution_type = "unresolved"

        primary_candidate_id = str(primary_candidate.get("node_id") or "").strip() if primary_candidate else ""
        primary_candidate_name = str(primary_candidate.get("name") or "").strip() if primary_candidate else ""
        should_promote_to_specific_event = (
            bool(primary_candidate)
            and not should_create_new_event
            and not is_generic_event_name(query_name)
            and (
                primary_candidate_id == "EVT_TRAFFIC_ACCIDENT"
                or is_generic_event_name(primary_candidate_name)
            )
        )
        if should_promote_to_specific_event and primary_candidate:
            if primary_candidate_id and primary_candidate_id not in seen_expanded_ids:
                expanded_candidates.append(dict(primary_candidate))
                seen_expanded_ids.add(primary_candidate_id)
            should_create_new_event = True
            primary_candidate = {}

        if should_create_new_event or not primary_candidate:
            primary_event_id = self.allocate_node_id(new_event_name, "Event")
            primary_event_name = new_event_name
            is_new_event = True
            resolution_type = "new_event_with_expansions" if expanded_candidates else "new_event"
            alias_patch_target_ids = [item.get("node_id", "") for item in expanded_candidates if item.get("node_id")]
        else:
            primary_event_id = str(primary_candidate.get("node_id") or "").strip()
            primary_event_name = str(primary_candidate.get("name") or query_name).strip()
            matched_alias = str(primary_candidate.get("matched_alias") or primary_event_name).strip()
            primary_match_score = float(primary_candidate.get("score", 0.0) or 0.0)
            resolution_type = "alias_match_with_expansions" if expanded_candidates else "alias_match"
            if query_name and query_name != primary_event_name and not self.event_alias_exists(primary_event_id, query_name):
                alias_patch_target_ids = [primary_event_id]

        expanded_event_ids: List[str] = []
        expanded_event_names: List[str] = []
        for candidate in expanded_candidates:
            candidate_id = str(candidate.get("node_id") or "").strip()
            if not candidate_id or candidate_id == primary_event_id or candidate_id in expanded_event_ids:
                continue
            expanded_event_ids.append(candidate_id)
            expanded_event_names.append(str(candidate.get("name") or "").strip())

        query_event_ids = clean_string_list([primary_event_id] + expanded_event_ids)
        query_event_names = clean_string_list([primary_event_name] + expanded_event_names)
        alias_patch_target_ids = clean_string_list(alias_patch_target_ids)

        return {
            "resolution_type": resolution_type,
            "primary_event_id": primary_event_id,
            "primary_event_name": primary_event_name,
            "primary_match_score": round(primary_match_score, 4),
            "matched_alias": matched_alias,
            "expanded_event_ids": expanded_event_ids,
            "expanded_event_names": expanded_event_names,
            "query_event_ids": query_event_ids,
            "query_event_names": query_event_names,
            "is_new_event": is_new_event,
            "alias_patch_target_ids": alias_patch_target_ids,
            "reason": str(payload.get("reason") or "").strip(),
        }

    def resolve_entity(self, entity_name: str, entity_type: str) -> Dict[str, Any]:
        normalized_type = str(entity_type or "").strip()
        query_text = str(entity_name or "").strip()
        if not normalized_type or not query_text:
            return {}

        if normalized_type == "Event":
            return self.resolve_event_hits(query_text)

        exact_existing = self.lookup_existing_node(query_text, normalized_type)
        if exact_existing:
            return {
                "resolution_type": "existing_match",
                "final_entity_id": exact_existing.get("node_id", ""),
                "final_entity_name": exact_existing.get("name", query_text),
                "matched_alias": exact_existing.get("name", query_text),
                "normalized_score": 1.0,
                "is_new_entity": False,
            }

        suggestion = self.suggest_node(query_text, normalized_type)
        suggestion_score = float(suggestion.get("score", 0.0) or 0.0)
        if suggestion.get("node_id") and suggestion_score >= 0.92:
            return {
                "resolution_type": "existing_match",
                "final_entity_id": suggestion.get("node_id", ""),
                "final_entity_name": suggestion.get("name", query_text),
                "matched_alias": suggestion.get("matched_alias", suggestion.get("name", query_text)),
                "normalized_score": suggestion_score,
                "is_new_entity": False,
            }

        return {
            "resolution_type": "new_entity",
            "final_entity_id": self.allocate_node_id(query_text, normalized_type),
            "final_entity_name": query_text,
            "matched_alias": "",
            "normalized_score": suggestion_score,
            "is_new_entity": True,
        }


class CaseKnowledgeExtractor:
    """使用 LLM 从案例 TXT 中抽取实体与关系，并缓存结果。"""

    def __init__(
        self,
        output_dir: Path | None = None,
        model: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        use_cache: bool = True,
    ):
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.cache_dir = self.output_dir / "cache"
        ensure_directory(self.output_dir)
        ensure_directory(self.cache_dir)
        self.model = model or get_default_model()
        self.timeout_seconds = timeout_seconds
        self.use_cache = use_cache
        self.ontology_catalog = OntologyCatalog()

    def _cache_path(self, case_id: str) -> Path:
        return self.cache_dir / f"{case_id}.json"

    def _build_entity_system_prompt(self) -> str:
        return (
            "你是交通事故案例实体抽取器。"
            "请严格依据输入文本抽取实体，不要虚构。"
            "输出必须是 JSON 对象。"
            "事件实体优先抽取细粒度场景词，不要优先使用过于宽泛的词。"
            "例如优先使用‘危化品泄漏’‘桥梁坍塌’‘车辆侧翻’‘道路积水’等具体事件，而非‘交通事故’。"
            "对于事件识别，请结合候选事件判断：当前案例事件是已有事件别名，还是应新增的更具体事件。"
            "expanded_event_candidate_ids 可以填写多个，表示后续查询时要一并扩展命中的已有事件。"
            "实体类型只能使用：Event、Action、Department、Resource。"
            "禁止输出任何不在上述白名单内的实体。"
            "若信息不明确，可留空或不输出，不要猜测。"
        )

    def _build_entity_user_prompt(self, case: CaseRecord) -> str:
        event_candidates = self.ontology_catalog.rank_event_candidates(
            f"{case.title} {case.accident_text} {case.consequence_text}",
            top_k=10,
        )
        payload = {
            "case_id": case.case_id,
            "title": case.title,
            "accident_text": case.accident_text,
            "consequence_text": case.consequence_text,
            "measure_text": case.measure_text,
            "event_candidates": event_candidates,
            "output_schema": {
                "summary": "string",
                "confidence": 0.0,
                "event": {
                    "name": "string",
                    "evidence": "string",
                    "confidence": 0.0,
                },
                "event_resolution": {
                    "primary_event_candidate_id": "string",
                    "primary_event_candidate_name": "string",
                    "expanded_event_candidate_ids": ["string"],
                    "expanded_event_candidate_names": ["string"],
                    "should_create_new_event": False,
                    "new_event_name": "string",
                    "reason": "string"
                },
                "entities": [
                    {
                        "name": "string",
                        "entity_type": "Event|Action|Department|Resource",
                        "evidence": "string",
                        "confidence": 0.0,
                    }
                ],
                "casualties": {
                    "deaths": None,
                    "injuries": None,
                    "missing": None,
                    "unknown": True,
                },
                "legal_references": ["string"],
                "quality_notes": ["string"],
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _build_relation_system_prompt(self) -> str:
        return (
            "你是交通事故案例关系抽取器。"
            "你只能在给定实体清单内部建立关系，不能新增实体。"
            "每条关系必须引用 source_temp_id 和 target_temp_id。"
            "关系类型只能使用：CAUSES、IMPLEMENTED_BY、REQUIRES、TRIGGERS。"
            "关系方向必须符合：CAUSES(Event->Event)、TRIGGERS(Event->Action)、REQUIRES(Action->Resource)、IMPLEMENTED_BY(Action->Department)。"
            "不要输出任何不在白名单中的关系类型或方向。"
            "输出必须是 JSON 对象。"
            "若信息不明确，可不输出关系，不要猜测。"
        )

    def _build_relation_retry_system_prompt(self) -> str:
        return (
            "你是交通事故案例关系补抽器。"
            "目标是补齐遗漏关系，但仍然只能使用给定实体，不得新增实体。"
            "每条关系必须引用 source_temp_id 和 target_temp_id。"
            "关系类型只能使用：CAUSES、IMPLEMENTED_BY、REQUIRES、TRIGGERS。"
            "关系方向必须符合：CAUSES(Event->Event)、TRIGGERS(Event->Action)、REQUIRES(Action->Resource)、IMPLEMENTED_BY(Action->Department)。"
            "优先根据后果和措施文本建立关系。"
            "如果存在 Event 与 Action，请至少给出一条 TRIGGERS。"
            "输出必须是 JSON 对象。"
            "若确实无法建立任何合法关系，relations 输出空数组并在 quality_notes 写明原因。"
        )

    def _build_relation_user_prompt(self, case: CaseRecord, entities: List[Dict[str, Any]]) -> str:
        payload = {
            "case_id": case.case_id,
            "title": case.title,
            "accident_text": case.accident_text,
            "consequence_text": case.consequence_text,
            "measure_text": case.measure_text,
            "entity_catalog": [
                {
                    "entity_temp_id": str(item.get("entity_temp_id") or "").strip(),
                    "name": str(item.get("name") or "").strip(),
                    "entity_type": str(item.get("entity_type") or "").strip(),
                }
                for item in entities
            ],
            "output_schema": {
                "summary": "string",
                "confidence": 0.0,
                "relations": [
                    {
                        "source_temp_id": "string",
                        "source": "string",
                        "source_type": "Event|Action|Department|Resource",
                        "relation": "CAUSES|IMPLEMENTED_BY|REQUIRES|TRIGGERS",
                        "target_temp_id": "string",
                        "target": "string",
                        "target_type": "Event|Action|Department|Resource",
                        "evidence": "string",
                        "confidence": 0.0,
                    }
                ],
                "quality_notes": ["string"],
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _build_relation_fallback(self, case: CaseRecord, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events = [item for item in entities if str(item.get("entity_type") or "").strip() == "Event"]
        actions = [item for item in entities if str(item.get("entity_type") or "").strip() == "Action"]
        resources = [item for item in entities if str(item.get("entity_type") or "").strip() == "Resource"]
        departments = [item for item in entities if str(item.get("entity_type") or "").strip() == "Department"]

        if not events:
            return []

        fallback_relations: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        def add_relation(source_entity: Dict[str, Any], relation_type: str, target_entity: Dict[str, Any], evidence: str, confidence: float = 0.62) -> None:
            source_temp_id = str(source_entity.get("entity_temp_id") or "").strip()
            target_temp_id = str(target_entity.get("entity_temp_id") or "").strip()
            if not source_temp_id or not target_temp_id:
                return
            key = (source_temp_id, relation_type, target_temp_id)
            if key in seen:
                return
            seen.add(key)
            fallback_relations.append(
                {
                    "source": str(source_entity.get("name") or "").strip(),
                    "source_type": str(source_entity.get("entity_type") or "").strip(),
                    "source_temp_id": source_temp_id,
                    "source_final_id": source_entity.get("final_entity_id", source_entity.get("normalized_id", "")),
                    "source_final_name": source_entity.get("final_entity_name", source_entity.get("name", "")),
                    "source_query_event_ids": source_entity.get("query_event_ids", []),
                    "relation": relation_type,
                    "target": str(target_entity.get("name") or "").strip(),
                    "target_type": str(target_entity.get("entity_type") or "").strip(),
                    "target_temp_id": target_temp_id,
                    "target_final_id": target_entity.get("final_entity_id", target_entity.get("normalized_id", "")),
                    "target_final_name": target_entity.get("final_entity_name", target_entity.get("name", "")),
                    "target_query_event_ids": target_entity.get("query_event_ids", []),
                    "evidence": str(evidence or "").strip(),
                    "confidence": float(confidence),
                    "quality_risk_level": "MEDIUM",
                    "quality_risk_reason": "relation_fallback_inferred",
                }
            )

        primary_event = next((item for item in events if not is_generic_event_name(str(item.get("name") or ""))), events[0])
        measure_text = str(case.measure_text or "")
        consequence_text = str(case.consequence_text or "")

        if actions:
            for action in actions:
                action_name = str(action.get("name") or "").strip()
                action_evidence = str(action.get("evidence") or "").strip()
                evidence = action_evidence or measure_text or case.raw_block

                matched_department = next(
                    (
                        dept for dept in departments
                        if str(dept.get("name") or "").strip() and str(dept.get("name") or "").strip() in (action_evidence or measure_text)
                    ),
                    None,
                )
                if matched_department:
                    add_relation(action, "IMPLEMENTED_BY", matched_department, evidence)

                matched_resource = next(
                    (
                        resource for resource in resources
                        if str(resource.get("name") or "").strip() and str(resource.get("name") or "").strip() in (action_evidence or measure_text)
                    ),
                    None,
                )
                if matched_resource:
                    add_relation(action, "REQUIRES", matched_resource, evidence)

                add_relation(primary_event, "TRIGGERS", action, evidence)

        if len(events) >= 2:
            secondary_event = next((item for item in events if item is not primary_event), None)
            if secondary_event:
                cause_evidence = consequence_text or str(secondary_event.get("evidence") or "").strip() or case.raw_block
                add_relation(primary_event, "CAUSES", secondary_event, cause_evidence, confidence=0.60)

        return fallback_relations

    def _normalize_entities(self, case: CaseRecord, payload: Dict[str, Any]) -> Dict[str, Any]:
        entities = payload.get("entities") or []
        event = payload.get("event") or {}
        event_resolution = payload.get("event_resolution") or {}
        legal_references = clean_string_list(payload.get("legal_references"))
        quality_notes = clean_string_list(payload.get("quality_notes"))

        processed_entities: List[Dict[str, Any]] = []
        seen_entity_keys: set[tuple[str, str]] = set()

        if event.get("name"):
            event_entity = {
                "name": str(event.get("name") or "").strip(),
                "entity_type": "Event",
                "evidence": str(event.get("evidence") or case.accident_text or case.title).strip(),
                "confidence": event.get("confidence", payload.get("confidence", 0.0)),
            }
            entities = [event_entity] + list(entities)

        ranked_event_candidates = self.ontology_catalog.rank_event_candidates(
            f"{case.title} {case.accident_text} {case.consequence_text}",
            top_k=10,
        )

        for entity in entities:
            name = str(entity.get("name") or "").strip()
            entity_type = str(entity.get("entity_type") or "").strip()
            if not name or not entity_type:
                continue
            if entity_type not in ALLOWED_EXTRACTION_ENTITY_TYPES:
                continue

            key = (entity_type, name)
            if key in seen_entity_keys:
                continue
            seen_entity_keys.add(key)

            if entity_type == "Event":
                resolution = self.ontology_catalog.resolve_event_hits(
                    name,
                    resolution_payload=event_resolution if not processed_entities else {},
                    fallback_candidates=ranked_event_candidates,
                )
                final_entity_id = resolution.get("primary_event_id", "")
                final_entity_name = resolution.get("primary_event_name", name)
                normalized_id = final_entity_id if not resolution.get("is_new_event") else ""
                normalized_name = final_entity_name if not resolution.get("is_new_event") else ""
                normalized_score = float(resolution.get("primary_match_score", 0.0) or 0.0)
                matched_alias = resolution.get("matched_alias", "")
                resolution_type = resolution.get("resolution_type", "unresolved")
                is_new_entity = bool(resolution.get("is_new_event"))
                expanded_event_ids = clean_string_list(resolution.get("expanded_event_ids"))
                expanded_event_names = clean_string_list(resolution.get("expanded_event_names"))
                query_event_ids = clean_string_list(resolution.get("query_event_ids"))
                query_event_names = clean_string_list(resolution.get("query_event_names"))
                alias_patch_target_ids = clean_string_list(resolution.get("alias_patch_target_ids"))
                alias_patch_target_names = clean_string_list(
                    self.ontology_catalog.get_node_name(node_id) for node_id in alias_patch_target_ids
                )
            else:
                resolution = self.ontology_catalog.resolve_entity(name, entity_type)
                final_entity_id = resolution.get("final_entity_id", "")
                final_entity_name = resolution.get("final_entity_name", name)
                normalized_id = final_entity_id if not resolution.get("is_new_entity") else ""
                normalized_name = final_entity_name if not resolution.get("is_new_entity") else ""
                normalized_score = float(resolution.get("normalized_score", 0.0) or 0.0)
                matched_alias = resolution.get("matched_alias", "")
                resolution_type = resolution.get("resolution_type", "new_entity")
                is_new_entity = bool(resolution.get("is_new_entity"))
                expanded_event_ids = []
                expanded_event_names = []
                query_event_ids = []
                query_event_names = []
                alias_patch_target_ids = []
                alias_patch_target_names = []

            entity_temp_id = make_entity_temp_id(case.case_id, entity_type, name, len(processed_entities) + 1)
            entity_confidence = float(entity.get("confidence") or payload.get("confidence") or 0.0)
            role_ok, role_risk_reason = evaluate_entity_role_quality(
                entity_type=entity_type,
                name=name,
                evidence=str(entity.get("evidence") or "").strip(),
            )
            if not role_ok and entity_confidence < 0.75:
                continue

            entity_record = {
                "entity_temp_id": entity_temp_id,
                "name": name,
                "entity_type": entity_type,
                "evidence": str(entity.get("evidence") or "").strip(),
                "confidence": entity_confidence,
                "normalized_id": normalized_id,
                "normalized_name": normalized_name,
                "normalized_score": normalized_score,
                "matched_alias": matched_alias,
                "resolution_type": resolution_type,
                "final_entity_id": final_entity_id,
                "final_entity_name": final_entity_name,
                "is_new_entity": is_new_entity,
                "primary_event_id": final_entity_id if entity_type == "Event" else "",
                "primary_event_name": final_entity_name if entity_type == "Event" else "",
                "expanded_event_ids": expanded_event_ids,
                "expanded_event_names": expanded_event_names,
                "query_event_ids": query_event_ids,
                "query_event_names": query_event_names,
                "alias_patch_target_ids": alias_patch_target_ids,
                "alias_patch_target_names": alias_patch_target_names,
                "quality_risk_level": "MEDIUM" if (not role_ok and role_risk_reason) else "",
                "quality_risk_reason": role_risk_reason,
            }
            processed_entities.append(entity_record)

        specific_event_exists = any(
            item.get("entity_type") == "Event" and not is_generic_event_name(str(item.get("name") or ""))
            for item in processed_entities
        )
        if specific_event_exists:
            processed_entities = [
                item
                for item in processed_entities
                if item.get("entity_type") != "Event" or not is_generic_event_name(str(item.get("name") or ""))
            ]

        return {
            "entities": processed_entities,
            "summary": str(payload.get("summary") or "").strip(),
            "confidence": float(payload.get("confidence") or 0.0),
            "legal_references": legal_references,
            "quality_notes": quality_notes,
            "casualties": payload.get("casualties") or {},
        }

    def _normalize_relations(
        self,
        relation_payload: Dict[str, Any],
        entities: List[Dict[str, Any]],
        default_confidence: float,
    ) -> List[Dict[str, Any]]:
        entity_by_temp_id: Dict[str, Dict[str, Any]] = {
            str(item.get("entity_temp_id") or "").strip(): item
            for item in entities
            if str(item.get("entity_temp_id") or "").strip()
        }
        entity_by_name_type: Dict[tuple[str, str], Dict[str, Any]] = {
            (str(item.get("entity_type") or "").strip(), str(item.get("name") or "").strip()): item
            for item in entities
        }

        processed_relations: List[Dict[str, Any]] = []
        seen_relation_keys: set[tuple[str, str, str]] = set()

        for relation in relation_payload.get("relations") or []:
            source_temp_id = str(relation.get("source_temp_id") or "").strip()
            target_temp_id = str(relation.get("target_temp_id") or "").strip()

            source_entity = entity_by_temp_id.get(source_temp_id, {})
            target_entity = entity_by_temp_id.get(target_temp_id, {})

            if not source_entity or not target_entity:
                source_name = str(relation.get("source") or "").strip()
                target_name = str(relation.get("target") or "").strip()
                source_type = str(relation.get("source_type") or "").strip()
                target_type = str(relation.get("target_type") or "").strip()
                source_entity = source_entity or entity_by_name_type.get((source_type, source_name), {})
                target_entity = target_entity or entity_by_name_type.get((target_type, target_name), {})
                source_temp_id = str(source_entity.get("entity_temp_id") or "").strip()
                target_temp_id = str(target_entity.get("entity_temp_id") or "").strip()

            if not source_entity or not target_entity:
                continue

            source_name = str(source_entity.get("name") or "").strip()
            target_name = str(target_entity.get("name") or "").strip()
            source_type = str(source_entity.get("entity_type") or "").strip()
            target_type = str(target_entity.get("entity_type") or "").strip()

            relation_type, should_swap = normalize_extraction_relation(
                raw_relation_type=str(relation.get("relation") or "").strip().upper(),
                source_type=source_type,
                target_type=target_type,
            )
            if not relation_type or relation_type not in ALLOWED_EXTRACTION_RELATION_TYPES:
                continue

            if should_swap:
                source_entity, target_entity = target_entity, source_entity
                source_temp_id, target_temp_id = target_temp_id, source_temp_id
                source_name, target_name = target_name, source_name
                source_type, target_type = target_type, source_type

            key = (source_temp_id, relation_type, target_temp_id)
            if key in seen_relation_keys:
                continue
            seen_relation_keys.add(key)

            relation_confidence = float(relation.get("confidence") or default_confidence or 0.0)
            relation_evidence = str(relation.get("evidence") or "").strip()
            is_valid_relation, risk_level, risk_reason = evaluate_relation_quality(
                source_name=source_name,
                target_name=target_name,
                relation_type=relation_type,
                evidence=relation_evidence,
                confidence=relation_confidence,
            )
            if not is_valid_relation:
                continue

            processed_relations.append(
                {
                    "source": source_name,
                    "source_type": source_type,
                    "source_temp_id": source_temp_id,
                    "source_final_id": source_entity.get("final_entity_id", source_entity.get("normalized_id", "")),
                    "source_final_name": source_entity.get("final_entity_name", source_name),
                    "source_query_event_ids": source_entity.get("query_event_ids", []),
                    "relation": relation_type,
                    "target": target_name,
                    "target_type": target_type,
                    "target_temp_id": target_temp_id,
                    "target_final_id": target_entity.get("final_entity_id", target_entity.get("normalized_id", "")),
                    "target_final_name": target_entity.get("final_entity_name", target_name),
                    "target_query_event_ids": target_entity.get("query_event_ids", []),
                    "evidence": relation_evidence,
                    "confidence": relation_confidence,
                    "quality_risk_level": risk_level,
                    "quality_risk_reason": risk_reason,
                }
            )

        linked_entity_temp_ids: set[str] = set()
        for relation in processed_relations:
            source_temp_id = str(relation.get("source_temp_id") or "").strip()
            target_temp_id = str(relation.get("target_temp_id") or "").strip()
            if source_temp_id:
                linked_entity_temp_ids.add(source_temp_id)
            if target_temp_id:
                linked_entity_temp_ids.add(target_temp_id)

        valid_entity_temp_ids = {
            str(entity.get("entity_temp_id") or "").strip()
            for entity in entities
            if entity.get("entity_type") == "Event" or str(entity.get("entity_temp_id") or "") in linked_entity_temp_ids
        }

        return [
            relation
            for relation in processed_relations
            if str(relation.get("source_temp_id") or "").strip() in valid_entity_temp_ids
            and str(relation.get("target_temp_id") or "").strip() in valid_entity_temp_ids
        ]

    def _post_process(
        self,
        case: CaseRecord,
        entity_payload: Dict[str, Any],
        relation_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        entity_result = self._normalize_entities(case, entity_payload)
        processed_entities = entity_result.get("entities", [])

        processed_relations = self._normalize_relations(
            relation_payload=relation_payload,
            entities=processed_entities,
            default_confidence=float(relation_payload.get("confidence") or entity_result.get("confidence") or 0.0),
        )

        if not processed_relations:
            fallback_relations = self._build_relation_fallback(case, processed_entities)
            if fallback_relations:
                processed_relations = fallback_relations
                relation_payload = {
                    **(relation_payload or {}),
                    "quality_notes": clean_string_list(relation_payload.get("quality_notes", [])) + ["关系抽取为空，已启用规则兜底补抽"],
                }

        linked_entity_temp_ids: set[str] = set()
        for relation in processed_relations:
            source_temp_id = str(relation.get("source_temp_id") or "").strip()
            target_temp_id = str(relation.get("target_temp_id") or "").strip()
            if source_temp_id:
                linked_entity_temp_ids.add(source_temp_id)
            if target_temp_id:
                linked_entity_temp_ids.add(target_temp_id)

        processed_entities = [
            entity
            for entity in processed_entities
            if entity.get("entity_type") == "Event" or str(entity.get("entity_temp_id") or "") in linked_entity_temp_ids
        ]

        return {
            "case_id": case.case_id,
            "title": case.title,
            "source_file": case.source_file,
            "summary": str(entity_result.get("summary") or relation_payload.get("summary") or "").strip(),
            "confidence": float(relation_payload.get("confidence") or entity_result.get("confidence") or 0.0),
            "event_resolution": {
                "primary_event_id": next((item.get("primary_event_id", "") for item in processed_entities if item.get("entity_type") == "Event"), ""),
                "expanded_event_ids": next((item.get("expanded_event_ids", []) for item in processed_entities if item.get("entity_type") == "Event"), []),
            },
            "entities": processed_entities,
            "relations": processed_relations,
            "legal_references": entity_result.get("legal_references", []),
            "quality_notes": clean_string_list(entity_result.get("quality_notes", [])) + clean_string_list(relation_payload.get("quality_notes", [])),
            "casualties": entity_result.get("casualties") or {},
            "raw_case": {
                "accident_text": case.accident_text,
                "consequence_text": case.consequence_text,
                "measure_text": case.measure_text,
            },
        }

    def _extract_relation_payload(self, case: CaseRecord, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(entities) < 2:
            return {"relations": [], "summary": "", "confidence": 0.0, "quality_notes": ["实体数量不足，跳过关系抽取"]}

        response = generate_json_response(
            model=self.model,
            system_prompt=self._build_relation_system_prompt(),
            user_content=self._build_relation_user_prompt(case, entities),
            timeout_seconds=self.timeout_seconds,
        )
        payload = extract_json_object(str(response.get("content") or ""))
        if not payload.get("relations"):
            retry_response = generate_json_response(
                model=self.model,
                system_prompt=self._build_relation_retry_system_prompt(),
                user_content=self._build_relation_user_prompt(case, entities),
                timeout_seconds=self.timeout_seconds,
            )
            payload = extract_json_object(str(retry_response.get("content") or ""))
        return payload

    def extract_case(self, case: CaseRecord, force: bool = False) -> Dict[str, Any]:
        cache_path = self._cache_path(case.case_id)
        if self.use_cache and cache_path.exists() and not force:
            return json.loads(cache_path.read_text(encoding="utf-8", errors="ignore"))

        entity_response = generate_json_response(
            model=self.model,
            system_prompt=self._build_entity_system_prompt(),
            user_content=self._build_entity_user_prompt(case),
            timeout_seconds=self.timeout_seconds,
        )
        entity_payload = extract_json_object(str(entity_response.get("content") or ""))
        entity_result = self._normalize_entities(case, entity_payload)
        relation_payload = self._extract_relation_payload(case, entity_result.get("entities", []))
        processed = self._post_process(case, entity_payload, relation_payload)
        cache_path.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")
        return processed


def write_csv(file_path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    ensure_directory(file_path.parent)
    with file_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(file_path: Path, items: List[Dict[str, Any]]) -> None:
    ensure_directory(file_path.parent)
    with file_path.open("w", encoding="utf-8") as output_file:
        for item in items:
            output_file.write(json.dumps(item, ensure_ascii=False) + "\n")


def resolve_input_path(input_arg: str) -> Path:
    candidate = Path(input_arg)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()
