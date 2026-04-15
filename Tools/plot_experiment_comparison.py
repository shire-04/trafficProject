import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def _read_summary_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {
        "group_id",
        "retrieval_mode",
        "agent_mode",
        "sample_count",
        "avg_latency_ms",
        "avg_approved_like",
        "avg_executability_score",
        "avg_safety_score",
        "avg_constraint_alignment_score",
        "avg_evidence_grounding_score",
        "avg_total_score",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Summary CSV missing columns: {missing}")
    return df


def _build_labels(df: pd.DataFrame) -> list[str]:
    labels = []
    for _, row in df.iterrows():
        labels.append(f"{row['group_id']}\n{row['retrieval_mode']}|{row['agent_mode']}")
    return labels


def _save_bar_chart(output_path: Path, title: str, x_labels: list[str], values, y_label: str, ylim=None) -> None:
    plt.figure(figsize=(11, 6))
    bars = plt.bar(x_labels, values)
    plt.title(title)
    plt.ylabel(y_label)
    if ylim is not None:
        plt.ylim(*ylim)

    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2, height, f"{height:.4f}", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _save_grouped_scores_chart(output_path: Path, title: str, x_labels: list[str], df: pd.DataFrame) -> None:
    plt.figure(figsize=(12, 6))
    x = range(len(x_labels))
    width = 0.18

    e = df["avg_executability_score"].tolist()
    s = df["avg_safety_score"].tolist()
    c = df["avg_constraint_alignment_score"].tolist()
    g = df["avg_evidence_grounding_score"].tolist()

    plt.bar([i - 1.5 * width for i in x], e, width=width, label="executability")
    plt.bar([i - 0.5 * width for i in x], s, width=width, label="safety")
    plt.bar([i + 0.5 * width for i in x], c, width=width, label="constraint")
    plt.bar([i + 1.5 * width for i in x], g, width=width, label="evidence")

    plt.xticks(list(x), x_labels)
    plt.ylim(0, 1)
    plt.ylabel("score")
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate comparison charts from experiment summary CSV.")
    parser.add_argument("--input", required=True, help="Path to all_groups_summary.csv")
    parser.add_argument("--output-dir", default="", help="Output folder for chart images")
    parser.add_argument("--title-prefix", default="Experiment Comparison", help="Prefix text for chart titles")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input summary not found: {input_path}")

    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = _read_summary_csv(input_path).sort_values(by="group_id")
    labels = _build_labels(df)

    _save_bar_chart(
        output_dir / "total_score_comparison.png",
        f"{args.title_prefix} - Average Total Score",
        labels,
        df["avg_total_score"],
        "avg_total_score",
        (0, 1),
    )

    _save_grouped_scores_chart(
        output_dir / "subscores_comparison.png",
        f"{args.title_prefix} - Subscores",
        labels,
        df,
    )

    _save_bar_chart(
        output_dir / "latency_comparison_ms.png",
        f"{args.title_prefix} - Average Latency",
        labels,
        df["avg_latency_ms"],
        "avg_latency_ms",
        None,
    )

    _save_bar_chart(
        output_dir / "approved_like_comparison.png",
        f"{args.title_prefix} - Approved Like",
        labels,
        df["avg_approved_like"],
        "avg_approved_like",
        (0, 1.05),
    )

    _save_bar_chart(
        output_dir / "evidence_grounding_comparison.png",
        f"{args.title_prefix} - Evidence Grounding",
        labels,
        df["avg_evidence_grounding_score"],
        "avg_evidence_grounding_score",
        (0, 1),
    )

    print(f"input={input_path}")
    print(f"rows={len(df)}")
    print(f"output_dir={output_dir}")
    for name in [
        "total_score_comparison.png",
        "subscores_comparison.png",
        "latency_comparison_ms.png",
        "approved_like_comparison.png",
        "evidence_grounding_comparison.png",
    ]:
        print(f"chart={output_dir / name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
