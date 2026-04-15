import argparse
import json
import re
from pathlib import Path
from typing import Any

CITE_PATTERN = re.compile(r"\s*\[cite:\s*\d+\]")
MULTI_SPACE_PATTERN = re.compile(r"[ \t]{2,}")

TEXT_FIELDS = [
    "incident_text",
    "notes",
]
LIST_TEXT_FIELDS = [
    "must_actions",
    "must_constraints",
    "must_evidence_topics",
    "critical_actions",
    "forbidden_actions",
]


def clean_text(value: str) -> tuple[str, int]:
    original = str(value or "")
    matches = CITE_PATTERN.findall(original)
    cleaned = CITE_PATTERN.sub("", original)
    cleaned = MULTI_SPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned, len(matches)


def clean_record(record: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
    cleaned = dict(record)
    removed_count = 0
    changed = False

    for field in TEXT_FIELDS:
        if field in cleaned and isinstance(cleaned[field], str):
            new_text, removed = clean_text(cleaned[field])
            removed_count += removed
            if new_text != cleaned[field]:
                changed = True
                cleaned[field] = new_text

    for field in LIST_TEXT_FIELDS:
        values = cleaned.get(field)
        if not isinstance(values, list):
            continue
        new_values = []
        list_changed = False
        for item in values:
            if not isinstance(item, str):
                new_values.append(item)
                continue
            new_text, removed = clean_text(item)
            removed_count += removed
            if new_text != item:
                list_changed = True
            new_values.append(new_text)
        if list_changed:
            changed = True
            cleaned[field] = new_values

    return cleaned, removed_count, changed


def main() -> int:
    parser = argparse.ArgumentParser(description="清理评测数据集中的 [cite: n] 噪声标记")
    parser.add_argument("input", help="输入 JSONL 文件路径")
    parser.add_argument("--output", help="输出 JSONL 文件路径；默认生成 *.cleaned.jsonl")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".cleaned.jsonl")

    lines = input_path.read_text(encoding="utf-8").splitlines()
    total_records = 0
    changed_records = 0
    removed_markers = 0
    output_lines: list[str] = []

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是合法 JSON: {exc}") from exc

        total_records += 1
        cleaned_record, removed, changed = clean_record(record)
        removed_markers += removed
        if changed:
            changed_records += 1
        output_lines.append(json.dumps(cleaned_record, ensure_ascii=False))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    print(f"input: {input_path}")
    print(f"output: {output_path}")
    print(f"total_records: {total_records}")
    print(f"changed_records: {changed_records}")
    print(f"removed_cite_markers: {removed_markers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
