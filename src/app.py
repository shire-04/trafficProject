import json

import streamlit as st

from contracts import IncidentInput
from orchestrator import PipelineOrchestrator


def run_new_pipeline(user_input: str, image_bytes: bytes | None, show_debug: bool) -> None:
    orchestrator = PipelineOrchestrator()
    try:
        spinner_text = "正在执行 LLM + 双库编排链路..."
        if image_bytes:
            spinner_text = "正在执行 LLM + 双库编排链路（含图片分析，本地多模态模型可能需要 1-3 分钟）..."

        with st.spinner(spinner_text):
            incident = IncidentInput(raw_text=user_input, image_bytes=image_bytes)
            result = orchestrator.run_once(incident)
    finally:
        orchestrator.close()

    routing = getattr(result, "routing", None)
    revised_rounds = max(0, int(getattr(result.review, "retry_count", 0) or 0))
    is_revised = revised_rounds > 0
    initial_draft = getattr(result, "initial_draft", None) or result.draft
    final_draft = result.draft

    st.divider()
    st.subheader("📢 LLM + 双库编排会议纪要")

    if routing:
        with st.chat_message("assistant", avatar="🛣️"):
            st.write("**路由专家** 已完成难度判定与链路分流。")
            st.json(
                {
                    "requested_mode": routing.requested_mode,
                    "effective_mode": routing.effective_mode,
                    "route_target": routing.route_target,
                    "difficulty": routing.difficulty,
                    "reason": routing.reason,
                    "confidence": routing.confidence,
                    "used_llm": routing.used_llm,
                    "fallback_to_g5": routing.fallback_to_g5,
                    "fallback_reason": routing.fallback_reason,
                    "rule_hit_count": routing.rule_hit_count,
                    "rule_hits": routing.rule_hits,
                },
                expanded=False,
            )

    with st.chat_message("assistant", avatar="📞"):
        st.write("**接警解析专家** 已完成结构化抽取。")
        st.json(
            {
                "incident_type_raw": result.entities.incident_type_raw,
                "incident_type": result.entities.incident_type,
                "matched_events": [
                    {
                        "surface_form": item.surface_form,
                        "normalized_name": item.normalized_name,
                        "node_id": item.node_id,
                        "match_confidence": item.match_confidence,
                        "match_reason": item.match_reason,
                    }
                    for item in result.entities.matched_events
                ],
                "severity": result.entities.severity,
                "severity_reason": result.entities.severity_reason,
                "severity_confidence": result.entities.severity_confidence,
                "difficulty": result.entities.difficulty,
                "difficulty_reason": result.entities.difficulty_reason,
                "difficulty_confidence": result.entities.difficulty_confidence,
                "weather": result.entities.weather,
                "hazards": result.entities.hazards,
                "vehicles": result.entities.vehicles,
                "location_features": result.entities.location_features,
                "casualty_estimate": {
                    "deaths": result.entities.casualty_estimate.deaths,
                    "injuries": result.entities.casualty_estimate.injuries,
                    "missing": result.entities.casualty_estimate.missing,
                    "unknown": result.entities.casualty_estimate.unknown,
                },
                "extract_confidence": result.entities.extract_confidence,
                "evidence_from_image": result.entities.evidence_from_image,
            },
            expanded=False,
        )
        if result.entities.evidence_from_image:
            st.caption("图片证据摘要")
            for item in result.entities.evidence_from_image:
                st.write(f"- {item}")

    with st.chat_message("assistant", avatar="🧭"):
        st.write("**检索与逻辑专家** 已返回双库约束与证据。")
        st.write(f"- 当前判级：`{result.context.severity}`")
        st.write(f"- 判级来源：`{result.context.severity_source}`")
        st.write(f"- 图谱约束数：`{len(result.context.neo4j_constraints)}`")
        st.write(f"- 向量证据数：`{len(result.context.chroma_evidence)}`")

    with st.chat_message("assistant", avatar="👮"):
        st.write("**指挥调度专家** 已输出初版方案。")
        if is_revised:
            st.caption(f"该初版随后经历 {revised_rounds} 轮审查修订。")
        st.write(f"聚焦：{initial_draft.focus}")
        for index, step in enumerate(initial_draft.steps, start=1):
            st.markdown(f"{index}. {step}")

    with st.chat_message("assistant", avatar="🛡️"):
        st.write("**推演评估专家** 已完成审查。")
        review_payload = {
            "status": result.review.status,
            "reason": result.review.reason,
            "retry_count": result.review.retry_count,
            "missing_actions": result.review.missing_actions,
            "risk_notes": result.review.risk_notes,
            "failure_type": result.review.failure_type,
        }
        st.code(json.dumps(review_payload, ensure_ascii=False, indent=2), language="json")
        if result.review.status == "APPROVED":
            st.success("✅ 新编排链路审查通过")
        else:
            st.warning("⚠️ 新编排链路仍需人工接管")

    st.divider()
    if is_revised:
        st.header("🚦 最终下达管控策略（修订后终稿）")
    else:
        st.header("🚦 最终下达管控策略（与初版一致）")
    for index, step in enumerate(final_draft.steps, start=1):
        st.markdown(f"{index}. {step}")

    if final_draft.required_resources:
        st.subheader("🧰 资源需求")
        st.write("、".join(final_draft.required_resources))

    if final_draft.legal_references:
        st.subheader("📚 法规依据")
        st.write("、".join(final_draft.legal_references))

    if result.human_handoff:
        st.warning("当前结果建议人工复核后再下达。")
    else:
        st.success("当前结果可作为默认推荐方案输出。")

    if show_debug:
        with st.sidebar:
            st.write("---")
            st.subheader("底层数据监控")
            st.json(
                {
                    "entities": {
                        "incident_type_raw": result.entities.incident_type_raw,
                        "incident_type": result.entities.incident_type,
                        "matched_events": [
                            {
                                "surface_form": item.surface_form,
                                "normalized_name": item.normalized_name,
                                "node_id": item.node_id,
                                "match_confidence": item.match_confidence,
                                "match_reason": item.match_reason,
                            }
                            for item in result.entities.matched_events
                        ],
                        "severity": result.entities.severity,
                        "severity_reason": result.entities.severity_reason,
                        "severity_confidence": result.entities.severity_confidence,
                        "weather": result.entities.weather,
                        "hazards": result.entities.hazards,
                        "vehicles": result.entities.vehicles,
                        "location_features": result.entities.location_features,
                        "evidence_from_image": result.entities.evidence_from_image,
                        "extract_confidence": result.entities.extract_confidence,
                    },
                    "retrieval": {
                        "severity": result.context.severity,
                        "severity_source": result.context.severity_source,
                        "neo4j_constraints": [
                            {
                                "rule": item.rule,
                                "source_node": item.source_node,
                                "relation": item.relation,
                                "target_node": item.target_node,
                            }
                            for item in result.context.neo4j_constraints[:12]
                        ],
                        "chroma_evidence": [
                            {
                                "file_name": item.file_name,
                                "chunk_id": item.chunk_id,
                                "distance": item.distance,
                            }
                            for item in result.context.chroma_evidence[:5]
                        ],
                    },
                    "review": review_payload,
                    "routing": {
                        "requested_mode": routing.requested_mode if routing else "",
                        "effective_mode": routing.effective_mode if routing else "",
                        "route_target": routing.route_target if routing else "",
                        "difficulty": routing.difficulty if routing else "",
                        "confidence": routing.confidence if routing else 0.0,
                        "fallback_to_g5": routing.fallback_to_g5 if routing else False,
                        "fallback_reason": routing.fallback_reason if routing else "",
                        "rule_hit_count": routing.rule_hit_count if routing else 0,
                        "rule_hits": routing.rule_hits if routing else [],
                    },
                }
            )
# --- 页面配置 ---
st.set_page_config(page_title="E-KELL 交通管控智脑", layout="wide")

st.title("🚦 基于大语言模型的交通管控策略生成系统")
st.markdown("*E-KELL Traffic: Knowledge-Enhanced LLM for Traffic Emergency Response*")

# --- 侧边栏：系统状态 ---
with st.sidebar:
    st.header("系统状态")
    st.success("✅ Neo4j 知识图谱已连接")
    st.success("✅ ChromaDB 向量库已就绪")
    st.info("🧠 当前运行: Auto 动态路由（easy→G3，medium/hard→G5）")
    st.header("📸 现场多模态输入")
    uploaded_file = st.file_uploader("上传事故现场照片", type=['jpg', 'png'])
    if uploaded_file:
        st.info("检测到图像输入，已加载 Vision Encoder...")
        st.caption("新链路会将图片分析结果并入接警解析阶段。")
        st.caption("若远程模型首轮冷启动较慢，页面可能等待 1-3 分钟；超时后会自动退回文本链路。")
    show_debug = st.checkbox("显示推理思维链 (Chain-of-Thought)", value=True)

# --- 主界面 ---
user_input = st.text_input("请输入突发交通事件描述：", placeholder="例如：路口发生泥头车右转碾压电动车事故...")

if st.button("启动联合指挥研判"):
    if not user_input and not uploaded_file:
        st.warning("请至少输入事件描述或上传现场图片。")
    else:
        effective_input = user_input.strip() or "请结合现场图片生成交通事故处置建议。"
        image_bytes = None
        if uploaded_file:
            image_bytes = uploaded_file.getvalue()

        run_new_pipeline(effective_input, image_bytes, show_debug)

# --- 页脚 ---
st.divider()
st.caption("基于 Chen et al. (2024) E-KELL 框架与 Xie et al. (2025) 三层架构实现")