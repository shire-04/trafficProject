import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

DEFAULT_METRICS = [
    "total_score",
    "executability_score",
    "safety_score",
    "llm_executability_score",
    "llm_safety_score",
    "llm_compliance_score",
    "critical_miss_rate",
    "missing_actions_count",
]


def _to_float(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _is_valid_result_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    if path.name.endswith("_summary.csv"):
        return False

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
    except OSError:
        return False

    return {"group_id", "retrieval_mode", "agent_mode", "total_score"}.issubset(fields)


def _iter_result_csvs(input_path: Path) -> Iterable[Path]:
    if input_path.is_file():
        if _is_valid_result_csv(input_path):
            yield input_path
        return

    if not input_path.is_dir():
        return

    for path in sorted(input_path.glob("*.csv")):
        if _is_valid_result_csv(path):
            yield path


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _normalize_bool(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def _build_group_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        str(row.get("group_id", "")).strip(),
        str(row.get("retrieval_mode", "")).strip(),
        str(row.get("agent_mode", "")).strip(),
    )


def _summarize_run(run_label: str, csv_files: list[Path], metrics: list[str]) -> list[dict[str, object]]:
    grouped_rows: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)

    for csv_path in csv_files:
        for row in _load_rows(csv_path):
            key = _build_group_key(row)
            if not all(key):
                continue
            grouped_rows[key].append(row)

    summary_rows: list[dict[str, object]] = []
    for (group_id, retrieval_mode, agent_mode), rows in sorted(grouped_rows.items()):
        item: dict[str, object] = {
            "run_label": run_label,
            "group_id": group_id,
            "retrieval_mode": retrieval_mode,
            "agent_mode": agent_mode,
            "sample_count": len(rows),
            "llm_judge_success_rate": round(
                _avg([1.0 if _normalize_bool(row.get("llm_judge_success", "")) else 0.0 for row in rows]),
                4,
            ),
        }
        for metric in metrics:
            item[f"avg_{metric}"] = round(_avg([_to_float(row.get(metric, 0.0)) for row in rows]), 4)
        summary_rows.append(item)

    return summary_rows


def _resolve_baseline_label(run_labels: list[str], baseline: str) -> str:
    if baseline.isdigit():
        idx = int(baseline)
        if 0 <= idx < len(run_labels):
            return run_labels[idx]
    if baseline in run_labels:
        return baseline
    raise ValueError(f"baseline 不存在: {baseline}，可选值: {run_labels}")


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_markdown(
    summary_rows: list[dict[str, object]],
    compare_rows: list[dict[str, object]],
    baseline_label: str,
    metrics: list[str],
) -> str:
    lines: list[str] = []
    lines.append("# 实验结果对比")
    lines.append("")
    lines.append(f"- 基线: {baseline_label}")
    lines.append("")

    lines.append("## 运行汇总")
    lines.append("")
    lines.append("| run | group | retrieval | agent | n | avg_total | avg_llm_exec | avg_llm_safe | avg_llm_comp | judge_success_rate |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|")
    for row in summary_rows:
        lines.append(
            "| {run_label} | {group_id} | {retrieval_mode} | {agent_mode} | {sample_count} | {avg_total_score} | {avg_llm_executability_score} | {avg_llm_safety_score} | {avg_llm_compliance_score} | {llm_judge_success_rate} |".format(
                **row
            )
        )

    lines.append("")
    lines.append("## 基线差值")
    lines.append("")
    lines.append("| group | retrieval | agent | candidate | metric | baseline | candidate_value | delta |")
    lines.append("|---|---|---|---|---|---:|---:|---:|")
    for row in compare_rows:
        lines.append(
            "| {group_id} | {retrieval_mode} | {agent_mode} | {candidate_run} | {metric} | {baseline_value} | {candidate_value} | {delta} |".format(
                **row
            )
        )

    lines.append("")
    lines.append("## 指标说明")
    lines.append("")
    lines.append("- delta = candidate - baseline")
    lines.append("- 对于 critical_miss_rate、missing_actions_count，这两个指标越低越好")
    lines.append("- 本次对比指标: " + ", ".join(metrics + ["llm_judge_success_rate"]))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总并对比多个实验结果目录/CSV")
    parser.add_argument(
        "inputs",
        nargs="+",
        help="多个结果目录或 CSV 文件路径，目录下会自动读取可识别结果 CSV",
    )
    parser.add_argument(
        "--labels",
        default="",
        help="可选，逗号分隔标签，顺序与 inputs 一致",
    )
    parser.add_argument(
        "--baseline",
        default="0",
        help="基线运行标签或下标（默认 0）",
    )
    parser.add_argument(
        "--metrics",
        default=",".join(DEFAULT_METRICS),
        help="用于对比的指标，逗号分隔",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="输出目录，默认 experiments/results/compare_时间戳",
    )
    args = parser.parse_args()

    input_paths = [Path(text) for text in args.inputs]
    missing = [str(path) for path in input_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"以下输入不存在: {missing}")

    labels = [item.strip() for item in str(args.labels or "").split(",") if item.strip()]
    if labels and len(labels) != len(input_paths):
        raise ValueError("labels 数量必须与 inputs 一致")

    metrics = [item.strip() for item in str(args.metrics or "").split(",") if item.strip()]
    if not metrics:
        raise ValueError("metrics 不能为空")

    run_inputs: list[tuple[str, list[Path]]] = []
    for index, input_path in enumerate(input_paths):
        label = labels[index] if labels else input_path.stem
        csv_files = list(_iter_result_csvs(input_path))
        if not csv_files:
            raise ValueError(f"未找到可识别结果 CSV: {input_path}")
        run_inputs.append((label, csv_files))

    summary_rows: list[dict[str, object]] = []
    for label, csv_files in run_inputs:
        summary_rows.extend(_summarize_run(label, csv_files, metrics))

    run_labels = [label for label, _ in run_inputs]
    baseline_label = _resolve_baseline_label(run_labels, str(args.baseline))

    by_run_group: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for row in summary_rows:
        key = (
            str(row["run_label"]),
            str(row["group_id"]),
            str(row["retrieval_mode"]),
            str(row["agent_mode"]),
        )
        by_run_group[key] = row

    compare_rows: list[dict[str, object]] = []
    group_keys = sorted(
        {
            (str(row["group_id"]), str(row["retrieval_mode"]), str(row["agent_mode"]))
            for row in summary_rows
        }
    )

    metric_list = metrics + ["llm_judge_success_rate"]
    for group_id, retrieval_mode, agent_mode in group_keys:
        base_key = (baseline_label, group_id, retrieval_mode, agent_mode)
        baseline_row = by_run_group.get(base_key)
        if not baseline_row:
            continue

        for run_label in run_labels:
            if run_label == baseline_label:
                continue
            candidate_key = (run_label, group_id, retrieval_mode, agent_mode)
            candidate_row = by_run_group.get(candidate_key)
            if not candidate_row:
                continue

            for metric in metric_list:
                base_col = metric if metric == "llm_judge_success_rate" else f"avg_{metric}"
                baseline_value = _to_float(baseline_row.get(base_col, 0.0))
                candidate_value = _to_float(candidate_row.get(base_col, 0.0))
                compare_rows.append(
                    {
                        "group_id": group_id,
                        "retrieval_mode": retrieval_mode,
                        "agent_mode": agent_mode,
                        "baseline_run": baseline_label,
                        "candidate_run": run_label,
                        "metric": metric,
                        "baseline_value": round(baseline_value, 4),
                        "candidate_value": round(candidate_value, 4),
                        "delta": round(candidate_value - baseline_value, 4),
                    }
                )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else Path("experiments") / "results" / f"compare_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_fields = [
        "run_label",
        "group_id",
        "retrieval_mode",
        "agent_mode",
        "sample_count",
    ] + [f"avg_{metric}" for metric in metrics] + ["llm_judge_success_rate"]

    compare_fields = [
        "group_id",
        "retrieval_mode",
        "agent_mode",
        "baseline_run",
        "candidate_run",
        "metric",
        "baseline_value",
        "candidate_value",
        "delta",
    ]

    summary_rows.sort(key=lambda row: (str(row["group_id"]), str(row["run_label"])))
    compare_rows.sort(
        key=lambda row: (
            str(row["group_id"]),
            str(row["candidate_run"]),
            str(row["metric"]),
        )
    )

    summary_csv = output_dir / "summary.csv"
    compare_csv = output_dir / "compare.csv"
    report_md = output_dir / "report.md"

    _write_csv(summary_csv, summary_rows, summary_fields)
    _write_csv(compare_csv, compare_rows, compare_fields)
    report_md.write_text(_build_markdown(summary_rows, compare_rows, baseline_label, metrics), encoding="utf-8")

    print(f"runs={len(run_inputs)}")
    print(f"baseline={baseline_label}")
    print(f"output_dir={output_dir}")
    print(f"summary_csv={summary_csv}")
    print(f"compare_csv={compare_csv}")
    print(f"report_md={report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
