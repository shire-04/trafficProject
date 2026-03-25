# Event 别名表说明

## 文件
- `data_clean/event_aliases.csv`：`Event` 节点别名/同义表达表。

## 设计目的
这张表同时服务两个链路：
1. `实体抽取 -> 标准实体匹配`
2. `图谱标准表达 -> Chroma 原文证据召回`

## 字段说明
- `entity_id`：对应 `国家交通应急预案_neo4j_nodes.csv` 中的事件节点 ID。
- `entity_name`：标准事件名称。
- `entity_type`：当前固定为 `Event`。
- `alias`：标准事件的别名、原文变体、场景化说法。
- `alias_type`：别名类型，当前包括 `official_variant`、`text_expression`、`scene_expression`。
- `source`：别名来源，当前为 `国家交通应急预案+人工整理`。

## 使用建议
- 在 `EntityMatcherAgent` 中，将标准节点名与别名一起提供给匹配模型。
- 在 `RetrievalLogicAgent` 中，用标准节点名 + 别名扩展 Chroma 检索查询词。
- 后续可继续扩展到 `Action`、`Resource` 节点，但建议先稳定 `Event` 链路。

## 校验
可运行 `Tools/check_event_aliases.py`，检查所有 `Event` 节点是否都已在别名表中覆盖。
