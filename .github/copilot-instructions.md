# AI Coding Agent Instructions - Traffic Emergency Response System (E-KELL)

## 项目现状（请按当前代码实现执行）
本项目是**本地知识图谱 + 向量检索 + LLM 编排**的交通事故应急处置系统。当前主链路为：

1. **Neo4j 图谱检索**：事件、动作、资源等结构化约束。
2. **ChromaDB 检索**：法规与案例文本证据检索。
3. **多 Agent 编排**：由 `PipelineOrchestrator` 串联抽取、匹配、检索、生成、审查。
4. **Streamlit 前端**：输入文本/图片并展示结构化决策结果。

## 明确环境要求（重要）

### 统一运行环境
- 项目默认使用 **Conda 环境：`traffic_env`**。
- 当前项目依赖（含 `neo4j` 驱动）应安装在 `traffic_env` 中。
- 若脚本涉及 `Tools/importData`，建议设置：`$env:PYTHONPATH = "e:\trafficProject\Tools\importData"`。

### 推荐版本与依赖
- Python：建议 `3.10+`（项目当前在 Conda 环境中运行）。
- 依赖来源：`requirements.txt`（`chromadb`、`sentence-transformers`、`numpy`、`neo4j`）。

### 外部服务
- Neo4j 本地服务：默认 `bolt://localhost:7687`。
- LLM API：Google AI Studio，需配置 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`。

### Windows PowerShell 常用启动方式
```powershell
conda activate traffic_env
pip install -r requirements.txt
streamlit run src/app.py
```

## 当前核心架构

### 1) 编排器（主入口）`src/orchestrator.py`
- 核心类：`PipelineOrchestrator`。
- 执行链路：`DispatcherAgent -> EntityMatcherAgent -> RetrievalLogicAgent -> CommanderAgent -> EvaluatorAgent`。
- 审查机制：最多一次自动修订后复审。

### 2) Agents `src/agents.py`
- `DispatcherAgent`：文本/图片联合抽取事故实体。
- `EntityMatcherAgent`：将事故表达规范化到图谱事件节点。
- `RetrievalLogicAgent`：统一拉取 Neo4j 约束 + Chroma 证据。
- `CommanderAgent`：生成单一策略草案。
- `EvaluatorAgent`：结构化审查并返回通过/拒绝。
- 约束：Agent 间通过 `src/contracts.py` 的 dataclass 传递，不走自由对话。

### 3) 检索层 `src/retrieval_logic.py`
- Neo4j 默认连接参数读取环境变量：
  - `NEO4J_URI`（默认 `bolt://localhost:7687`）
  - `NEO4J_USER`（默认 `neo4j`）
  - `NEO4J_PASSWORD`（默认 `trafficv2`）
  - `NEO4J_DATABASE`（默认 `neo4j`）
- 向量库默认使用 `PRODUCTION_COLLECTION_NAME = "traffic_documents_v2"`。

### 4) 向量库 `src/vectorDB.py`
- 数据目录：`./chroma_data`。
- 文本来源：`data_raw/*.txt`。
- 规则：仅处理 TXT，不处理 CSV。
- 默认切块：500 字符；编码：UTF-8 + `errors='ignore'`。
- 集合名：基础 `traffic_documents`，生产默认 `traffic_documents_v2`。

### 5) 前端 `src/app.py`
- Streamlit 页面调用 `PipelineOrchestrator`。
- 支持文本输入 + 图片输入。

## 导入流程（以 `Tools/importData` 为准）
- 权威流程文档：`Tools/importData/README.md`。
- 标准顺序：抽取 -> 正式化门控 -> Neo4j 导入（带 `source_tag`）-> Chroma 重建/补齐 -> 核验。
- 当前规则：
  - 导入后自动同步节点基线 CSV。
  - 导入后自动合并 `event_aliases_patch.csv` 到 `data_clean/event_aliases.csv`。
  - 严格导入默认不允许缺失端点关系（`缺失端点关系数`应为 `0`）。

## LLM 配置现状
- Provider：`google_ai_studio`。
- 默认模型：`gemma-3-27b-it`（见 `src/llm_provider.py`）。
- 兼容别名：`gemma3` / `gemma-3` 会规范化为 `gemma-3-27b-it`。
- 可通过环境变量覆盖：`TRAFFIC_LLM_PROVIDER`、`TRAFFIC_LLM_MODEL`。

## 编码与协作约定
- 语言：Python，建议保留类型标注。
- 注释与文档字符串：使用中文。
- 路径：优先使用项目根目录相对路径。
- 数据分工：
  - Neo4j：结构化图数据（CSV 导入）。
  - ChromaDB：非结构化文本（TXT 向量化）。

## 调试建议
- Agent 级检查：`Tools/check_dispatcher_agent.py`、`Tools/check_retrieval_agent.py`、`Tools/check_commander_agent.py`、`Tools/check_evaluator_agent.py`。
- 编排链路检查：`Tools/check_orchestrator.py`、`Tools/check_orchestrator_with_image.py`、`Tools/check_pipeline_regression.py`。
- 图谱异常时，优先在 Neo4j Browser 复查 Cypher 与节点标签大小写。
