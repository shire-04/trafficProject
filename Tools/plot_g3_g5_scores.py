import csv
import matplotlib.pyplot as plt
from pathlib import Path


def main() -> int:
    csv_path = Path('experiments/results/G3_G5_各难度得分统计结果/avg_score.csv')
    output_path = Path('experiments/results/G3_G5_各难度得分统计结果/g3_g5_score_comparison.png')

    groups = {'G3': {}, 'G5': {}}
    difficulties = ['easy', 'medium', 'hard']

    with csv_path.open('r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            group = row['group']
            diff = row['difficulty']
            if group not in groups:
                groups[group] = {}
            groups[group][diff] = float(row['total_score']) if row['total_score'] else None

    fig, ax = plt.subplots(figsize=(10, 6))
    for group, data in groups.items():
        values = [data[d] for d in difficulties]
        ax.plot(difficulties, values, marker='o', label=group, linewidth=2)

    # annotate differences
    for i, diff in enumerate(difficulties):
        if all(groups[group][diff] is not None for group in groups):
            g3_val = groups['G3'][diff]
            g5_val = groups['G5'][diff]
            diff_val = g3_val - g5_val
            ax.annotate(
                f'{diff_val:+.3f}',
                xy=(i, (g3_val + g5_val) / 2),
                xytext=(0, 10),
                textcoords='offset points',
                ha='center',
                fontsize=10,
                color='black',
                bbox=dict(facecolor='white', edgecolor='gray', boxstyle='round,pad=0.2'),
            )

    ax.set_title('G3 vs G5 Total Score by Difficulty')
    ax.set_xlabel('Difficulty')
    ax.set_ylabel('Average Total Score')
    ax.set_ylim(0, 1)
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.legend(title='Group')

    output_path = Path('experiments/results/G3_G5_各难度得分统计结果/g3_g5_total_score_comparison.png')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f'Plot saved to {output_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
