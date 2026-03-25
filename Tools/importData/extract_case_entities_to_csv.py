import argparse
import json
from pathlib import Path
from typing import List

from case_extraction_common import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    CaseKnowledgeExtractor,
    load_case_records,
    resolve_input_path,
    write_csv,
    write_jsonl,
)


ENTITY_FIELDNAMES = [
    "case_id",
    "case_title",
    "source_file",
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
    "query_event_ids",
    "query_event_names",
    "alias_patch_target_ids",
    "alias_patch_target_names",
    "normalized_id",
    "normalized_name",
    "normalized_score",
    "matched_alias",
    "extraction_confidence",
    "evidence",
    "review_status",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从事故案例 TXT 中抽取实体并写入 CSV。")
    parser.add_argument("input_path", nargs="?", default="data_raw/案例.txt", help="输入 txt 文件路径")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--output-file", default="case_entities.csv", help="实体 CSV 文件名")
    parser.add_argument("--jsonl-file", default="case_structured.jsonl", help="结构化 JSONL 文件名")
    parser.add_argument("--model", default="", help="可选：覆盖默认模型")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 个案例，0 表示全部")
    parser.add_argument("--force", action="store_true", help="忽略缓存，强制重新抽取")
    parser.add_argument("--dry-run", action="store_true", help="只解析案例块，不调用 LLM")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = resolve_input_path(args.input_path)
    output_dir = Path(args.output_dir)

    cases = load_case_records(input_path)
    if args.limit and args.limit > 0:
        cases = cases[:args.limit]

    if args.dry_run:
        print(json.dumps({
            "input_path": str(input_path),
            "case_count": len(cases),
            "first_case_title": cases[0].title if cases else "",
        }, ensure_ascii=False, indent=2))
        return 0

    extractor = CaseKnowledgeExtractor(output_dir=output_dir, model=args.model or None)

    rows: List[dict] = []
    structured_items: List[dict] = []
    for case in cases:
        structured = extractor.extract_case(case, force=args.force)
        structured_items.append(structured)
        for entity in structured.get("entities", []):
            normalized_score = float(entity.get("normalized_score") or 0.0)
            review_status = "REVIEW_REQUIRED"
            if normalized_score >= 0.8:
                review_status = "AUTO_HIGH"
            elif normalized_score >= 0.45:
                review_status = "AUTO_MEDIUM"

            rows.append(
                {
                    "case_id": structured.get("case_id", ""),
                    "case_title": structured.get("title", ""),
                    "source_file": structured.get("source_file", ""),
                    "entity_temp_id": entity.get("entity_temp_id", ""),
                    "entity_name": entity.get("name", ""),
                    "entity_type": entity.get("entity_type", ""),
                    "resolution_type": entity.get("resolution_type", ""),
                    "final_entity_id": entity.get("final_entity_id", ""),
                    "final_entity_name": entity.get("final_entity_name", ""),
                    "is_new_entity": str(bool(entity.get("is_new_entity"))).lower(),
                    "primary_event_id": entity.get("primary_event_id", ""),
                    "primary_event_name": entity.get("primary_event_name", ""),
                    "expanded_event_ids": "|".join(entity.get("expanded_event_ids", []) or []),
                    "expanded_event_names": "|".join(entity.get("expanded_event_names", []) or []),
                    "query_event_ids": "|".join(entity.get("query_event_ids", []) or []),
                    "query_event_names": "|".join(entity.get("query_event_names", []) or []),
                    "alias_patch_target_ids": "|".join(entity.get("alias_patch_target_ids", []) or []),
                    "alias_patch_target_names": "|".join(entity.get("alias_patch_target_names", []) or []),
                    "normalized_id": entity.get("normalized_id", ""),
                    "normalized_name": entity.get("normalized_name", ""),
                    "normalized_score": f"{normalized_score:.4f}",
                    "matched_alias": entity.get("matched_alias", ""),
                    "extraction_confidence": f"{float(entity.get('confidence') or 0.0):.4f}",
                    "evidence": entity.get("evidence", ""),
                    "review_status": review_status,
                }
            )

    write_csv(output_dir / args.output_file, ENTITY_FIELDNAMES, rows)
    write_jsonl(output_dir / args.jsonl_file, structured_items)

    print(json.dumps({
        "input_path": str(input_path),
        "output_csv": str(output_dir / args.output_file),
        "output_jsonl": str(output_dir / args.jsonl_file),
        "case_count": len(cases),
        "entity_row_count": len(rows),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
