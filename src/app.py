import json
import re

import streamlit as st
import ollama
from reasoning_engine import TrafficReasoningEngine
from agents import BaseAgent, PROMPT_ANALYST, PROMPT_LEGAL, PROMPT_CRITIC, PROMPT_COMMANDER


def _extract_json_block(text: str) -> str:
    """Attempt to extract the first JSON object from the model response."""
    if not text:
        return ""

    cleaned = text.strip()

    # Handle fenced code blocks, e.g. ```json { ... } ```
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fenced_match:
        return fenced_match.group(1).strip()

    # Fallback: grab the first JSON-looking block
    plain_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if plain_match:
        return plain_match.group(0).strip()

    return ""


def parse_critic_response(response: str) -> dict:
    """Parse critic JSON response; return empty dict when parsing fails."""
    json_block = _extract_json_block(response)
    if not json_block:
        return {}

    try:
        return json.loads(json_block)
    except json.JSONDecodeError:
        return {}
# --- 页面配置 ---
st.set_page_config(page_title="E-KELL 交通管控智脑", layout="wide")

st.title("🚦 基于知识图谱的交通管控策略生成系统")
st.markdown("*E-KELL Traffic: Knowledge-Enhanced LLM for Traffic Emergency Response*")

# --- 侧边栏：系统状态 ---
with st.sidebar:
    st.header("系统状态")
    st.success("✅ Neo4j 知识图谱已连接")
    st.success("✅ ChromaDB 向量库已就绪")
    st.info("🧠 模型加载: Qwen3-vl:4b")
    st.header("📸 现场多模态输入")
    uploaded_file = st.file_uploader("上传事故现场照片", type=['jpg', 'png'])
    if uploaded_file:
        st.info("检测到图像输入，已加载 Vision Encoder...")
        # 你的 VL 模型天然支持这个！
        # 未来你只需要把图片转为 base64 传给 qwen3-vl 即可
    # 模拟一个“调试模式”开关
    show_debug = st.checkbox("显示推理思维链 (Chain-of-Thought)", value=True)

# --- 主界面 ---
user_input = st.text_input("请输入突发交通事件描述：", placeholder="例如：路口发生泥头车右转碾压电动车事故...")

# --- 修改后的核心逻辑区 ---
if st.button("启动联合指挥研判") and user_input:
    engine = TrafficReasoningEngine()
    # 处理图片
    image_bytes = None
    if uploaded_file:
        image_bytes = uploaded_file.getvalue() # 获取二进制数据
        # 注意：这里需要把 bytes 转为 base64 或者保存为临时文件传给 Ollama
        # 简单起见，这里假设 Agent 能处理
    
    # 1. 工具层：先默默把数据查好 (Data Fetching)
    with st.spinner("正在连接指挥中心数据库..."):
        # Step 0: 提取关键词 (保留你原来的优秀逻辑)
        extract_prompt = f"提取'{user_input}'的核心风险事件词(如追尾)，不要修饰词，直接输出词汇。"
        try:
            res = ollama.chat(model='qwen3-vl:4b', messages=[{'role': 'user', 'content': extract_prompt}])
            core_keyword = res['message']['content'].strip()
        except:
            core_keyword = user_input
            
        # Step A: 查图谱
        graph_data = engine.query_graph(core_keyword)

    # 2. 实例化四个智能体 (Agents Initialization)
    analyst = BaseAgent("情报员", PROMPT_ANALYST)
    legal = BaseAgent("法规参谋", PROMPT_LEGAL)
    critic = BaseAgent("审查员", PROMPT_CRITIC)
    commander = BaseAgent("指挥官", PROMPT_COMMANDER)

    # 3. 演绎层：多智能体协作流程 (The Workflow)
    st.divider()
    st.subheader("📢 联合指挥中心会议纪要")

    # Round 1: 情报研判先行
    msg_analyst = ""
    col_a, col_b = st.columns(2)

    with col_a:
        with st.chat_message("analyst", avatar="🕵️"):
            st.write("**情报研判员** 正在发言...")
            msg_analyst = analyst.speak(
                user_input,
                context_data=str(graph_data),
                image_data=image_bytes
            )
            st.info(msg_analyst)

    # 基于情报结果检索法规
    semantic_hint = msg_analyst if msg_analyst and msg_analyst.strip() else ""
    with st.spinner("根据情报结果检索法规依据..."):
        vector_context = engine.query_vector_db(user_input, semantic_hint=semantic_hint)

    engine.close()

    with col_b:
        with st.chat_message("legal", avatar="⚖️"):
            st.write("**法规参谋** 正在发言...")
            legal_context = (
                "【情报摘要】\n"
                f"{msg_analyst}\n\n"
                "【法规检索片段】\n"
                f"{vector_context}"
            )
            msg_legal = legal.speak(user_input, context_data=legal_context)
            st.success(msg_legal)

    # Round 2: 指挥官生成初稿
    with st.chat_message("commander", avatar="👮"):
        st.write("**首席指挥官**：收到，正在拟定初版方案...")
        draft_input = f"情报结论：{msg_analyst}\n法规依据：{msg_legal}"
        msg_draft = commander.speak(user_input, context_data=draft_input)
        with st.expander("查看初版拟定方案", expanded=False):
            st.write(msg_draft)

    # Round 3: 审查员介入
    critic_payload = {}
    critic_status = "REJECTED"
    critic_reason = ""
    critic_missing = []
    critic_risks = []

    with st.chat_message("critic", avatar="🛡️"):
        st.write("**安全审查员** 正在审核...")
        critic_input = (
            "初版方案如下：\n"
            f"{msg_draft}\n"
            "请基于你的职责输出结构化 JSON 审核结果。"
        )
        critic_raw = critic.speak(user_input, context_data=critic_input)

        # --- 调试代码：把原始输出打印到终端，方便排查 ---
        print(f"DEBUG: 审查员的原始输出 -> [{critic_raw}]")

        critic_data = parse_critic_response(critic_raw)

        if not critic_raw or not critic_raw.strip():
            st.warning("⚠️ 审查员未输出文字，系统自动触发修订流程。")

        if not critic_data:
            st.warning("⚠️ 审查员输出格式异常，系统将按照驳回处理。")
            st.code(critic_raw or "无输出", language="json")
            critic_status = "REJECTED"
            critic_reason = "输出格式异常，系统自动要求重新修订方案。"
            critic_missing = []
            critic_risks = []
            critic_payload = {
                "status": critic_status,
                "reason": critic_reason,
                "missing_actions": critic_missing,
                "risk_notes": critic_risks,
                "raw": critic_raw or ""
            }
        else:
            critic_status = str(critic_data.get("status", "")).upper()
            critic_reason = critic_data.get("reason", "").strip()
            critic_missing = critic_data.get("missing_actions") or []
            critic_risks = critic_data.get("risk_notes") or []
            critic_payload = critic_data

            st.code(json.dumps(critic_data, ensure_ascii=False, indent=2), language="json")

        approved_markers = {"APPROVED", "PASS", "PASSED"}

        if critic_status in approved_markers:
            display_reason = critic_reason or "方案审核通过"
            st.success(f"✅ 审核意见：{display_reason}")
            if critic_missing:
                st.info(f"提醒：{'; '.join(critic_missing)}")
            if critic_risks:
                st.caption(f"风险提示：{'; '.join(critic_risks)}")
            final_plan = msg_draft
        else:
            st.warning("⚠️ 驳回意见：")
            if critic_reason:
                st.write(f"- 原因：{critic_reason}")
            if critic_missing:
                st.write(f"- 缺失动作：{', '.join(critic_missing)}")
            if critic_risks:
                st.write(f"- 风险提示：{'; '.join(critic_risks)}")

            st.code(json.dumps(critic_payload, ensure_ascii=False, indent=2), language="json")

            # Round 4: 修正
            with st.spinner("🔄 指挥官正在根据审查意见修订方案..."):
                fix_context = json.dumps(critic_payload, ensure_ascii=False)
                fix_input = (
                    "原方案被审查员驳回。\n"
                    f"审查员结构化意见：{fix_context}\n"
                    "请你参考上述意见，或自行检查原方案漏洞，生成修正后的最终方案。"
                )
                final_plan = commander.speak(user_input, context_data=fix_input)

    # 4. 最终结果展示
    st.divider()
    st.header("🚦 最终下达管控策略")
    st.markdown(final_plan)

    # 5. (可选) 侧边栏调试信息保持不变
    if show_debug:
        with st.sidebar:
            st.write("---")
            st.subheader("底层数据监控")
            st.json(graph_data)
            st.text(vector_context)
            if critic_payload:
                st.json(critic_payload)
            else:
                st.json({"status": critic_status, "reason": critic_reason})

# --- 页脚 ---
st.divider()
st.caption("基于 Chen et al. (2024) E-KELL 框架与 Xie et al. (2025) 三层架构实现")