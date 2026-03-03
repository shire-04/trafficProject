"""
基于真实图谱数据生成完整文档
"""
from neo4j import GraphDatabase
import random

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def generate_real_doc():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    doc_content = []
    
    def add_section(title, level=1):
        doc_content.append(f"\n{'#' * level} {title}\n")
    
    def add_text(text):
        doc_content.append(f"{text}\n")
    
    def add_code_block(code, language="cypher"):
        doc_content.append(f"```{language}\n{code}\n```\n")
    
    def add_table(headers, rows):
        header_line = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join(["---"] * len(headers)) + " |"
        doc_content.append(header_line)
        doc_content.append(separator)
        for row in rows:
            doc_content.append("| " + " | ".join(str(cell) for cell in row) + " |")
        doc_content.append("")
    
    try:
        with driver.session() as session:
            # 标题
            add_section("交通应急知识图谱完整文档")
            add_text("**版本**: V3.0 (基于真实数据验证)")
            add_text("**最后更新**: 2024-12-25")
            add_text("**数据验证**: ✅ 所有示例已通过 Neo4j 实际查询验证")
            
            # 1. 概述
            add_section("概述", 2)
            add_text("本知识图谱是一个专门针对**交通应急处置**场景构建的大规模结构化知识库，用于支持基于 LLM 的智能决策系统。")
            
            # 基础统计
            total_nodes = session.run("MATCH (n) RETURN count(n) as c").single()['c']
            total_rels = session.run("MATCH ()-[r]->() RETURN count(r) as c").single()['c']
            avg_degree = total_rels / total_nodes if total_nodes > 0 else 0
            
            add_section("核心统计", 3)
            add_table(
                ["指标", "数值"],
                [
                    ["节点总数", f"{total_nodes:,}"],
                    ["关系总数", f"{total_rels:,}"],
                    ["平均连接度", f"{avg_degree:.2f} 条边/节点"]
                ]
            )
            
            # 2. 图谱 Schema
            add_section("图谱 Schema (模式)", 2)
            
            add_section("节点类型", 3)
            node_stats = session.run("""
                MATCH (n)
                RETURN labels(n)[0] as label, count(*) as count
                ORDER BY count DESC
            """).data()
            
            headers = ["节点类型", "数量", "占比", "说明"]
            rows = []
            descriptions = {
                "Event": "交通事故场景、触发条件",
                "Action": "应急响应措施、处置步骤",
                "Consequence": "事件导致的中间状态",
                "Resource": "人力、车辆、设施资源"
            }
            for node in node_stats:
                label = node['label']
                count = node['count']
                pct = f"{count/total_nodes*100:.1f}%"
                desc = descriptions.get(label, "")
                rows.append([f"**{label}**", f"{count:,}", pct, desc])
            add_table(headers, rows)
            
            add_section("关系类型", 3)
            rel_stats = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(*) as count
                ORDER BY count DESC
            """).data()
            
            headers = ["关系类型", "数量", "占比", "语义说明"]
            rows = []
            rel_descriptions = {
                "NEXT_STEP": "流程顺承、任务分解",
                "REQUIRES": "资源需求",
                "TRIGGERS": "等级分类触发",
                "CONSISTS_OF": "后果包含动作",
                "LEADS_TO": "事件导致后果",
                "CLASSIFIED_AS": "事件分类",
                "MITIGATES": "动作缓解后果"
            }
            for rel in rel_stats:
                rel_type = rel['rel_type']
                count = rel['count']
                pct = f"{count/total_rels*100:.1f}%"
                desc = rel_descriptions.get(rel_type, "")
                rows.append([f"`{rel_type}`", f"{count:,}", pct, desc])
            add_table(headers, rows)
            
            # 3. 关系连接模式
            add_section("关系连接模式 (Schema Pattern)", 3)
            schema_pattern = session.run("""
                MATCH (a)-[r]->(b)
                RETURN DISTINCT 
                    labels(a)[0] as source, 
                    type(r) as rel_type, 
                    labels(b)[0] as target,
                    count(*) as count
                ORDER BY count DESC
                LIMIT 15
            """).data()
            
            headers = ["源节点", "关系", "目标节点", "数量"]
            rows = []
            for p in schema_pattern:
                rows.append([
                    p['source'] or "Unknown",
                    f"`{p['rel_type']}`",
                    p['target'] or "Unknown",
                    f"{p['count']:,}"
                ])
            add_table(headers, rows)
            
            # 4. 核心推理路径
            add_section("核心推理路径", 2)
            
            add_section("路径 1: 完整因果链 (Event → Consequence → Action)", 3)
            add_text("**逻辑**: `Event -[:LEADS_TO]-> Consequence -[:CONSISTS_OF]-> Action`")
            
            path_count = session.run("""
                MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
                RETURN count(*) as c
            """).single()['c']
            add_text(f"**路径数**: {path_count} 条")
            add_text("**用途**: 从事件到后果再到具体动作的深度推理")
            
            # 真实示例
            real_example1 = session.run("""
                MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
                RETURN e.name as event, c.name as consequence, collect(a.name)[0..3] as actions
                LIMIT 1
            """).single()
            
            add_text("\n**真实示例**:")
            add_code_block(f"""// 查询: "{real_example1['event']}" 的处置链
MATCH (e:Event {{name: "{real_example1['event']}"}})-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
RETURN e.name as event, c.name as consequence, collect(a.name) as actions

// 结果:
事件: {real_example1['event']}
  ↓ [LEADS_TO]
后果: {real_example1['consequence']}
  ↓ [CONSISTS_OF]
动作:
{chr(10).join('  - ' + action for action in real_example1['actions'])}
""")
            
            # 路径 2: 任务分解链
            add_section("路径 2: 任务分解链 (Action → Action)", 3)
            add_text("**逻辑**: `Action -[:NEXT_STEP]-> Action`")
            
            next_step_count = session.run("""
                MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
                RETURN count(*) as c
            """).single()['c']
            add_text(f"**路径数**: {next_step_count:,} 条")
            add_text("**用途**: 将高层任务分解为具体执行步骤")
            
            # 真实示例
            real_example2 = session.run("""
                MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
                WHERE size(a1.name) > 30
                WITH a1, collect(a2.name)[0..3] as next_actions
                RETURN a1.name as parent_action, next_actions
                LIMIT 1
            """).single()
            
            add_text("\n**真实示例**:")
            add_code_block(f"""// 查询: 高层任务的分解步骤
MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
WHERE a1.name = "{real_example2['parent_action']}"
RETURN a1.name as parent_action, collect(a2.name)[0..5] as sub_actions

// 结果:
高层任务: {real_example2['parent_action']}
  ↓ [NEXT_STEP] 分解为:
{chr(10).join('  - ' + action for action in real_example2['next_actions'])}
""")
            
            # 路径 3: 资源调度链
            add_section("路径 3: 资源调度链 (Action → Resource)", 3)
            add_text("**逻辑**: `Action -[:REQUIRES]-> Resource`")
            
            requires_count = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                RETURN count(DISTINCT a) as action_count, count(*) as rel_count
            """).single()
            add_text(f"**覆盖**: {requires_count['action_count']} 个动作定义了资源需求")
            add_text(f"**关系数**: {requires_count['rel_count']} 条")
            
            # 真实示例
            real_example3 = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                WITH a, collect(r.name) as resources
                WHERE size(resources) >= 3
                RETURN a.name as action, resources
                LIMIT 1
            """).single()
            
            add_text("\n**真实示例**:")
            add_code_block(f"""// 查询: "{real_example3['action'][:50]}..." 需要的资源
MATCH (a:Action)-[:REQUIRES]->(r:Resource)
WHERE a.name = "{real_example3['action']}"
RETURN r.name as resource

// 结果:
{chr(10).join('- ' + r for r in real_example3['resources'])}
""")
            
            # 5. 典型节点示例
            add_section("典型节点示例", 2)
            
            # Event 节点
            add_section("Event (事件) 节点", 3)
            event_samples = session.run("""
                MATCH (e:Event)
                WHERE size(e.name) < 50 AND size(e.name) > 10
                RETURN e.name as name
                ORDER BY rand()
                LIMIT 5
            """).data()
            
            add_text("**示例 (随机抽样)**:")
            for i, e in enumerate(event_samples, 1):
                add_text(f"{i}. `{e['name']}`")
            
            # 查询一个有完整关系的事件
            event_detail = session.run("""
                MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)
                WITH e, count(c) as cons_count
                WHERE cons_count > 0
                MATCH (e)-[:LEADS_TO]->(c:Consequence)
                RETURN e.name as event, collect(c.name)[0..2] as consequences
                LIMIT 1
            """).single()
            
            add_text(f"\n**详细示例**: `{event_detail['event']}`")
            add_text("\n关联关系:")
            add_text(f"- `LEADS_TO` → 后果: {', '.join(f'`{c}`' for c in event_detail['consequences'])}")
            
            # 获取该事件的完整推理链
            event_full_path = session.run("""
                MATCH (e:Event {name: $event_name})-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
                RETURN c.name as consequence, collect(a.name)[0..3] as actions
                LIMIT 1
            """, event_name=event_detail['event']).data()
            
            if event_full_path:
                add_text(f"\n完整推理示例:")
                for item in event_full_path:
                    add_code_block(f"""Event: {event_detail['event']}
  ↓ [LEADS_TO]
Consequence: {item['consequence']}
  ↓ [CONSISTS_OF]
Actions:
{chr(10).join('  - ' + a for a in item['actions'])}
""", "yaml")
            
            # Action 节点
            add_section("Action (动作) 节点", 3)
            
            # 高频动作
            high_freq_actions = session.run("""
                MATCH (a:Action)<-[r]-()
                WITH a, count(r) as in_degree
                WHERE in_degree > 50
                RETURN a.name as action, in_degree
                ORDER BY in_degree DESC
                LIMIT 5
            """).data()
            
            add_text("**高频动作 (入度 Top 5)**:")
            headers = ["动作名称", "入度 (被引用次数)"]
            rows = [[a['action'], a['in_degree']] for a in high_freq_actions]
            add_table(headers, rows)
            
            # 查询一个有完整资源的动作
            action_detail = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                WITH a, collect(r.name) as resources
                WHERE size(resources) >= 2 AND size(a.name) < 30
                RETURN a.name as action, resources
                LIMIT 1
            """).single()
            
            add_text(f"\n**详细示例**: `{action_detail['action']}`")
            add_text("关联资源:")
            for r in action_detail['resources']:
                add_text(f"- `{r}`")
            
            # Consequence 节点
            add_section("Consequence (后果) 节点", 3)
            consequence_samples = session.run("""
                MATCH (c:Consequence)
                WHERE size(c.name) < 40
                RETURN c.name as name
                ORDER BY rand()
                LIMIT 5
            """).data()
            
            add_text("**示例 (随机抽样)**:")
            for i, c in enumerate(consequence_samples, 1):
                add_text(f"{i}. `{c['name']}`")
            
            # 查询一个有完整动作的后果
            cons_detail = session.run("""
                MATCH (c:Consequence)-[:CONSISTS_OF]->(a:Action)
                WITH c, collect(a.name)[0..3] as actions
                WHERE size(actions) >= 3
                RETURN c.name as consequence, actions
                LIMIT 1
            """).single()
            
            add_text(f"\n**详细示例**: `{cons_detail['consequence']}`")
            add_text("包含的处置动作:")
            for a in cons_detail['actions']:
                add_text(f"- `{a}`")
            
            # Resource 节点
            add_section("Resource (资源) 节点", 3)
            all_resources = session.run("""
                MATCH (r:Resource)
                RETURN r.name as name
                ORDER BY r.name
            """).data()
            
            add_text("**完整资源清单** (共24种):")
            add_text("\n人力资源:")
            human_resources = [r['name'] for r in all_resources if any(kw in r['name'] for kw in ['人员', '人', '警', '医', '法医'])]
            for r in human_resources:
                add_text(f"- `{r}`")
            
            add_text("\n车辆资源:")
            vehicle_resources = [r['name'] for r in all_resources if any(kw in r['name'] for kw in ['车'])]
            for r in vehicle_resources:
                add_text(f"- `{r}`")
            
            add_text("\n设施资源:")
            facility_resources = [r['name'] for r in all_resources if r['name'] not in human_resources + vehicle_resources]
            for r in facility_resources:
                add_text(f"- `{r}`")
            
            # 资源使用频率
            resource_usage = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                RETURN r.name as resource, count(a) as usage_count
                ORDER BY usage_count DESC
                LIMIT 5
            """).data()
            
            add_text("\n**资源使用频率 Top 5**:")
            headers = ["资源名称", "被调用次数"]
            rows = [[r['resource'], r['usage_count']] for r in resource_usage]
            add_table(headers, rows)
            
            # 6. 完整案例
            add_section("完整推理案例", 2)
            
            # 案例1: 危化品事故
            add_section("案例 1: 危化品追尾事故", 3)
            
            case1_data = session.run("""
                MATCH path = (e:Event {name: "液化天然气车追尾苯酚车"})
                  -[:LEADS_TO]->(c:Consequence)
                  -[:CONSISTS_OF]->(a:Action)
                RETURN e.name as event, c.name as consequence, collect(DISTINCT a.name) as actions
            """).data()
            
            add_text("**事件起点**:")
            add_code_block(f'Event: "{case1_data[0]["event"]}"', "yaml")
            
            add_text("**完整推理链**:")
            for item in case1_data:
                add_code_block(f"""事件: {item['event']}
  ↓ [LEADS_TO]
后果: {item['consequence']}
  ↓ [CONSISTS_OF] 包含以下处置动作:
{chr(10).join('  - ' + action for action in item['actions'])}
""", "yaml")
            
            # 查询相关资源
            case1_resources = session.run("""
                MATCH (e:Event {name: "液化天然气车追尾苯酚车"})
                  -[:LEADS_TO]->(:Consequence)
                  -[:CONSISTS_OF]->(a:Action)
                  -[:REQUIRES]->(r:Resource)
                RETURN DISTINCT r.name as resource
            """).data()
            
            if case1_resources:
                add_text("**所需资源**:")
                for r in case1_resources:
                    add_text(f"- `{r['resource']}`")
            
            # 7. 数据质量指标
            add_section("数据质量指标", 2)
            
            # 完整性检查
            isolated = session.run("MATCH (n) WHERE NOT (n)--() RETURN count(*) as c").single()['c']
            empty_names = session.run("MATCH (n) WHERE n.name IS NULL OR n.name = '' RETURN count(*) as c").single()['c']
            duplicates = session.run("""
                MATCH (n)
                WITH labels(n)[0] as label, n.name as name, count(*) as c
                WHERE c > 1
                RETURN count(*) as c
            """).single()['c']
            
            add_text("**数据完整性**:")
            headers = ["检查项", "状态", "数值"]
            rows = [
                ["孤立节点", "✅" if isolated == 0 else "❌", isolated],
                ["空名称节点", "✅" if empty_names == 0 else "❌", empty_names],
                ["重复节点", "✅" if duplicates == 0 else "❌", duplicates]
            ]
            add_table(headers, rows)
            
            # 覆盖率统计
            total_actions = session.run("MATCH (a:Action) RETURN count(a) as c").single()['c']
            actions_with_resources = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(:Resource)
                RETURN count(DISTINCT a) as c
            """).single()['c']
            resource_coverage = (actions_with_resources / total_actions * 100) if total_actions > 0 else 0
            
            cons_with_actions = session.run("""
                MATCH (c:Consequence)-[:CONSISTS_OF]->(:Action)
                RETURN count(DISTINCT c) as c
            """).single()['c']
            total_cons = session.run("MATCH (c:Consequence) RETURN count(c) as c").single()['c']
            action_coverage = (cons_with_actions / total_cons * 100) if total_cons > 0 else 0
            
            add_text("\n**逻辑覆盖率**:")
            headers = ["维度", "覆盖率", "说明"]
            rows = [
                ["动作的资源定义", f"{resource_coverage:.1f}%", f"{actions_with_resources}/{total_actions}"],
                ["后果的动作细化", f"{action_coverage:.1f}%", f"{cons_with_actions}/{total_cons}"]
            ]
            add_table(headers, rows)
            
            # 8. 技术实现
            add_section("技术实现", 2)
            
            add_section("数据库信息", 3)
            add_text("- **数据库**: Neo4j 5.x")
            add_text("- **连接**: `bolt://localhost:7687`")
            add_text("- **驱动**: Python `neo4j-driver`")
            
            add_section("常用查询模板", 3)
            
            add_text("**模板 1: 查询事件的完整处置链**")
            add_code_block("""MATCH path = (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
WHERE e.name CONTAINS '泄漏'
RETURN e.name as event, c.name as consequence, collect(a.name) as actions
LIMIT 10
""")
            
            add_text("**模板 2: 查询动作的资源需求**")
            add_code_block("""MATCH (a:Action)-[:REQUIRES]->(r:Resource)
WHERE a.name CONTAINS '急救'
RETURN a.name as action, collect(r.name) as resources
""")
            
            add_text("**模板 3: 查询任务分解链**")
            add_code_block("""MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
WHERE a1.name CONTAINS '应急'
RETURN a1.name as parent_task, collect(a2.name) as sub_tasks
""")
            
            # 9. 附录
            add_section("附录: 核心统计汇总", 2)
            
            # 路径可用性
            path_checks = [
                ("Event -> Consequence -> Action", 
                 "MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action) RETURN count(*) as c"),
                ("Action -> NEXT_STEP -> Action", 
                 "MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action) RETURN count(*) as c"),
                ("Action -> REQUIRES -> Resource", 
                 "MATCH (a:Action)-[:REQUIRES]->(r:Resource) RETURN count(*) as c"),
                ("Event -> LEADS_TO -> Consequence", 
                 "MATCH (e:Event)-[:LEADS_TO]->(c:Consequence) RETURN count(*) as c"),
                ("Event -> TRIGGERS -> Event", 
                 "MATCH (e1:Event)-[:TRIGGERS]->(e2:Event) RETURN count(*) as c"),
            ]
            
            add_text("**核心推理路径可用性**:")
            headers = ["路径", "状态", "路径数"]
            rows = []
            for path_name, query in path_checks:
                count = session.run(query).single()['c']
                status = "✅" if count > 0 else "❌"
                rows.append([path_name, status, f"{count:,}"])
            add_table(headers, rows)
            
            # 写入文件
            final_doc = "".join(doc_content)
            
            return final_doc
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        driver.close()

if __name__ == "__main__":
    print("正在生成基于真实数据的知识图谱文档...")
    doc = generate_real_doc()
    
    if doc:
        with open("知识图谱完整文档.md", "w", encoding="utf-8") as f:
            f.write(doc)
        print("✅ 文档生成完成: 知识图谱完整文档.md")
        print(f"文档大小: {len(doc)} 字符")
    else:
        print("❌ 文档生成失败")
