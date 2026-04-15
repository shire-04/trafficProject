# 实验模板最小使用说明

## 目录说明
- `eval_dataset_template.jsonl`：评测样本模板（每行一个样本）。
- `judge_rubric_template.md`：评分规范模板（人工/LLM评审共用）。
- `experiment_results_template.csv`：结果汇总模板（每行一个样本在某组实验的结果）。

## 推荐使用顺序
1. 复制 `eval_dataset_template.jsonl` 为 `experiments/eval_dataset_v1.jsonl` 并补齐样本。
2. 固化 `judge_rubric_template.md` 为你的论文评分标准（不要频繁改）。
3. 每跑完一个实验组，将结果按行追加到 `experiment_results_template.csv` 对应副本（如 `results_v1.csv`）。

## 字段填写要点
### 1) 样本模板（JSONL）
- `sample_id`：唯一ID，建议 `S001` 连续编号。
- `difficulty`：`easy|medium|hard`。
- `must_actions`：必须出现的动作列表。
- `critical_actions`：漏掉即判重大缺陷。
- `forbidden_actions`：出现即高风险。

### 2) 结果模板（CSV）
- `group_id`：实验组标识（如 G1/G2/G3）。
- `retrieval_mode`：`neo4j|chroma|dual`。
- `agent_mode`：`single|multi_no_review|multi_with_review|auto`。
- `constraint_coverage`：范围建议 [0,1]。
- `critical_miss_rate`：范围建议 [0,1]。

> `auto` 为线上动态路由模式：easy→G3(single_agent)，medium/hard→G5(multi_with_review)。
> 若用于论文分组对比，建议仍按 G1-G5 固定口径独立运行。

## 最小实验矩阵建议
- G1: `single + neo4j`
- G2: `single + chroma`
- G3: `single + dual`
- G4: `multi_no_review + dual`
- G5: `multi_with_review + dual`（主方案）

## 论文中建议说明
- 你使用了固定Rubric + 盲评；
- LLM评审仅作为近似评价；
- 抽样人工复核用于校准评审偏差。
