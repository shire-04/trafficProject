from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import RouterAgent  # noqa: E402
from contracts import IncidentInput  # noqa: E402


CASES = [
    {
        "id": "easy_01",
        "text": "雨天高速追尾，1人轻伤，无明火，车道受阻。",
        "expected": "single_agent",
    },
    {
        "id": "medium_01",
        "text": "隧道内多车追尾，疑似泄漏，部分人员被困，需联动处置。",
        "expected": "multi_with_review",
    },
    {
        "id": "hard_01",
        "text": "夜间桥梁路段客货车相撞，现场冒烟，通信描述前后不一致，可能有危化品泄漏。",
        "expected": "multi_with_review",
    },
    {
        "id": "fallback_01",
        "text": "事故，情况不明，待确认。",
        "expected": "multi_with_review",
    },
]


def main() -> None:
    # 校验脚本默认关闭 LLM 仲裁，保证可离线稳定运行。
    router = RouterAgent(llm_router=lambda _: {})

    reports: list[dict] = []
    for item in CASES:
        decision = router.decide(IncidentInput(raw_text=item["text"]))
        reports.append(
            {
                "id": item["id"],
                "expected": item["expected"],
                "route_target": decision.route_target,
                "difficulty": decision.difficulty,
                "confidence": decision.confidence,
                "used_llm": decision.used_llm,
                "fallback_to_g5": decision.fallback_to_g5,
                "pass": decision.route_target == item["expected"],
            }
        )

    summary = {
        "case_count": len(reports),
        "pass_count": sum(1 for item in reports if item["pass"]),
        "fail_count": sum(1 for item in reports if not item["pass"]),
    }
    print(json.dumps({"summary": summary, "cases": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
