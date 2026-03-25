# 项目交接说明（Handoff）

## 1. 文档目的

本文档用于向下一个 LLM 或接手开发者说明：

1. 项目的当前目标与不可违背的设计边界
2. 当前代码基线已经完成了什么
3. 当前主链路真实可用到什么程度
4. 现在还剩哪些问题最值得继续推进
5. 接手后应该按什么顺序继续做，而不是重新摸索一遍

这是一份**面向当前真实状态**的交接文档，不再保留已经过时的本地模型主路线叙述。

---

## 2. 项目定位与总体目标

本项目是一个**LLM 主导、Neo4j + ChromaDB 双库增强的交通事故应急决策支持系统**。

目标是在用户输入事故文本或图片后，完成以下链路：

1. 事故信息理解与结构化抽取
2. 事件标准化匹配
3. 知识图谱逻辑链检索
4. 向量数据库原文证据检索
5. 单方案应急处置生成
6. 方案审查与一次自动修订

双库分工如下：

- **Neo4j 知识图谱**：提供结构化逻辑链、动作、资源、流程和约束
- **ChromaDB 向量数据库**：提供法规、预案、案例等文本证据

系统不是规则专家系统，而是：

> **由 LLM 负责理解、匹配、生成与审查，由图谱和向量库负责增强。**

---

## 3. 已确认的设计原则

这些原则是用户已经明确确认过的，后续开发必须继续遵守。

### 3.1 LLM 主导，不退回规则主导

允许的只是少量技术性容错，不允许把业务判断重新硬编码进主链路。

当前允许的“匹配”只有两类：

1. **提取实体 -> 实体/别名索引表**
2. **知识图谱逻辑链条 -> 向量数据库原始文本证据**

不允许做的事情包括：

- 用关键词规则直接推断 `incident_type`
- 用本地模式表替代 LLM 抽取
- 用静态规则偷偷替代生成或审查

### 3.2 图谱不是实例定级器

`CLASSIFIED_AS` 这类静态图谱边不再承担本次事故的具体定级工作。

当前规则是：

- `severity` 由 `DispatcherAgent` 内的独立 LLM 子任务完成
- `RetrievalLogicAgent` 只接收这一结果，不再二次做图谱定级融合

### 3.3 抽取与标准化必须分层

当前必须坚持：

- `DispatcherAgent` 输出开放式语义 `incident_type_raw`
- `EntityMatcherAgent` 再把它映射到标准 `Event` 节点

不要再把“抽取”和“标准化”混回一个 Agent。

### 3.4 槽位结构可以约束，但字段值表达应保持开放

允许约束输出字段，例如：

- `incident_type`
- `weather`
- `hazards`
- `vehicles`
- `location_features`
- `casualties`

但不应在抽取阶段把字段值限制成固定枚举题。

### 3.5 别名表属于语义索引，不是业务规则库

当前 `data_clean/event_aliases.csv` 的定位已经明确：

- 它是 `Event` 节点的别名/同义表达索引层
- 用于 `EntityMatcherAgent` 候选表达增强
- 用于 `RetrievalLogicAgent` 生成更好的 query terms 和 evidence query

它不是“绕过 LLM 的硬编码规则库”。

---

## 4. 当前主链路与模块职责

当前主线为：

`DispatcherAgent -> EntityMatcherAgent -> RetrievalLogicAgent -> CommanderAgent -> EvaluatorAgent -> PipelineOrchestrator`

### 4.1 `DispatcherAgent`

职责：

- 解析文本输入
- 可选解析图片输入
- 输出开放式实体抽取结果
- 独立完成严重级别判定

当前关键输出字段：

- `incident_type_raw`
- `incident_type`
- `severity`
- `severity_reason`
- `severity_confidence`
- `weather`
- `hazards`
- `vehicles`
- `location_features`
- `casualty_estimate`
- `evidence_from_image`
- `extract_confidence`

### 4.2 `EntityMatcherAgent`

职责：

- 根据 `incident_type_raw` 做标准事件匹配
- 输出 `matched_events`
- 用最高置信候选覆盖标准 `incident_type`

当前最新基线：

- **不再直接依赖 `neo4j_nodes.csv` 作为 matcher 主索引**
- **当前只读取 `data_clean/event_aliases.csv` 构造候选目录**
- 候选中会包含 `node_id`、`name`、`entity_type`、`aliases`

### 4.3 `RetrievalLogicAgent`

职责：

- 基于标准事件、别名、定级和其他实体信息做双库检索
- 形成 `RetrievalContext`

当前关键点：

- 不再使用 `CLASSIFIED_AS` 做图谱定级
- 优先使用 `matched_events` 查询图谱
- 使用 `incident_type_raw + aliases + hazards + vehicles + location_features + severity` 扩展查询词
- Chroma 证据召回与图谱链条联合为后续生成提供上下文

### 4.4 `CommanderAgent`

职责：

- 基于事件实体、图谱链条和文本证据生成单方案处置策略

输出：

- `focus`
- `steps`
- `required_resources`
- `legal_references`

当前为 LLM 主路径，无业务规则兜底。

### 4.5 `EvaluatorAgent`

职责：

- 审查方案是否覆盖关键风险与核心动作
- 决定 `APPROVED` 或 `REJECTED`
- 输出缺失动作、风险说明和失败类型

当前为 LLM 主路径，无业务规则兜底。

### 4.6 `PipelineOrchestrator`

职责：

- 串接上述各 Agent
- 负责一次自动修订重试
- 输出最终 `PipelineResult`

---

## 5. 当前关键代码基线

### 5.1 `src/llm_provider.py`

这是当前最重要的基础设施变化之一。

当前真实情况：

- 已**移除主链路对本地模型守护进程的依赖**
- 当前默认 provider 为 `google_ai_studio`
- 通过 Google AI Studio 的 `models.generateContent` REST 接口调用模型
- 支持 `.env` 自动加载
- 兼容用户当前写法：`env:GEMINI_API_KEY=...`
- 支持 `GEMINI_API_KEY`，兼容 `GOOGLE_API_KEY`
- 支持 `TRAFFIC_LLM_MODEL`
- 将 `gemma3` / `gemma-3` 自动映射到 `gemma-3-27b-it`
- 对 Gemma 自动关闭 `systemInstruction`
- 对 Gemma 自动关闭 Google JSON mode，改由项目侧解析返回 JSON

这意味着：

> 当前项目的 LLM 基线已经切换为“Google AI Studio 兼容与输出稳定性问题”。

### 5.2 `src/agents.py`

当前核心变化：

- 所有文本类 LLM 调用已经走 `generate_json_response(...)`
- `_extract_json_object()` 已能解析 fenced JSON
- `DispatcherAgent`、`CommanderAgent`、`EvaluatorAgent` 都已基于 Google provider 工作
- `EntityMatcherAgent` 已接入别名索引
- 调试日志已经包含：
  - `llm_chat_result`
  - `dispatcher_extract_result`
  - `entity_match_payload`
  - `entity_match_success`
  - `commander_generate_success`
  - `evaluator_review_success`

### 5.3 `src/entity_aliases.py`

作用：

- 加载 `data_clean/event_aliases.csv`
- 提供 `get_aliases()`
- 提供 `build_matcher_index()`

这是当前匹配与检索共享的语义索引层。

### 5.4 `src/retrieval_logic.py`

当前已经接入别名层：

- query terms 会加入事件别名扩展
- evidence query 会加入别名表达，提升 Chroma 证据召回概率
- 仍保留最小技术回退，但不应再扩展为业务规则系统

### 5.5 `data_clean/event_aliases.csv`

当前状态：

- 已覆盖全部 `50` 个 `Event` 节点
- 每个事件有 3-4 个别名或同义表达
- 已通过 `Tools/check_event_aliases.py` 验证覆盖率 `50/50`

### 5.6 `Tools/check_google_dispatcher.py`

作用：

- 以 UTF-8 独立脚本方式验证 Google 模型下 `DispatcherAgent` 的中文抽取能力
- 避开 PowerShell 管道向 Python stdin 传中文时可能出现的编码干扰

### 5.7 `Tools/check_orchestrator.py`

作用：

- 当前最重要的端到端单案例验证入口
- 可直接验证 `extract -> match -> retrieve -> generate -> review`

### 5.8 `Tools/check_pipeline_regression.py`

作用：

- 当前最小多案例回归基线
- 包含 5 个固定案例
- 可用于比较不同模型或提示词调整后的整体效果

---

## 6. 已完成的关键工作

以下内容已经完成，并且属于当前代码基线的一部分。

### 6.1 去硬编码与架构收束

- 删除了 `DispatcherAgent` 内的事故类型、天气、风险、车辆、位置等硬编码模式表
- 删除了抽取、生成、审查阶段的业务规则兜底
- 停用了图谱 `CLASSIFIED_AS` 在主链路中的定级作用
- 明确将标准化职责收束到 `EntityMatcherAgent`

### 6.2 建立最小回归与可观测性基线

- 新增 `Tools/check_pipeline_regression.py`
- 新增 `Tools/README_regression.md`
- 当前关键链路日志已足够看清：抽取、匹配、检索、生成、审查分别返回了什么

### 6.3 建立 Event 别名索引层

- 新增 `data_clean/event_aliases.csv`
- 新增 `data_clean/README_event_aliases.md`
- 新增 `src/entity_aliases.py`
- 新增 `Tools/check_event_aliases.py`
- 完成 `50/50` 全覆盖校验

### 6.4 将别名层接入匹配与检索

- `EntityMatcherAgent` 只读取别名索引表作为候选目录
- `RetrievalLogicAgent` 用别名扩展 query terms
- `RetrievalLogicAgent` 用别名扩展 evidence query

### 6.5 将底层 LLM 切换到 Google AI Studio

- 新增 `src/llm_provider.py`
- `src/agents.py` 中各类文本调用已切换到 Google provider
- 已支持 `.env` 自动加载和 `env:` 前缀兼容

---

## 7. 当前真实验证结果

这一部分最关键，供下一个 LLM 快速判断“项目到底已经到了哪一步”。

### 7.1 `DispatcherAgent` 已在 Gemma 路线上跑通

已实际运行 `Tools/check_google_dispatcher.py`，仅依赖 `.env` 配置，结果如下：

- `.env` 中的 `env:GEMINI_API_KEY=...` 能被自动加载
- `.env` 中的 `env:TRAFFIC_LLM_MODEL="gemma3"` 能被自动映射为 `gemma-3-27b-it`
- `DispatcherAgent` 对 3 个中文案例均成功输出可解析 JSON
- 严重级别判定子任务也已成功返回结构化 JSON

换句话说：

> `gemma3 -> gemma-3-27b-it` 这条 Google 路线已经不再停留在“接口通了”，而是已经能完成真实中文抽取。

### 7.2 单案例主链路已在 Gemma 路线上完整跑通

已实际运行 `Tools/check_orchestrator.py`，仅依赖 `.env` 配置，端到端成功。

一次实际结果要点如下：

- `incident_type_raw = 高速公路交通事故`
- 主匹配节点：`EVT_ROAD_EMERGENCY`
- `matched_events = 3`
- `constraint_count = 34`
- `evidence_count = 5`
- `step_count = 8`
- `resource_count = 7`
- `review_status = APPROVED`
- `retry_count = 0`

这说明当前链路：

- 抽取有输出
- 匹配有输出
- 双库检索有输出
- 方案生成有输出
- 审查也有输出

### 7.3 当前最大问题已经变化

当前最大的阻塞**不再是“链路为空”**，而是：

1. `EntityMatcherAgent` 的候选排序是否总能选到最贴切的标准事件
2. 多案例回归下的稳定性是否足够
3. Gemma 与 Gemini 在配额、输出稳定性、成本上的取舍
4. 是否还存在少量历史残留逻辑会误导后续开发

---

## 8. 当前模型与环境现状

### 8.1 当前推荐环境变量

当前可工作的最小环境变量是：

- `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`
- `TRAFFIC_LLM_MODEL`
- 可选：`TRAFFIC_LLM_PROVIDER=google_ai_studio`

当前 `.env` 已支持如下非标准写法：

```env
env:GEMINI_API_KEY="<your_key>"
env:TRAFFIC_LLM_MODEL="gemma3"
```

### 8.2 当前模型验证情况

已验证过的情况：

- `gemini-2.5-flash`：可真实运行，但免费层配额有限
- `gemini-2.5-pro`：当前账号上曾返回 `429 RESOURCE_EXHAUSTED`
- `gemini-1.5-flash`：当前账号/API 路径下曾出现 `404 NOT_FOUND`
- `gemma-3-27b-it`：当前已被证明可完成中文抽取与单案例主链路执行

### 8.3 Gemma 的特殊限制

当前代码已经兼容的限制：

- Gemma 不支持 `systemInstruction`
- Gemma 不支持 Google JSON mode

因此项目当前做法是：

- 将 system prompt 与 user prompt 拼接进普通 `contents`
- 关闭 Google 的 `responseMimeType=application/json`
- 由项目侧 `_extract_json_object()` 从文本中抽取 JSON

---

## 9. 仍存在的问题与风险

### 9.1 匹配排序仍值得继续优化

虽然主链路已跑通，但当前案例中首选命中的是 `EVT_ROAD_EMERGENCY`，而不是更具体的事故节点。

这不代表系统不可用，但说明：

- 别名索引层仍可继续细化
- matcher 候选裁剪与排序仍有优化空间
- 需要基于固定案例做误匹配分析，而不是凭感觉调整 prompt

### 9.2 多案例回归尚未在新 Google 基线上完整重跑

单案例已经成功，但 `Tools/check_pipeline_regression.py` 还没有在当前 `.env + gemma3` 基线上重新完整跑出新报告。

这意味着还不能过早宣称“已经稳定生产可用”。

### 9.3 仍有历史模块需要继续弱化存在感

虽然后续已清理了不少旧逻辑，但仍建议继续审视：

- `src/reasoning_engine.py`
- `src/vectorDB.py`

目标不是马上大改，而是避免这些旧模块的残留表达误导下一个接手者，以为主链路仍然依赖这些旧思路。

### 9.4 Google 配额与模型路线仍需现实约束

后续如果要做大量回归测试，需要提前考虑：

- 免费层配额是否够用
- 是否需要切到更稳妥的低成本模型
- 是否要加入 429 限流重试

---

## 10. 接手后的优先执行顺序

建议下一个 LLM 按以下顺序继续推进，而不是重新发散。

### 第一步：重跑最小多案例回归

优先使用：

- `Tools/check_pipeline_regression.py`

目标：

- 建立 Google + Gemma 当前基线下的真实通过率
- 统计 `matched_event_case_count`
- 统计 `approved_case_count`
- 判断问题主要卡在匹配、生成还是审查

### 第二步：专项分析 matcher 排序质量

重点不是“有没有命中”，而是“为什么排序成这样”。

建议分析：

- 错误 Top1 的案例有哪些共同模式
- 别名表是否缺少更贴近用户表达的短语
- shortlist 长度是否过长或候选噪声过大

### 第三步：根据回归结果微调检索与 prompt

如果问题主要出在：

- **匹配阶段**：优先调别名和候选排序
- **检索阶段**：优先调 query terms 与 evidence query
- **生成阶段**：再调 `CommanderAgent` prompt
- **审查阶段**：再调 `EvaluatorAgent` 的审查边界

### 第四步：再考虑更广义的工程收尾

例如：

- 补更清晰的模型配置文档
- 加入 429 重试与调用失败诊断
- 进一步削弱旧模块的误导性入口

---

## 11. 接手时不要做的事

这是给下一个 LLM 的明确提醒。

### 11.1 不要把业务规则重新塞回代码

尤其不要再做：

- `危化品 + 泄漏 => 强制匹配某节点`
- `隧道 + 多车 + 被困 => 强制定级`
- `起火 => 强制方案补某动作`

### 11.2 不要把别名表误用成规则表

别名表可以扩展语义表达，但不应承担业务决策逻辑。

### 11.3 不要把图谱静态边重新当成实例判断器

尤其不要恢复用 `CLASSIFIED_AS` 给当前事故自动定级。

### 11.4 不要因为单案例成功就停止回归

当前已经证明“能跑通”，但还没有证明“多案例下稳定”。

---

## 12. 一句话总结当前项目状态

当前项目已经完成向“Google AI Studio + LLM 主导 + Event 别名索引增强 + 双库联合检索”的关键迁移；`gemma3` 在当前 `.env` 配置下已能跑通中文抽取和单案例编排，下一阶段最值得做的是**基于固定案例重建多案例回归基线，并专项优化 matcher 排序质量**。
