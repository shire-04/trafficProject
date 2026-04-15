import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contracts import IncidentInput  # noqa: E402
from llm_provider import get_default_model  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402

from eval_metrics import score_sample

RESULT_COLUMNS = [
    "run_id",
    "run_time",
    "sample_id",
    "group_id",
    "retrieval_mode",
    "single_agent_retrieval_mode",
    "agent_mode",
    "effective_agent_mode",
    "route_target",
    "route_difficulty",
    "route_confidence",
    "route_used_llm",
    "route_fallback_to_g5",
    "enable_review",
    "enable_revision",
    "model_name",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "approved_like",
    "executability_score",
    "safety_score",
    "constraint_alignment_score",
    "evidence_grounding_score",
    "total_score",
    "rule_total_score",
    "score_backend",
    "llm_judge_enabled",
    "llm_judge_success",
    "llm_judge_weight",
    "llm_judge_model",
    "llm_executability_score",
    "llm_safety_score",
    "llm_compliance_score",
    "llm_overall_score",
    "llm_judge_reason",
    "llm_judge_error",
    "constraint_coverage",
    "critical_miss_rate",
    "has_forbidden_action",
    "critical_action_missed",
    "missing_actions_count",
    "violated_constraints_count",
    "evidence_hit_count",
    "notes",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONL 解析失败，第 {idx} 行: {exc}") from exc
        if isinstance(row, dict):
            records.append(row)
    return records


def _filter_records(records: list[dict[str, Any]], split: str, offset: int, limit: int) -> list[dict[str, Any]]:
    selected = records
    if split:
        selected = [item for item in selected if str(item.get("split", "")).strip() == split]
    if offset > 0:
        selected = selected[offset:]
    if limit > 0:
        selected = selected[:limit]
    return selected


def _mode_flags(agent_mode: str, effective_mode: str | None = None) -> tuple[int, int]:
    mode = str(effective_mode or agent_mode or "").strip().lower()
    if mode == "multi_with_review":
        return 1, 1
    return 0, 0


def _normalize_single_agent_retrieval_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"none", "off", "disabled", "skip", "no_retrieval"}:
        return "none"
    return "inherit"


def _resolve_effective_retrieval_mode(
    retrieval_mode: str,
    single_agent_retrieval_mode: str,
    effective_agent_mode: str,
) -> str:
    normalized_effective_mode = str(effective_agent_mode or "").strip().lower()
    normalized_retrieval_mode = str(retrieval_mode or "").strip().lower() or "dual"
    if normalized_effective_mode in {"single", "single_agent", "single_v2"} and single_agent_retrieval_mode == "none":
        return "none"
    return normalized_retrieval_mode


def _build_result_payload(
    sample: dict[str, Any],
    pipeline_result: Any,
    retrieval_mode: str,
    agent_mode: str,
) -> dict[str, Any]:
    routing = getattr(pipeline_result, "routing", None)
    effective_agent_mode = str(getattr(routing, "effective_mode", "") or agent_mode or "").strip().lower()
    route_difficulty = str(getattr(routing, "difficulty", "") or "").strip().lower()
    evidence_list = [
        {
            "file_name": item.file_name,
            "chunk_id": item.chunk_id,
            "content": item.content,
            "distance": item.distance,
        }
        for item in pipeline_result.context.chroma_evidence
    ]
    return {
        "steps": list(pipeline_result.draft.steps),
        "legal_references": list(pipeline_result.draft.legal_references),
        "final_strategy": pipeline_result.final_strategy,
        "review_status": pipeline_result.review.status,
        "review_reason": pipeline_result.review.reason,
        "violated_constraints_count": len(pipeline_result.review.violated_constraints),
        "evidence_list": evidence_list,
        "retrieval_mode": retrieval_mode,
        "agent_mode": agent_mode,
        "effective_agent_mode": effective_agent_mode,
        "route_difficulty": route_difficulty,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="批量运行评测样本并输出逐条结果 CSV")
    parser.add_argument("--dataset", default="data_raw/评测数据集.jsonl", help="评测数据集 JSONL 路径")
    parser.add_argument("--output", default="", help="输出 CSV 路径；默认写入 experiments/results")
    parser.add_argument("--group-id", default="G5", help="实验组标识")
    parser.add_argument("--retrieval-mode", default="dual", choices=["dual", "neo4j", "chroma"], help="检索模式")
    parser.add_argument(
        "--agent-mode",
        default="auto",
        choices=["single", "multi_no_review", "multi_with_review", "auto"],
        help="Agent 编排模式",
    )
    parser.add_argument(
        "--single-agent-retrieval-mode",
        default="inherit",
        choices=["inherit", "none"],
        help="single_agent 下的检索行为：inherit 复用 TRAFFIC_RETRIEVAL_MODE；none 跳过 Neo4j/Chroma",
    )
    parser.add_argument("--split", default="test", help="按 split 过滤，空字符串表示不过滤")
    parser.add_argument("--offset", type=int, default=0, help="起始偏移")
    parser.add_argument("--limit", type=int, default=0, help="最多运行样本数，0 表示全部")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"数据集不存在: {dataset_path}")

    run_time = datetime.now().isoformat(timespec="seconds")
    run_id = f"R{datetime.now().strftime('%Y%m%d%H%M%S')}"

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = PROJECT_ROOT / "experiments" / "results" / f"results_{run_id}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    records = _read_jsonl(dataset_path)
    selected = _filter_records(records, args.split.strip(), args.offset, args.limit)
    if not selected:
        raise ValueError("没有可运行的样本，请检查 split/offset/limit")

    single_agent_retrieval_mode = _normalize_single_agent_retrieval_mode(args.single_agent_retrieval_mode)
    os.environ["TRAFFIC_RETRIEVAL_MODE"] = args.retrieval_mode
    os.environ["TRAFFIC_AGENT_MODE"] = args.agent_mode
    os.environ["TRAFFIC_SINGLE_AGENT_RETRIEVAL_MODE"] = single_agent_retrieval_mode

    model_name = get_default_model()
    rows: list[dict[str, Any]] = []
    orchestrator = PipelineOrchestrator()
    try:
        for index, sample in enumerate(selected, start=1):
            sample_id = str(sample.get("sample_id", "") or f"S{index:03d}")
            incident_text = str(sample.get("incident_text", "")).strip()
            if not incident_text:
                continue

            started = time.perf_counter()
            try:
                result = orchestrator.run_once(IncidentInput(raw_text=incident_text))
                latency_ms = int((time.perf_counter() - started) * 1000)

                routing = getattr(result, "routing", None)
                effective_agent_mode = str(getattr(routing, "effective_mode", "") or args.agent_mode or "").strip().lower()
                route_target = str(getattr(routing, "route_target", "") or effective_agent_mode)
                route_difficulty = str(getattr(routing, "difficulty", "") or "").strip().lower()
                route_confidence = float(getattr(routing, "confidence", 0.0) or 0.0)
                route_used_llm = 1 if bool(getattr(routing, "used_llm", False)) else 0
                route_fallback_to_g5 = 1 if bool(getattr(routing, "fallback_to_g5", False)) else 0
                enable_review, enable_revision = _mode_flags(args.agent_mode, effective_agent_mode)
                effective_retrieval_mode = _resolve_effective_retrieval_mode(
                    retrieval_mode=args.retrieval_mode,
                    single_agent_retrieval_mode=single_agent_retrieval_mode,
                    effective_agent_mode=effective_agent_mode,
                )

                payload = _build_result_payload(sample, result, effective_retrieval_mode, args.agent_mode)
                metrics = score_sample(sample, payload)

                row = {
                    "run_id": run_id,
                    "run_time": run_time,
                    "sample_id": sample_id,
                    "group_id": args.group_id,
                    "retrieval_mode": effective_retrieval_mode,
                    "single_agent_retrieval_mode": single_agent_retrieval_mode,
                    "agent_mode": args.agent_mode,
                    "effective_agent_mode": effective_agent_mode,
                    "route_target": route_target,
                    "route_difficulty": route_difficulty,
                    "route_confidence": round(route_confidence, 4),
                    "route_used_llm": route_used_llm,
                    "route_fallback_to_g5": route_fallback_to_g5,
                    "enable_review": enable_review,
                    "enable_revision": enable_revision,
                    "model_name": model_name,
                    "latency_ms": latency_ms,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    **metrics,
                }
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.perf_counter() - started) * 1000)
                enable_review, enable_revision = _mode_flags(args.agent_mode, args.agent_mode)
                fallback_retrieval_mode = _resolve_effective_retrieval_mode(
                    retrieval_mode=args.retrieval_mode,
                    single_agent_retrieval_mode=single_agent_retrieval_mode,
                    effective_agent_mode=args.agent_mode,
                )
                row = {
                    "run_id": run_id,
                    "run_time": run_time,
                    "sample_id": sample_id,
                    "group_id": args.group_id,
                    "retrieval_mode": fallback_retrieval_mode,
                    "single_agent_retrieval_mode": single_agent_retrieval_mode,
                    "agent_mode": args.agent_mode,
                    "effective_agent_mode": args.agent_mode,
                    "route_target": "",
                    "route_difficulty": "",
                    "route_confidence": 0.0,
                    "route_used_llm": 0,
                    "route_fallback_to_g5": 0,
                    "enable_review": enable_review,
                    "enable_revision": enable_revision,
                    "model_name": model_name,
                    "latency_ms": latency_ms,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "approved_like": 0,
                    "executability_score": 0,
                    "safety_score": 0,
                    "constraint_alignment_score": 0,
                    "evidence_grounding_score": 0,
                    "total_score": 0,
                    "rule_total_score": 0,
                    "score_backend": str(os.getenv("EVAL_SCORE_BACKEND", "rules")).strip().lower() or "rules",
                    "llm_judge_enabled": 1 if str(os.getenv("EVAL_ENABLE_LLM_JUDGE", "0")).strip().lower() in {"1", "true", "on", "yes"} else 0,
                    "llm_judge_success": 0,
                    "llm_judge_weight": float(os.getenv("EVAL_LLM_JUDGE_WEIGHT", "0.35") or 0.35),
                    "llm_judge_model": str(os.getenv("EVAL_LLM_JUDGE_MODEL", "xdeepseekv3")).strip() or "xdeepseekv3",
                    "llm_executability_score": 0,
                    "llm_safety_score": 0,
                    "llm_compliance_score": 0,
                    "llm_overall_score": 0,
                    "llm_judge_reason": "",
                    "llm_judge_error": "",
                    "constraint_coverage": 0,
                    "critical_miss_rate": 1,
                    "has_forbidden_action": 0,
                    "critical_action_missed": 1,
                    "missing_actions_count": len(sample.get("must_actions", []) or []),
                    "violated_constraints_count": 0,
                    "evidence_hit_count": 0,
                    "notes": f"RUN_ERROR: {type(exc).__name__}: {exc}",
                }
            rows.append(row)
            print(f"[{index}/{len(selected)}] {sample_id} done")
    finally:
        orchestrator.close()

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"dataset={dataset_path}")
    print(f"selected_samples={len(selected)}")
    print(f"output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
