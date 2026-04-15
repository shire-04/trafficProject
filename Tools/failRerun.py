import csv
import json
from pathlib import Path

csv_path = Path("experiments\\results\\full_eval_20260411_140837\\G5_full.csv")
jsonl_path = Path("data_clean/评测数据集.jsonl")
out_path = Path("data_clean/评测数据集_g5_failed.jsonl")

failed_ids = set()
error_keywords = [
    "rules_judge_error",
    "llm_judge_error",
    "llm审查失败",
    "已跳过审查",
    "网络请求失败",
    "http 503",
    "http 429",
    "quota",
    "resource_exhausted",
    "unavailable",
    "ssl",
    "timeout",
    "unexpected_eof",
]

with csv_path.open("r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        sample_id = str(row.get("sample_id", "")).strip()
        note_text = " ".join(
            [
                str(row.get("llm_judge_error") or ""),
                str(row.get("rules_judge_error") or ""),
                str(row.get("notes") or ""),
            ]
        ).lower()
        if any(keyword in note_text for keyword in error_keywords):
            failed_ids.add(sample_id)

with jsonl_path.open("r", encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
    for line in fin:
        item = json.loads(line)
        if str(item.get("sample_id", "")).strip() in failed_ids:
            fout.write(json.dumps(item, ensure_ascii=False) + "\n")

print("failed count:", len(failed_ids))
print("failed sample ids:", sorted(failed_ids))
print("output file:", out_path)