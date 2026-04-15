# 评测 Rubric 模板（人工/LLM 通用）

## 一、使用范围
- 目标：评估处置方案是否满足“可执行、安全、证据一致、覆盖关键约束”。
- 适用对象：
  - 人工评审（你/同学）
  - LLM-as-a-Judge（如更强模型）

## 二、输入材料
每条样本评测时必须提供：
1. 样本原文（incident_text）
2. 系统输出策略（focus + steps + resources + legal references）
3. 检索证据摘要（图谱约束 + 向量证据）
4. 参考检查清单（must_actions / critical_actions / must_constraints）

## 三、评分维度（1~5分）

### 1) 可执行性（Executability）
- 5分：步骤顺序清晰，可直接执行，责任主体明确。
- 3分：有可执行主线，但部分步骤模糊或顺序欠合理。
- 1分：步骤笼统，缺少执行条件或主体。

### 2) 安全性（Safety）
- 5分：关键风险控制完整，无明显危险建议。
- 3分：总体安全，但存在关键防护缺口。
- 1分：存在明显高风险缺失或不当建议。

### 3) 约束一致性（Constraint Alignment）
- 5分：基本覆盖 must_constraints 与 must_actions。
- 3分：覆盖部分关键约束，仍有漏项。
- 1分：与关键约束明显不一致。

### 4) 证据一致性（Evidence Grounding）
- 5分：关键结论有检索证据支撑，引用合理。
- 3分：部分有证据，部分靠泛化推断。
- 1分：大量结论无证据支撑或与证据冲突。

## 四、二值检查项（必须记录）
- critical_action_missed：是否漏掉任一 critical_actions（0/1）
- has_forbidden_action：是否出现 forbidden_actions（0/1）
- approved_like：是否达到可下发标准（0/1）

## 五、综合评分与结论
- total_score = executability + safety + constraint_alignment + evidence_grounding（满分20）
- 推荐阈值：
  - total_score >= 16 且二值检查均为0 -> 通过
  - 否则 -> 不通过

## 六、LLM评审降偏建议（强烈建议写入论文）
1. 盲评：不向评审模型暴露实验组名（A/B/C...）。
2. 固定提示词：所有组使用同一评审提示。
3. 双评审：至少两个评审来源（例如 LLM + 人工抽检）。
4. 抽检一致性：随机抽取10%样本做人工复核，报告一致率。
