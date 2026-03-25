# 数据导入脚本（统一目录）

本目录用于集中管理“知识图谱 + 向量库”的数据导入脚本，匹配当前项目的双库架构。

## 当前匹配结论

- `import_national_plan_to_neo4j.py`：**匹配当前架构**
  - 使用 `data_clean/neo4j_import/*.csv`
  - 以 `id` 为主键导入节点与关系，符合当前图谱身份规则
- `rebuild_production_chromadb.py`：**匹配当前架构**
  - 写入 `chroma_data` 下 `traffic_documents_v2`
  - 默认仅导入 `data_raw/国家交通应急预案.txt`，与正式双库范围一致
- `import_case_triples_to_neo4j.py`：**用于补充经典案例三元组**
  - 支持从 `data_raw/极端重大交通事故案例.csv` 导入
  - 用于在现有图谱基础上追加案例关系
- `extract_case_entities_to_csv.py`：**高优先级实体抽取入口**
  - 从事故案例 `txt` 中抽取实体并输出 `CSV + JSONL`
  - `Event` 支持“主命中 + 多个扩展命中”
  - 输出正式化所需的临时键、最终 `id`、别名补丁目标与复核状态
- `extract_case_relations_to_csv.py`：**高优先级关系抽取入口**
  - 从事故案例 `txt` 中抽取关系并输出 `CSV`
  - 输出关系两端的临时键与最终 `id` 映射信息，便于后续统一转成 `id -> id`

## 为什么要新增案例导入脚本

`src/sync_graph_vector.py` 是旧版脚本，存在以下问题：

- 使用硬编码绝对路径
- 默认连接参数写死在脚本内
- 仅按 `name` 合并节点，不符合当前“结构化主图谱以 `id` 为主键”的导入规范

因此当前推荐只通过本目录脚本执行导入。

## 为什么新增 TXT 抽取脚本

你当前能拿到的经典事故案例主要是 `txt`，而不是已经整理好的三元组 CSV。

为了保证图谱补数质量，这里把处理链路拆成两步：

1. **先抽取**：从案例 `txt` 中抽出实体与关系，写入可复核 CSV
2. **再正式化**：将抽取结果整理为新增节点、正式关系、事件别名补丁
3. **再导入**：人工抽查后，将正式 `id -> id` CSV 导入 Neo4j

这样可以避免“未经审阅的 LLM 结果直接入库”，降低污染主图谱的风险。

## 新增抽取脚本

### 1) 实体抽取

```powershell
python Tools/importData/extract_case_entities_to_csv.py data_raw/案例.txt
```

默认输出：

- `data_clean/case_extract_output/case_entities.csv`
- `data_clean/case_extract_output/case_structured.jsonl`

核心字段：

- `entity_temp_id`
- `entity_name`
- `entity_type`
- `final_entity_id`
- `resolution_type`
- `primary_event_id`
- `expanded_event_ids`
- `normalized_id`
- `normalized_name`
- `normalized_score`
- `review_status`

说明：

- 对 `Event` 而言，`primary_event_id` 是主命中事件，`expanded_event_ids` 是后续查询要一并扩展的事件集合，且**可以有多个**
- 新具体事件不会与上位事件在图谱中建立从属边；扩展命中只存在于匹配/查询层
- `review_status=REVIEW_REQUIRED` 的行建议人工优先检查

### 2) 关系抽取

```powershell
python Tools/importData/extract_case_relations_to_csv.py data_raw/案例.txt
```

默认输出：

- `data_clean/case_extract_output/case_relations.csv`

核心字段：

- `source`
- `source_temp_id`
- `source_final_id`
- `relation`
- `target`
- `target_temp_id`
- `target_final_id`
- `source_type`
- `target_type`
- `confidence`
- `review_status`

当前只允许输出以下关系类型：

- `CAUSES`（`Event -> Event`）
- `TRIGGERS`（`Event -> Action`）
- `REQUIRES`（`Action -> Resource`）
- `IMPLEMENTED_BY`（`Action -> Department`）

### 3) 正式化抽取结果

```powershell
python Tools/importData/build_case_graph_import_csv.py data_clean/case_extract_output/case_structured.jsonl
```

默认输出：

- `data_clean/case_extract_output/case_new_nodes.csv`
- `data_clean/case_extract_output/case_new_relationships.csv`
- `data_clean/case_extract_output/event_aliases_patch.csv`
- `data_clean/case_extract_output/normalization_audit.csv`

### 4) 导入复核后的正式 CSV

```powershell
python Tools/importData/import_case_triples_to_neo4j.py data_clean/case_extract_output/case_new_nodes.csv data_clean/case_extract_output/case_new_relationships.csv neo4j
```

导入脚本当前默认还会执行两件事（除非显式关闭）：

- 回写节点基线：`data_clean/neo4j_import/国家交通应急预案_neo4j_nodes.csv`
- 合并事件别名补丁：`<relationships_csv同目录>/event_aliases_patch.csv -> data_clean/event_aliases.csv`

常用开关：

- `--skip-refresh-node-baseline`：跳过节点基线回写
- `--skip-merge-event-aliases`：跳过别名补丁合并
- `--event-alias-patch-csv`：指定别名补丁文件
- `--event-aliases-csv`：指定别名总表文件

## 质量建议

- 不要将抽取结果“零审阅”直接入正式图谱
- 优先复核 `REVIEW_REQUIRED` 和低 `confidence` 行
- 优先复核 `normalization_audit.csv` 中 `Event` 的 `resolution_type`、`primary_event_id`、`expanded_event_ids`
- 先用 5-10 个代表性案例抽取，人工修正提示与字段，再批量处理
- 对高价值案例建议保留 `case_structured.jsonl`，便于后续回溯抽取依据

## 标准批次 SOP（固定执行）

每次新增案例数据，固定按以下顺序执行：

1. 抽取（实体 + 关系）
2. 质量门控（正式化）
3. Neo4j 导入（必须带批次 `source_tag`，并自动同步节点基线+别名表）
4. Chroma 重建/更新
5. 双库一致性核验

前置规则（强制）：

- 若刚执行过批次回滚，必须先从 Neo4j 刷新一次节点基线 CSV，再进行后续抽取与正式化，避免“基线过期”导致端点错配。

### 1) 抽取（按批次目录隔离）

```powershell
python Tools/importData/extract_case_entities_to_csv.py data_raw/交通应急处理案例3.txt --output-dir data_clean/case_extract_output/batches/case3_20260320 --force
python Tools/importData/extract_case_relations_to_csv.py data_raw/交通应急处理案例3.txt --output-dir data_clean/case_extract_output/batches/case3_20260320 --force
```

### 2) 门控与正式化

```powershell
python Tools/importData/build_case_graph_import_csv.py data_clean/case_extract_output/batches/case3_20260320/case_structured.jsonl --output-dir data_clean/case_extract_output/batches/case3_20260320 --max-empty-relation-ratio 0.30 --fail-on-empty-relation-ratio
```

### 3) Neo4j 导入（批次标签必填）

```powershell
python Tools/importData/import_case_triples_to_neo4j.py data_clean/case_extract_output/batches/case3_20260320/case_new_nodes.csv data_clean/case_extract_output/batches/case3_20260320/case_new_relationships.csv neo4j --source-tag "案例抽取导入:案例3:20260320" --graph-version "case_v2_quality_gated"
```

说明：

- `--source-tag` 用于批次隔离与精准回滚，禁止使用单一通用标签覆盖所有批次。
- 建议格式：`案例抽取导入:<批次名>:<YYYYMMDD>`。
- 默认自动执行：
  - 节点基线回写（保持抽取阶段基线与 Neo4j 同步）；
  - 别名补丁合并（将本批次 `event_aliases_patch.csv` 合入 `data_clean/event_aliases.csv`）。
- 若只做演练可使用：`--dry-run`。

### 4) Chroma 更新（补齐文本语料）

```powershell
python Tools/importData/rebuild_production_chromadb.py 国家交通应急预案.txt 交通应急处理案例.txt 交通应急处理案例2.txt 交通应急处理案例3.txt
```

说明：

- 生产建议在每日或每批次结束后执行一次重建，保证图谱与向量语料一致。
- 若当前任务仅验证图谱链路（如单批次回归），可临时跳过此步，但需要在后续补齐重建。

### 5) 双库核验（最少检查项）

- Neo4j：
  - 新批次 `source_tag` 关系数与 CSV 关系行数一致。
  - `缺失端点关系数 = 0`。
  - `国家交通应急预案_neo4j_nodes.csv` 行数与当前图谱规模一致增长（导入后自动回写）。
- 别名：
  - `event_aliases.csv` 已更新，且重复执行同一批次时“新增别名行数”应为 `0`（幂等）。
- Chroma：
  - `traffic_documents_v2` 的 `source_files` 包含本批次对应 `txt`。
  - `total_chunks` 与文件规模变化趋势一致。

## 最新完整流程（推荐一键理解）

以 `交通应急处理案例6.txt` 为例：

```powershell
python Tools/importData/extract_case_entities_to_csv.py data_raw/交通应急处理案例6.txt --output-dir data_clean/case_extract_output/batches/case6_20260320 --force
python Tools/importData/extract_case_relations_to_csv.py data_raw/交通应急处理案例6.txt --output-dir data_clean/case_extract_output/batches/case6_20260320 --force
python Tools/importData/build_case_graph_import_csv.py data_clean/case_extract_output/batches/case6_20260320/case_structured.jsonl --output-dir data_clean/case_extract_output/batches/case6_20260320 --max-empty-relation-ratio 0.30 --fail-on-empty-relation-ratio
python Tools/importData/import_case_triples_to_neo4j.py data_clean/case_extract_output/batches/case6_20260320/case_new_nodes.csv data_clean/case_extract_output/batches/case6_20260320/case_new_relationships.csv neo4j --source-tag "案例抽取导入:案例6:20260320" --graph-version "case_v2_quality_gated"
python Tools/importData/rebuild_production_chromadb.py 国家交通应急预案.txt 交通应急处理案例.txt 交通应急处理案例2.txt 交通应急处理案例3.txt 交通应急处理案例4.txt 交通应急处理案例5.txt 交通应急处理案例6.txt
```

说明：第 4 步执行完成后，会自动打印：

- `节点基线已同步 ...`、`节点基线行数 ...`
- `别名总表已同步 ...`、`新增别名行数 ...`

## 案例2执行模板（本轮：不重建 Chroma）

当你只需要先完成 Neo4j 严格导入验证时，可执行：

```powershell
python Tools/importData/extract_case_entities_to_csv.py data_raw/交通应急处理案例2.txt --output-dir data_clean/case_extract_output/batches/case2_20260320 --force
python Tools/importData/extract_case_relations_to_csv.py data_raw/交通应急处理案例2.txt --output-dir data_clean/case_extract_output/batches/case2_20260320 --force
python Tools/importData/build_case_graph_import_csv.py data_clean/case_extract_output/batches/case2_20260320/case_structured.jsonl --output-dir data_clean/case_extract_output/batches/case2_20260320 --max-empty-relation-ratio 0.30 --fail-on-empty-relation-ratio
python Tools/importData/import_case_triples_to_neo4j.py data_clean/case_extract_output/batches/case2_20260320/case_new_nodes.csv data_clean/case_extract_output/batches/case2_20260320/case_new_relationships.csv neo4j --source-tag "案例抽取导入:案例2:20260320" --graph-version "case_v2_quality_gated"
```

后续补齐 Chroma：

```powershell
python Tools/importData/rebuild_production_chromadb.py 国家交通应急预案.txt 交通应急处理案例.txt 交通应急处理案例2.txt
```

## 使用方式

### 1) 导入国家交通应急预案图谱

```powershell
python Tools/importData/import_national_plan_to_neo4j.py prepare neo4j
python Tools/importData/import_national_plan_to_neo4j.py validate neo4j
```

### 2) 重建生产向量库

```powershell
python Tools/importData/rebuild_production_chromadb.py
```

### 3) 导入经典案例正式节点/关系（增量）

```powershell
python Tools/importData/import_case_triples_to_neo4j.py data_clean/case_extract_output/case_new_nodes.csv data_clean/case_extract_output/case_new_relationships.csv neo4j
```

## 环境变量

支持以下 Neo4j 连接参数（若不设置则使用默认值）：

- `NEO4J_URI`（默认 `bolt://localhost:7687`）
- `NEO4J_USER`（默认 `neo4j`）
- `NEO4J_PASSWORD`
- `NEO4J_DB`（仅 `import_case_triples_to_neo4j.py` 使用，默认 `neo4j`）
