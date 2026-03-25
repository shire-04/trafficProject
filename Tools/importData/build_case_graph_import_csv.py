import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from case_extraction_common import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    ENTITY_LABELS_MAP,
    ENTITY_TYPE_CN_MAP,
    OntologyCatalog,
    ensure_directory,
    make_generated_node_id,
    parse_multi_value,
    resolve_input_path,
)


ALLOWED_ENTITY_TYPES = {"Event", "Action", "Department", "Resource"}
ALLOWED_RELATIONS = {"CAUSES", "IMPLEMENTED_BY", "REQUIRES", "TRIGGERS"}

RELATION_CN_MAP = {
    "CAUSES": "引发",
    "IMPLEMENTED_BY": "实施主体",
    "REQUIRES": "调用",
    "TRIGGERS": "触发",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将案例抽取结果整理为正式导入 CSV 与别名补丁。")
    parser.add_argument("input_jsonl", nargs="?", default=str(DEFAULT_OUTPUT_DIR / "case_structured.jsonl"), help="结构化 JSONL 输入路径")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--nodes-file", default="case_new_nodes.csv", help="新增节点 CSV 文件名")
    parser.add_argument("--relations-file", default="case_new_relationships.csv", help="新增关系 CSV 文件名")
    parser.add_argument("--aliases-file", default="event_aliases_patch.csv", help="事件别名补丁 CSV 文件名")
    parser.add_argument("--audit-file", default="normalization_audit.csv", help="规范化审计 CSV 文件名")
    parser.add_argument("--quality-file", default="quality_gate_report.csv", help="质量闸门报告 CSV 文件名")
    parser.add_argument("--max-empty-relation-ratio", type=float, default=0.30, help="允许的空关系案例占比阈值（0~1）")
    parser.add_argument("--fail-on-empty-relation-ratio", action="store_true", help="当空关系占比超过阈值时返回非0状态码")
    return parser


def load_items(jsonl_path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8", errors="ignore") as input_file:
        for raw_line in input_file:
            line = raw_line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def normalize_entity(entity: Dict[str, Any], catalog: OntologyCatalog) -> Dict[str, Any]:
    entity_type = str(entity.get("entity_type") or "").strip()
    entity_name = str(entity.get("name") or "").strip()
    final_entity_id = str(entity.get("final_entity_id") or entity.get("normalized_id") or "").strip()
    final_entity_name = str(entity.get("final_entity_name") or entity.get("normalized_name") or entity_name).strip()
    resolution_type = str(entity.get("resolution_type") or "").strip()
    is_new_entity = bool(entity.get("is_new_entity"))

    existing_by_id = catalog.get_node_by_id(final_entity_id) if final_entity_id else {}
    existing_by_id_type = str(existing_by_id.get("entity_type") or "").strip()

    if final_entity_id and not is_new_entity:
        if not existing_by_id or existing_by_id_type != entity_type:
            final_entity_id = ""
            final_entity_name = entity_name
            resolution_type = "existing_id_invalid_relinked"

    if final_entity_id and is_new_entity and existing_by_id and existing_by_id_type == entity_type:
        final_entity_name = str(existing_by_id.get("name") or final_entity_name or entity_name).strip()
        resolution_type = resolution_type or "existing_match"
        is_new_entity = False

    if not final_entity_id and entity_name and entity_type:
        existing = catalog.lookup_existing_node(entity_name, entity_type)
        if existing.get("node_id"):
            final_entity_id = existing.get("node_id", "")
            final_entity_name = existing.get("name", entity_name)
            resolution_type = resolution_type or "existing_match"
            is_new_entity = False
        else:
            final_entity_id = make_generated_node_id(entity_type, entity_name)
            final_entity_name = final_entity_name or entity_name
            resolution_type = resolution_type or "new_entity"
            is_new_entity = True

    primary_event_id = str(entity.get("primary_event_id") or "").strip()
    primary_event_name = str(entity.get("primary_event_name") or "").strip()
    if entity_type == "Event" and not primary_event_id:
        primary_event_id = final_entity_id
        primary_event_name = final_entity_name

    return {
        **entity,
        "entity_type": entity_type,
        "name": entity_name,
        "final_entity_id": final_entity_id,
        "final_entity_name": final_entity_name,
        "resolution_type": resolution_type,
        "is_new_entity": is_new_entity,
        "primary_event_id": primary_event_id,
        "primary_event_name": primary_event_name,
        "expanded_event_ids": parse_multi_value(entity.get("expanded_event_ids")),
        "expanded_event_names": parse_multi_value(entity.get("expanded_event_names")),
        "alias_patch_target_ids": parse_multi_value(entity.get("alias_patch_target_ids")),
        "alias_patch_target_names": parse_multi_value(entity.get("alias_patch_target_names")),
        "import_eligible": entity_type in ALLOWED_ENTITY_TYPES,
    }


def normalize_relation_type(
    raw_relation_type: str,
    source_type: str,
    target_type: str,
) -> Tuple[str, bool]:
    relation_type = str(raw_relation_type or "").strip().upper()
    source_type = str(source_type or "").strip()
    target_type = str(target_type or "").strip()

    if relation_type == "CAUSES":
        if source_type == "Event" and target_type == "Event":
            return "CAUSES", False
        return "", False

    if relation_type == "TRIGGERS":
        if source_type == "Event" and target_type == "Action":
            return "TRIGGERS", False
        return "", False

    if relation_type == "REQUIRES":
        if source_type == "Action" and target_type == "Resource":
            return "REQUIRES", False
        if source_type == "Resource" and target_type == "Action":
            return "REQUIRES", True
        return "", False

    if relation_type == "IMPLEMENTED_BY":
        if source_type == "Action" and target_type == "Department":
            return "IMPLEMENTED_BY", False
        return "", False

    return "", False


def build_node_row(entity: Dict[str, Any]) -> Dict[str, str]:
    entity_type = str(entity.get("entity_type") or "").strip()
    return {
        "id:ID": str(entity.get("final_entity_id") or "").strip(),
        "name": str(entity.get("final_entity_name") or entity.get("name") or "").strip(),
        "entity_type_cn": ENTITY_TYPE_CN_MAP.get(entity_type, entity_type),
        ":LABEL": ";".join(ENTITY_LABELS_MAP.get(entity_type, [entity_type])),
    }


def build_relation_row(item: Dict[str, Any], relation: Dict[str, Any]) -> Dict[str, str]:
    relation_type = str(relation.get("normalized_relation_type") or "").strip().upper()
    return {
        ":START_ID": str(relation.get("source_final_id") or "").strip(),
        ":END_ID": str(relation.get("target_final_id") or "").strip(),
        "relation_cn": RELATION_CN_MAP.get(relation_type, relation_type),
        ":TYPE": relation_type,
        "case_id": str(item.get("case_id") or "").strip(),
        "case_title": str(item.get("title") or "").strip(),
        "evidence": str(relation.get("evidence") or "").strip(),
        "confidence": f"{float(relation.get('confidence') or 0.0):.4f}",
        "source": "案例抽取导入",
    }


def build_audit_row(item: Dict[str, Any], entity: Dict[str, Any]) -> Dict[str, str]:
    return {
        "case_id": str(item.get("case_id") or "").strip(),
        "case_title": str(item.get("title") or "").strip(),
        "entity_temp_id": str(entity.get("entity_temp_id") or "").strip(),
        "entity_name": str(entity.get("name") or "").strip(),
        "entity_type": str(entity.get("entity_type") or "").strip(),
        "resolution_type": str(entity.get("resolution_type") or "").strip(),
        "final_entity_id": str(entity.get("final_entity_id") or "").strip(),
        "final_entity_name": str(entity.get("final_entity_name") or "").strip(),
        "is_new_entity": str(bool(entity.get("is_new_entity"))).lower(),
        "primary_event_id": str(entity.get("primary_event_id") or "").strip(),
        "primary_event_name": str(entity.get("primary_event_name") or "").strip(),
        "expanded_event_ids": "|".join(entity.get("expanded_event_ids", []) or []),
        "expanded_event_names": "|".join(entity.get("expanded_event_names", []) or []),
        "alias_patch_target_ids": "|".join(entity.get("alias_patch_target_ids", []) or []),
        "alias_patch_target_names": "|".join(entity.get("alias_patch_target_names", []) or []),
        "import_eligible": str(bool(entity.get("import_eligible"))).lower(),
        "import_skip_reason": str(entity.get("import_skip_reason") or ""),
        "evidence": str(entity.get("evidence") or "").strip(),
        "confidence": f"{float(entity.get('confidence') or 0.0):.4f}",
    }


def build_quality_row(
    item: Dict[str, Any],
    object_type: str,
    object_name: str,
    object_role: str,
    risk_level: str,
    risk_reason: str,
    confidence: float,
    evidence: str,
    import_action: str,
) -> Dict[str, str]:
    return {
        "case_id": str(item.get("case_id") or "").strip(),
        "case_title": str(item.get("title") or "").strip(),
        "object_type": str(object_type or "").strip(),
        "object_name": str(object_name or "").strip(),
        "object_role": str(object_role or "").strip(),
        "risk_level": str(risk_level or "").strip(),
        "risk_reason": str(risk_reason or "").strip(),
        "confidence": f"{float(confidence or 0.0):.4f}",
        "evidence": str(evidence or "").strip(),
        "import_action": str(import_action or "").strip(),
    }


def write_csv(output_path: Path, fieldnames: Sequence[str], rows: Sequence[Dict[str, str]]) -> None:
    ensure_directory(output_path.parent)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = build_parser().parse_args()
    input_path = resolve_input_path(args.input_jsonl)
    output_dir = resolve_input_path(args.output_dir)
    catalog = OntologyCatalog()
    items = load_items(input_path)

    new_nodes: List[Dict[str, str]] = []
    new_relations: List[Dict[str, str]] = []
    alias_patch_rows: List[Dict[str, str]] = []
    audit_rows: List[Dict[str, str]] = []
    quality_rows: List[Dict[str, str]] = []
    seen_node_ids: set[str] = set()
    seen_relation_keys: set[Tuple[str, str, str]] = set()
    seen_alias_keys: set[Tuple[str, str]] = set()
    kept_relation_endpoints: set[str] = set()
    candidate_new_nodes: Dict[str, Dict[str, Any]] = {}
    relation_dropped_count = 0
    disallowed_entity_count = 0
    empty_relation_case_count = 0

    for item in items:
        if not (item.get("relations") or []):
            empty_relation_case_count += 1
            quality_rows.append(
                build_quality_row(
                    item=item,
                    object_type="case",
                    object_name=str(item.get("title") or "").strip(),
                    object_role="case",
                    risk_level="HIGH",
                    risk_reason="case_empty_relations",
                    confidence=float(item.get("confidence") or 0.0),
                    evidence=str((item.get("raw_case") or {}).get("accident_text") or "").strip(),
                    import_action="needs_reextract",
                )
            )

        entity_index: Dict[str, Dict[str, Any]] = {}
        entity_index_by_name_type: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for raw_entity in item.get("entities") or []:
            entity = normalize_entity(raw_entity, catalog)
            if not entity.get("import_eligible"):
                entity["import_skip_reason"] = "disallowed_entity_type"
                disallowed_entity_count += 1
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="entity",
                        object_name=str(entity.get("name") or "").strip(),
                        object_role=str(entity.get("entity_type") or "").strip(),
                        risk_level="HIGH",
                        risk_reason="disallowed_entity_type",
                        confidence=float(entity.get("confidence") or 0.0),
                        evidence=str(entity.get("evidence") or "").strip(),
                        import_action="dropped",
                    )
                )
            entity_index[str(entity.get("entity_temp_id") or "").strip()] = entity
            entity_index_by_name_type[(str(entity.get("name") or "").strip(), str(entity.get("entity_type") or "").strip())] = entity
            audit_rows.append(build_audit_row(item, entity))

            quality_risk_reason = str(entity.get("quality_risk_reason") or "").strip()
            if quality_risk_reason:
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="entity",
                        object_name=str(entity.get("name") or "").strip(),
                        object_role=str(entity.get("entity_type") or "").strip(),
                        risk_level=str(entity.get("quality_risk_level") or "MEDIUM").strip(),
                        risk_reason=quality_risk_reason,
                        confidence=float(entity.get("confidence") or 0.0),
                        evidence=str(entity.get("evidence") or "").strip(),
                        import_action="kept_with_risk",
                    )
                )

            final_entity_id = str(entity.get("final_entity_id") or "").strip()
            if bool(entity.get("is_new_entity")) and final_entity_id and entity.get("import_eligible"):
                candidate_new_nodes[final_entity_id] = entity

            if entity.get("entity_type") == "Event":
                alias_name = str(entity.get("name") or "").strip()
                alias_target_ids = entity.get("alias_patch_target_ids", []) or []
                alias_target_names = entity.get("alias_patch_target_names", []) or []

                if str(entity.get("resolution_type") or "").startswith("alias_match") and alias_name != entity.get("primary_event_name", ""):
                    alias_target_ids = alias_target_ids or [str(entity.get("primary_event_id") or "").strip()]
                    alias_target_names = alias_target_names or [str(entity.get("primary_event_name") or "").strip()]

                for idx, target_id in enumerate(alias_target_ids):
                    target_id = str(target_id or "").strip()
                    target_name = alias_target_names[idx] if idx < len(alias_target_names) else catalog.get_node_name(target_id)
                    alias_key = (target_id, alias_name)
                    if not target_id or not alias_name or alias_key in seen_alias_keys:
                        continue
                    if catalog.event_alias_exists(target_id, alias_name):
                        continue
                    alias_patch_rows.append(
                        {
                            "entity_id": target_id,
                            "entity_name": target_name,
                            "entity_type": "Event",
                            "alias": alias_name,
                            "alias_type": "scene_expression",
                            "source": f"案例抽取自动补充:{str(item.get('case_id') or '').strip()}",
                        }
                    )
                    seen_alias_keys.add(alias_key)

        for relation in item.get("relations") or []:
            source_name = str(relation.get("source") or "").strip()
            target_name = str(relation.get("target") or "").strip()
            source_type = str(relation.get("source_type") or "").strip()
            target_type = str(relation.get("target_type") or "").strip()

            normalized_relation_type, should_swap = normalize_relation_type(
                raw_relation_type=str(relation.get("relation") or ""),
                source_type=source_type,
                target_type=target_type,
            )
            if not normalized_relation_type or normalized_relation_type not in ALLOWED_RELATIONS:
                relation_dropped_count += 1
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{str(relation.get('relation') or '').strip()}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level="HIGH",
                        risk_reason="disallowed_relation_type_or_direction",
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="dropped",
                    )
                )
                continue

            source_temp_id = str(relation.get("source_temp_id") or "").strip()
            target_temp_id = str(relation.get("target_temp_id") or "").strip()
            source_entity = entity_index.get(source_temp_id, {}) or entity_index_by_name_type.get((source_name, source_type), {})
            target_entity = entity_index.get(target_temp_id, {}) or entity_index_by_name_type.get((target_name, target_type), {})

            if not source_entity.get("import_eligible") or not target_entity.get("import_eligible"):
                relation_dropped_count += 1
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{normalized_relation_type}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level="HIGH",
                        risk_reason="relation_endpoint_not_import_eligible",
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="dropped",
                    )
                )
                continue

            relation_source_final_id = str(relation.get("source_final_id") or "").strip()
            relation_target_final_id = str(relation.get("target_final_id") or "").strip()
            entity_source_final_id = str(source_entity.get("final_entity_id") or "").strip()
            entity_target_final_id = str(target_entity.get("final_entity_id") or "").strip()

            source_final_id = entity_source_final_id or relation_source_final_id
            target_final_id = entity_target_final_id or relation_target_final_id

            if relation_source_final_id and entity_source_final_id and relation_source_final_id != entity_source_final_id:
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{normalized_relation_type}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level="MEDIUM",
                        risk_reason="relation_source_final_id_mismatch_fixed_by_entity_mapping",
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="kept_with_risk",
                    )
                )

            if relation_target_final_id and entity_target_final_id and relation_target_final_id != entity_target_final_id:
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{normalized_relation_type}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level="MEDIUM",
                        risk_reason="relation_target_final_id_mismatch_fixed_by_entity_mapping",
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="kept_with_risk",
                    )
                )

            if not source_final_id or not target_final_id:
                relation_dropped_count += 1
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{normalized_relation_type}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level="HIGH",
                        risk_reason="relation_endpoint_id_missing",
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="dropped",
                    )
                )
                continue

            if should_swap:
                source_final_id, target_final_id = target_final_id, source_final_id

            source_known = bool(candidate_new_nodes.get(source_final_id)) or bool(catalog.get_node_by_id(source_final_id))
            target_known = bool(candidate_new_nodes.get(target_final_id)) or bool(catalog.get_node_by_id(target_final_id))
            if not source_known or not target_known:
                relation_dropped_count += 1
                missing_side = "both"
                if source_known and not target_known:
                    missing_side = "target"
                elif target_known and not source_known:
                    missing_side = "source"
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{normalized_relation_type}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level="HIGH",
                        risk_reason=f"relation_endpoint_unresolvable_{missing_side}",
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="dropped",
                    )
                )
                continue

            relation_key = (source_final_id, normalized_relation_type, target_final_id)
            if relation_key in seen_relation_keys:
                continue
            seen_relation_keys.add(relation_key)

            relation_risk_reason = str(relation.get("quality_risk_reason") or "").strip()
            if relation_risk_reason:
                quality_rows.append(
                    build_quality_row(
                        item=item,
                        object_type="relation",
                        object_name=f"{source_name}-{normalized_relation_type}-{target_name}",
                        object_role=f"{source_type}->{target_type}",
                        risk_level=str(relation.get("quality_risk_level") or "MEDIUM").strip(),
                        risk_reason=relation_risk_reason,
                        confidence=float(relation.get("confidence") or 0.0),
                        evidence=str(relation.get("evidence") or "").strip(),
                        import_action="kept_with_risk",
                    )
                )

            kept_relation_endpoints.add(source_final_id)
            kept_relation_endpoints.add(target_final_id)
            new_relations.append(
                build_relation_row(
                    item,
                    {
                        **relation,
                        "normalized_relation_type": normalized_relation_type,
                        "source_final_id": source_final_id,
                        "target_final_id": target_final_id,
                    },
                )
            )

    for node_id, entity in candidate_new_nodes.items():
        if node_id in kept_relation_endpoints and node_id not in seen_node_ids:
            new_nodes.append(build_node_row(entity))
            seen_node_ids.add(node_id)

    write_csv(output_dir / args.nodes_file, ["id:ID", "name", "entity_type_cn", ":LABEL"], new_nodes)
    write_csv(
        output_dir / args.relations_file,
        [":START_ID", ":END_ID", "relation_cn", ":TYPE", "case_id", "case_title", "evidence", "confidence", "source"],
        new_relations,
    )
    write_csv(output_dir / args.aliases_file, ["entity_id", "entity_name", "entity_type", "alias", "alias_type", "source"], alias_patch_rows)
    write_csv(
        output_dir / args.audit_file,
        [
            "case_id",
            "case_title",
            "entity_temp_id",
            "entity_name",
            "entity_type",
            "resolution_type",
            "final_entity_id",
            "final_entity_name",
            "is_new_entity",
            "primary_event_id",
            "primary_event_name",
            "expanded_event_ids",
            "expanded_event_names",
            "alias_patch_target_ids",
            "alias_patch_target_names",
            "import_eligible",
            "import_skip_reason",
            "evidence",
            "confidence",
        ],
        audit_rows,
    )
    write_csv(
        output_dir / args.quality_file,
        [
            "case_id",
            "case_title",
            "object_type",
            "object_name",
            "object_role",
            "risk_level",
            "risk_reason",
            "confidence",
            "evidence",
            "import_action",
        ],
        quality_rows,
    )

    total_case_count = len(items)
    empty_relation_ratio = (empty_relation_case_count / total_case_count) if total_case_count else 0.0
    threshold_exceeded = empty_relation_ratio > float(args.max_empty_relation_ratio)

    print(json.dumps({
        "input_jsonl": str(input_path),
        "output_dir": str(output_dir),
        "allowed_entity_types": sorted(ALLOWED_ENTITY_TYPES),
        "allowed_relation_types": sorted(ALLOWED_RELATIONS),
        "new_node_count": len(new_nodes),
        "new_relation_count": len(new_relations),
        "dropped_relation_count": relation_dropped_count,
        "disallowed_entity_count": disallowed_entity_count,
        "event_alias_patch_count": len(alias_patch_rows),
        "audit_row_count": len(audit_rows),
        "quality_row_count": len(quality_rows),
        "total_case_count": total_case_count,
        "empty_relation_case_count": empty_relation_case_count,
        "empty_relation_ratio": round(empty_relation_ratio, 4),
        "max_empty_relation_ratio": float(args.max_empty_relation_ratio),
        "empty_relation_ratio_exceeded": threshold_exceeded,
    }, ensure_ascii=False, indent=2))

    if args.fail_on_empty_relation_ratio and threshold_exceeded:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
