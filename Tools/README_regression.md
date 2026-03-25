# 最小回归工具说明

## 文件

- `Tools/check_pipeline_regression.py`

## 用途

用于批量验证当前 `PipelineOrchestrator` 主链路在固定事故样例集上的行为，形成一份最小回归基线。

## 当前覆盖场景

- 普通事故伤员
- 雨天高速起火
- 危化品泄漏
- 隧道多车追尾
- 桥梁客车事故

## 输出字段

脚本会输出两部分 JSON：

1. `summary`
   - 样例总数
   - 成功数 / 失败数
   - `incident_type_raw` 非空案例数
   - 匹配到标准事件的案例数
   - 生成非空方案的案例数
   - 审查通过案例数
   - 人工交接案例数

2. `cases`
   - 每个案例的抽取、匹配、检索、生成、审查结果摘要

## 运行方式

建议在项目根目录执行：

```powershell
& "e:\miniconda\envs\traffic_env\python.exe" "Tools\check_pipeline_regression.py"
```

如需快速观察回归结果，可临时降低超时：

```powershell
$env:DISPATCHER_TEXT_TIMEOUT_SECONDS='5'
$env:SEVERITY_TEXT_TIMEOUT_SECONDS='5'
$env:MATCHER_TEXT_TIMEOUT_SECONDS='5'
$env:COMMANDER_TEXT_TIMEOUT_SECONDS='5'
$env:EVALUATOR_TEXT_TIMEOUT_SECONDS='5'
& "e:\miniconda\envs\traffic_env\python.exe" "Tools\check_pipeline_regression.py"
```
