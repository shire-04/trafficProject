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
)


RELATION_FIELDNAMES = [
    "source",
    "source_temp_id",
    "source_final_id",
    "source_final_name",
    "source_query_event_ids",
    "relation",
    "target",
    "target_temp_id",
    "target_final_id",
    "target_final_name",
    "target_query_event_ids",
    "case_id",
    "case_title",
    "source_type",
    "target_type",
    "confidence",
    "evidence",
    "source_file",
    "review_status",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从事故案例 TXT 中抽取关系并写入 CSV。")
    parser.add_argument("input_path", nargs="?", default="data_raw/案例.txt", help="输入 txt 文件路径")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--output-file", default="case_relations.csv", help="关系 CSV 文件名")
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
    for case in cases:
        structured = extractor.extract_case(case, force=args.force)
        for relation in structured.get("relations", []):
            confidence = float(relation.get("confidence") or 0.0)
            review_status = "REVIEW_REQUIRED"
            if confidence >= 0.85:
                review_status = "AUTO_HIGH"
            elif confidence >= 0.6:
                review_status = "AUTO_MEDIUM"

            rows.append(
                {
                    "source": relation.get("source", ""),
                    "source_temp_id": relation.get("source_temp_id", ""),
                    "source_final_id": relation.get("source_final_id", ""),
                    "source_final_name": relation.get("source_final_name", ""),
                    "source_query_event_ids": "|".join(relation.get("source_query_event_ids", []) or []),
                    "relation": relation.get("relation", ""),
                    "target": relation.get("target", ""),
                    "target_temp_id": relation.get("target_temp_id", ""),
                    "target_final_id": relation.get("target_final_id", ""),
                    "target_final_name": relation.get("target_final_name", ""),
                    "target_query_event_ids": "|".join(relation.get("target_query_event_ids", []) or []),
                    "case_id": structured.get("case_id", ""),
                    "case_title": structured.get("title", ""),
                    "source_type": relation.get("source_type", ""),
                    "target_type": relation.get("target_type", ""),
                    "confidence": f"{confidence:.4f}",
                    "evidence": relation.get("evidence", ""),
                    "source_file": structured.get("source_file", ""),
                    "review_status": review_status,
                }
            )

    write_csv(output_dir / args.output_file, RELATION_FIELDNAMES, rows)

    print(json.dumps({
        "input_path": str(input_path),
        "output_csv": str(output_dir / args.output_file),
        "case_count": len(cases),
        "relation_row_count": len(rows),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
