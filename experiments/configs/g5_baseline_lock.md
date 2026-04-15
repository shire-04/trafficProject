# G5 基线锁定配置

用于保证评测可复现。

## 固定运行参数

- `TRAFFIC_AGENT_MODE=multi_with_review`
- `TRAFFIC_RETRIEVAL_MODE=dual`
- `EVAL_SCORE_BACKEND=llm`
- `EVAL_ENABLE_LLM_JUDGE=1`
- `EVAL_LLM_JUDGE_WEIGHT=0.35`
- `EVAL_LLM_JUDGE_MODEL=xopdeepseekv32`

## 审查模式开关（去硬编码迁移期）

- `EVALUATOR_REVIEW_MODE=llm_only`：仅 LLM 审查（当前默认）
- `EVALUATOR_REVIEW_MODE=hybrid`：规则+LLM 双轨（兼容老链路）
- `EVALUATOR_REVIEW_MODE=rules_only`：仅规则审查（仅用于诊断）

## 规则关键词与阈值（可配置）

- `EVALUATOR_RULE_ACTION_THRESHOLD` 默认 `0.42`
- `EVALUATOR_RULE_INJURY_THRESHOLD` 默认 `0.38`
- `EVALUATOR_RULE_FIRE_THRESHOLD` 默认 `0.38`
- `EVALUATOR_RULE_LEAK_THRESHOLD` 默认 `0.38`

- `EVALUATOR_RULE_FIRE_KEYWORDS` 默认 `起火,燃烧,火情`
- `EVALUATOR_RULE_LEAK_KEYWORDS` 默认 `泄漏,危化,油品`
- `EVALUATOR_RULE_INJURY_ACTIONS` 默认 `医疗救治,伤员救治`
- `EVALUATOR_RULE_FIRE_ACTIONS` 默认 `灭火处置,消防介入`
- `EVALUATOR_RULE_LEAK_ACTIONS` 默认 `泄漏围控,封堵处置`

## 生成器后处理（可配置）

- `COMMANDER_FALLBACK_STEPS` 默认内置5条兜底步骤；可用逗号分隔覆盖。
- `COMMANDER_NORMALIZE_WEAK_TOKENS` 默认 `启动,复核,联动,协同,落实,处置`
- `COMMANDER_NORMALIZE_STRONG_TOKENS` 默认 `封控,分流,救治,灭火,封堵,转运,清障,排险,警戒,复通`
- `COMMANDER_NORMALIZE_REWRITE_TEMPLATE` 默认 `步骤{index}：由现场责任单位执行具体现场动作，并记录完成条件与时限。`
