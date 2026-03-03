from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def analyze_and_clean():
    print(f"正在连接 Neo4j: {URI} ...")
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        with driver.session() as session:
            # --- Phase 1: 清理幽灵节点与元数据 ---
            print("\n=== 1. 清理幽灵节点 (Cleanup) ===")
            ghost_labels = ['action', 'resource', 'Outcome', 'Scenario', 'Plan']
            
            for label in ghost_labels:
                # 1.1 尝试删除节点 (虽然之前显示为0，但为了保险)
                query_del = f"MATCH (n:`{label}`) DETACH DELETE n"
                result = session.run(query_del)
                stats = result.consume().counters
                if stats.nodes_deleted > 0:
                    print(f"✅ Deleted {stats.nodes_deleted} nodes with label `{label}`.")
                else:
                    print(f"ℹ️  Label `{label}` had 0 nodes to delete.")
                
                # 1.2 检查并删除相关的约束/索引 (这通常是标签残留的原因)
                # 注意：不同 Neo4j 版本语法不同，这里尝试通用查询
                try:
                    # 获取所有约束
                    constraints = session.run("SHOW CONSTRAINTS").data()
                    for c in constraints:
                        # 检查约束是否关联当前幽灵标签
                        # 结构通常包含 'labelsOrTypes' 或类似字段
                        if label in c.get('labelsOrTypes', []):
                            name = c.get('name')
                            print(f"  - Dropping constraint `{name}` on `{label}`...")
                            session.run(f"DROP CONSTRAINT {name}")
                except Exception as e:
                    print(f"  (Constraint check skipped/failed: {e})")

            # --- Phase 2: 深度结构分析 ---
            print("\n=== 2. 深度结构分析 (Deep Structure Analysis) ===")
            
            # 2.1 核心路径统计
            print("\n[核心推理路径统计]")
            
            # Path: Event -> Consequence -> Action
            q_chain = """
            MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
            RETURN count(*) as count
            """
            c_chain = session.run(q_chain).single()['count']
            print(f"1. 完整因果链 (Event -> Consequence -> Action): {c_chain} 条路径")
            
            # Path: Event -> Action (Direct Trigger)
            q_trigger = """
            MATCH (e:Event)-[:TRIGGERS]->(a:Action)
            RETURN count(*) as count
            """
            c_trigger = session.run(q_trigger).single()['count']
            print(f"2. 快速响应链 (Event -> Action): {c_trigger} 条路径")
            
            # Path: Action -> Resource
            q_res = """
            MATCH (a:Action)-[:REQUIRES]->(r:Resource)
            RETURN count(*) as count
            """
            c_res = session.run(q_res).single()['count']
            print(f"3. 资源调用链 (Action -> Resource): {c_res} 条路径")

            # 2.2 关键节点分析 (Hubs)
            print("\n[关键节点分析]")
            
            # 最繁忙的资源
            print("Top 5 最常被调用的资源:")
            q_top_res = """
            MATCH (a:Action)-[:REQUIRES]->(r:Resource)
            RETURN r.name as resource, count(a) as calls
            ORDER BY calls DESC LIMIT 5
            """
            for r in session.run(q_top_res):
                print(f"  - {r['resource']}: {r['calls']} 次调用")
                
            # 最复杂的事件 (引发后果最多)
            print("\nTop 5 最复杂的事件 (引发后果最多):")
            q_top_event = """
            MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)
            RETURN e.name as event, count(c) as consequences
            ORDER BY consequences DESC LIMIT 5
            """
            for r in session.run(q_top_event):
                print(f"  - {r['event']}: {r['consequences']} 个后果")

            # 2.3 孤立子图检查
            # 检查是否有 Action 既不属于 Consequence 也不被 Event 触发
            q_orphan_action = """
            MATCH (a:Action)
            WHERE NOT (a)<-[:CONSISTS_OF]-(:Consequence) 
              AND NOT (a)<-[:TRIGGERS]-(:Event)
              AND NOT (a)<-[:NEXT_STEP]-(:Action)
            RETURN count(a) as count, collect(a.name)[0..3] as examples
            """
            orphan = session.run(q_orphan_action).single()
            if orphan['count'] > 0:
                print(f"\n⚠️  发现 {orphan['count']} 个'悬空'动作 (未被任何事件或后果触发):")
                print(f"   示例: {orphan['examples']}")
            else:
                print("\n✅ 所有动作都已正确关联到事件或后果链中。")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    analyze_and_clean()