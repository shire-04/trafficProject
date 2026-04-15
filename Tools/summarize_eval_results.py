import argparse
import csv
from collections import defaultdict
from pathlib import Path

AGG_FIELDS = [
    "latency_ms",
    "approved_like",
    "executability_score",
    "safety_score",
    "constraint_alignment_score",
    "evidence_grounding_score",
    "total_score",
    "constraint_coverage",
    "critical_miss_rate",
    "has_forbidden_action",
    "critical_action_missed",
    "missing_actions_count",
    "violated_constraints_count",
    "evidence_hit_count",
]


def _to_float(value: str) -> float:
    try:
        return float(str(value).strip())
    except ValueError:
        return 0.0


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总实验结果 CSV，输出 group 级统计")
    parser.add_argument("input", help="逐条结果 CSV 路径")
    parser.add_argument("--output-csv", default="", help="汇总 CSV 输出路径")
    parser.add_argument("--output-md", default="", help="汇总 Markdown 输出路径")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    rows = _load_rows(input_path)
    if not rows:
        raise ValueError("输入 CSV 为空")

    group_map: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("group_id", ""),
            row.get("retrieval_mode", ""),
            row.get("agent_mode", ""),
        )
        group_map[key].append(row)

    summary_rows: list[dict[str, str | float | int]] = []
    for key, values in group_map.items():
        group_id, retrieval_mode, agent_mode = key
        summary = {
            "group_id": group_id,
            "retrieval_mode": retrieval_mode,
            "agent_mode": agent_mode,
            "sample_count": len(values),
        }
        for field in AGG_FIELDS:
            summary[f"avg_{field}"] = round(_avg([_to_float(item.get(field, "0")) for item in values]), 4)
        summary_rows.append(summary)

    summary_rows.sort(key=lambda item: (str(item["group_id"]), str(item["retrieval_mode"]), str(item["agent_mode"])))

    output_csv = Path(args.output_csv) if args.output_csv else input_path.with_name(input_path.stem + "_summary.csv")
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    csv_fields = ["group_id", "retrieval_mode", "agent_mode", "sample_count"] + [f"avg_{f}" for f in AGG_FIELDS]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)

    output_md = Path(args.output_md) if args.output_md else input_path.with_name(input_path.stem + "_summary.md")
    md_lines = [
        "# 实验汇总",
        "",
        "| group_id | retrieval_mode | agent_mode | sample_count | avg_total_score | avg_approved_like | avg_critical_miss_rate | avg_has_forbidden_action | avg_constraint_coverage | avg_evidence_grounding_score |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        md_lines.append(
            "| {group_id} | {retrieval_mode} | {agent_mode} | {sample_count} | {avg_total_score} | {avg_approved_like} | {avg_critical_miss_rate} | {avg_has_forbidden_action} | {avg_constraint_coverage} | {avg_evidence_grounding_score} |".format(
                **row
            )
        )

    output_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"input={input_path}")
    print(f"groups={len(summary_rows)}")
    print(f"summary_csv={output_csv}")
    print(f"summary_md={output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
