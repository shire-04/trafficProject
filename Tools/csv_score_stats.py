import argparse
import csv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 CSV 中 total_score、rule_total_score、llm_overall_score 的平均值")
    parser.add_argument("csv_path", help="CSV 文件路径")
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    sums = {
        "total_score": 0.0,
        "rule_total_score": 0.0,
        "llm_overall_score": 0.0,
    }
    counts = {key: 0 for key in sums}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rule_value = row.get("rule_total_score", "")
            llm_value = row.get("llm_overall_score", "")
            try:
                rule_score = float(rule_value) if rule_value not in (None, "") else None
            except ValueError:
                rule_score = None
            try:
                llm_score = float(llm_value) if llm_value not in (None, "") else None
            except ValueError:
                llm_score = None

            if rule_score is not None:
                sums["rule_total_score"] += rule_score
                counts["rule_total_score"] += 1
            if llm_score is not None:
                sums["llm_overall_score"] += llm_score
                counts["llm_overall_score"] += 1
            if rule_score is not None and llm_score is not None:
                sums["total_score"] += (rule_score + llm_score) / 2.0
                counts["total_score"] += 1

    for key in ["total_score", "rule_total_score", "llm_overall_score"]:
        if counts[key] > 0:
            avg = sums[key] / counts[key]
            print(f"{key}: {avg:.6f} (count={counts[key]})")
        else:
            print(f"{key}: N/A (no valid values)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
