from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contracts import (  # noqa: E402
    IncidentInput,
    ExtractedEntities,
    CasualtyEstimate,
    Neo4jConstraint,
    ChromaEvidence,
    RetrievalContext,
    StrategyDraft,
    ReviewResult,
    PipelineResult,
)


def main() -> None:
    incident = IncidentInput(raw_text="雨天高速追尾并伴随起火，有人员受伤")
    entities = ExtractedEntities(
        incident_type="追尾",
        weather="雨天",
        hazards=["起火"],
        vehicles=["小轿车", "货车"],
        location_features=["高速"],
        casualty_estimate=CasualtyEstimate(deaths=0, injuries=2, unknown=False),
        evidence_from_image=["车头变形", "明火"],
        extract_confidence=0.86,
    )
    context = RetrievalContext(
        neo4j_constraints=[
            Neo4jConstraint(
                rule="严禁直接用水扑救危化品火灾",
                source_node="HazardRule",
                relation="RESTRICTS",
                target_node="DisposalMeasure",
            )
        ],
        chroma_evidence=[
            ChromaEvidence(
                content="历史案例显示雨天追尾起火应优先隔离与泡沫抑制。",
                file_name="案例.txt",
                chunk_id="12",
                distance=0.21,
            )
        ],
        severity_candidates=["较大", "重大"],
        severity="较大",
        severity_source="neo4j",
    )
    draft = StrategyDraft(
        focus="先控火后疏导",
        steps=["封闭内侧车道", "泡沫灭火", "医疗急救转运"],
        required_resources=["消防车", "救护车", "清障车"],
        legal_references=["道路交通安全法"],
    )
    review = ReviewResult(
        status="APPROVED",
        reason="方案满足硬约束且资源充足",
        retry_count=1,
    )

    result = PipelineResult(
        incident=incident,
        entities=entities,
        context=context,
        draft=draft,
        review=review,
        final_strategy="执行封闭-灭火-转运三阶段处置，持续发布绕行信息。",
        human_handoff=False,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
