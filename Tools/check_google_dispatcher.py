import json
import os
import sys

sys.path.insert(0, r"E:\trafficProject\src")

from agents import DispatcherAgent
from contracts import IncidentInput
from llm_provider import generate_json_response


def main() -> None:
    api_result = generate_json_response(
        model=os.getenv("TRAFFIC_LLM_MODEL", "gemma-3-27b-it"),
        system_prompt="你是一个只输出JSON的助手。",
        user_content='请只输出 JSON：{"ok": true, "message": "hello"}',
        timeout_seconds=120,
    )

    agent = DispatcherAgent()
    samples = [
        "高速公路危化品车辆泄漏，需要处置。",
        "雨天高速公路发生交通事故，现场起火并造成2人受伤，需要立即组织救援。",
        "隧道内多车追尾，部分人员被困，道路中断。",
    ]

    results = []
    for text in samples:
        item = agent.extract(IncidentInput(raw_text=text))
        results.append(
            {
                "raw_text": text,
                "incident_type_raw": item.incident_type_raw,
                "severity": item.severity,
                "extract_confidence": item.extract_confidence,
                "hazards": item.hazards,
                "vehicles": item.vehicles,
                "location_features": item.location_features,
            }
        )

    print(
        json.dumps(
            {
                "api_content_preview": str(api_result.get("content", ""))[:200],
                "dispatcher_results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
