import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_dataset_difficulties(dataset_path: Path, difficulty_field: str = "difficulty") -> dict[str, str]:
    difficulties: dict[str, str] = {}
    with dataset_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            record = json.loads(raw)
            sample_id = str(record.get("sample_id", "")).strip()
            if not sample_id:
                continue
            difficulties[sample_id] = str(record.get(difficulty_field, "unknown")).strip() or "unknown"
    return difficulties


def parse_score(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        return float(text) if text != "" else None
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="按 difficulty 统计 CSV 评测结果的平均分")
    parser.add_argument("--dataset", default="data_clean/评测数据集.jsonl", help="JSONL 评测数据集路径")
    parser.add_argument("--csv", required=True, help="G5 全样本评测结果 CSV 路径")
    parser.add_argument("--difficulty-field", default="difficulty", help="JSONL 中表示难度的字段名")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    csv_path = Path(args.csv)

    if not dataset_path.exists():
        raise FileNotFoundError(f"JSONL 数据集不存在: {dataset_path}")
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    difficulties = load_dataset_difficulties(dataset_path, args.difficulty_field)
    if not difficulties:
        raise ValueError("未能从数据集中加载任何 difficulty 信息")

    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "total_score_sum": 0.0,
        "total_score_count": 0,
        "rule_total_score_sum": 0.0,
        "rule_total_score_count": 0,
        "llm_total_score_sum": 0.0,
        "llm_total_score_count": 0,
        "row_count": 0,
    })
    missing_sample_ids: set[str] = set()

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sample_id = str(row.get("sample_id", "")).strip()
            if not sample_id:
                continue

            difficulty = difficulties.get(sample_id, "unknown")
            if difficulty == "unknown":
                missing_sample_ids.add(sample_id)

            group = stats[difficulty]
            group["row_count"] += 1

            total_value = parse_score(row.get("total_score"))
            rule_value = parse_score(row.get("rule_total_score"))
            llm_value = parse_score(row.get("llm_overall_score"))

            if total_value is None and rule_value is not None and llm_value is not None:
                total_value = (rule_value + llm_value) / 2.0

            if total_value is not None:
                group["total_score_sum"] += total_value
                group["total_score_count"] += 1
            if rule_value is not None:
                group["rule_total_score_sum"] += rule_value
                group["rule_total_score_count"] += 1
            if llm_value is not None:
                group["llm_total_score_sum"] += llm_value
                group["llm_total_score_count"] += 1

    print("difficulty,total_score_avg,rule_total_score_avg,llm_total_score_avg,row_count")
    for difficulty in sorted(stats.keys()):
        group = stats[difficulty]
        total_avg = (
            group["total_score_sum"] / group["total_score_count"]
            if group["total_score_count"] > 0
            else float("nan")
        )
        rule_avg = (
            group["rule_total_score_sum"] / group["rule_total_score_count"]
            if group["rule_total_score_count"] > 0
            else float("nan")
        )
        llm_avg = (
            group["llm_total_score_sum"] / group["llm_total_score_count"]
            if group["llm_total_score_count"] > 0
            else float("nan")
        )
        print(
            f"{difficulty},{total_avg:.6f},{rule_avg:.6f},{llm_avg:.6f},{group['row_count']}"
        )

    if missing_sample_ids:
        print(f"# WARNING: {len(missing_sample_ids)} sample_id 未在数据集中找到，已归为 unknown")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
