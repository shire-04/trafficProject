# G1-G5 实验组定义（权威）

本文件用于固化评测分组口径。后续所有结果解读、论文图表、回归对比均以本定义为准。

## 固定映射

1. G1 = single + neo4j
2. G2 = single + chroma
3. G3 = single + dual
4. G4 = multi_no_review + dual
5. G5 = multi_with_review + dual

## 参数对应

- retrieval_mode: neo4j | chroma | dual
- agent_mode: single | multi_no_review | multi_with_review | auto

## Auto 模式说明（不纳入 G1-G5 固定口径）

- `agent_mode=auto` 采用动态路由：
- easy -> G3 链路（single_agent + dual）
- medium/hard -> G5 链路（multi_with_review + dual）
- 该模式适合线上运行与工程验证；论文分组对比仍建议使用固定 G1-G5。

## 10样本复现实验命令（Windows PowerShell）

在项目根目录执行：

```powershell
$runDir = "experiments/results/matrix_g1_g5_" + (Get-Date -Format "yyyyMMdd_HHmmss")
New-Item -ItemType Directory -Path $runDir -Force | Out-Null

# 评分后端可按需要切换 llm/rules/hybrid
$env:EVAL_SCORE_BACKEND='llm'
$env:EVAL_ENABLE_LLM_JUDGE='1'
$env:EVAL_LLM_JUDGE_WEIGHT='0.35'
$env:EVAL_LLM_JUDGE_MODEL='xopdeepseekv32'

& "e:\miniconda\envs\traffic_env\python.exe" "Tools\run_eval_experiments.py" --group-id G1 --retrieval-mode neo4j --agent-mode single --limit 10 --split test --output "$runDir/G1_10.csv"
& "e:\miniconda\envs\traffic_env\python.exe" "Tools\run_eval_experiments.py" --group-id G2 --retrieval-mode chroma --agent-mode single --limit 10 --split test --output "$runDir/G2_10.csv"
& "e:\miniconda\envs\traffic_env\python.exe" "Tools\run_eval_experiments.py" --group-id G3 --retrieval-mode dual --agent-mode single --limit 10 --split test --output "$runDir/G3_10.csv"
& "e:\miniconda\envs\traffic_env\python.exe" "Tools\run_eval_experiments.py" --group-id G4 --retrieval-mode dual --agent-mode multi_no_review --limit 10 --split test --output "$runDir/G4_10.csv"
& "e:\miniconda\envs\traffic_env\python.exe" "Tools\run_eval_experiments.py" --group-id G5 --retrieval-mode dual --agent-mode multi_with_review --limit 10 --split test --output "$runDir/G5_10.csv"
```

## 说明

- 如模型接口出现瞬时错误，建议按组重跑单个命令，不改变组定义。
- 若临时做探索性分组，请使用其他 group_id（例如 G3X/G4X），避免覆盖本文件定义。