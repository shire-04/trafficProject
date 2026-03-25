# Legacy Rules Tools

这些脚本属于**历史规则/关键词驱动工具**，不属于当前项目主链路。

## 说明

当前项目主链路遵循以下边界：

1. `LLM` 负责实体抽取、方案生成与审查
2. `EntityMatcherAgent` 负责“提取实体 -> 实体表”的匹配
3. `Neo4j` 提供结构化逻辑链条
4. `ChromaDB` 提供原始文本证据

本目录下脚本大多采用以下方式工作：

- 基于关键词的规则匹配
- 基于映射表的关系补全
- 基于规则的分类导出或统计分析

因此，这些脚本被迁移到 `Tools/legacy_rules/`，以避免继续与当前主链路设计混用。

## 当前迁入脚本

- `enrich_graph_resources.py`
- `enrich_graph_consists_of.py`
- `export_actions_categorized.py`
- `analyze_actions.py`

## 使用建议

- 仅在需要复盘历史数据处理方法时参考
- 不应作为当前主链路推理、匹配、生成或审查逻辑的实现依据
- 若后续决定彻底弃用，可直接删除本目录下相关脚本
