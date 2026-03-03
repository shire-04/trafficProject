"""
知识图谱数据补全脚本 - 为 Consequence 节点补充 CONSISTS_OF 关系
"""
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def analyze_and_enrich():
    print("正在连接 Neo4j 进行数据分析...")
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        with driver.session() as session:
            # === Phase 1: 分析现有 Consequence 节点 ===
            print("\n=== 1. 现有 Consequence 节点分析 ===")
            
            consequences = session.run("""
                MATCH (c:Consequence)
                OPTIONAL MATCH (c)-[:CONSISTS_OF]->(a:Action)
                RETURN c.name as name, collect(a.name) as linked_actions
                ORDER BY c.name
            """).data()
            
            print(f"共有 {len(consequences)} 个 Consequence 节点")
            
            # 分类：已有关联 vs 无关联
            linked = [c for c in consequences if c['linked_actions'] and c['linked_actions'][0] is not None]
            orphan = [c for c in consequences if not c['linked_actions'] or c['linked_actions'][0] is None]
            
            print(f"✅ 已有 CONSISTS_OF 关系: {len(linked)} 个")
            print(f"❌ 缺少 CONSISTS_OF 关系: {len(orphan)} 个")
            
            print("\n[无关联的 Consequence 示例 (前20个)]:")
            for c in orphan[:20]:
                print(f"  - {c['name']}")

            # === Phase 2: 分析现有 Action 节点 ===
            print("\n=== 2. 现有 Action 节点分析 ===")
            
            actions = session.run("""
                MATCH (a:Action)
                RETURN a.name as name
                ORDER BY a.name
            """).data()
            
            print(f"共有 {len(actions)} 个 Action 节点")
            print("\n[Action 示例 (前20个)]:")
            for a in actions[:20]:
                print(f"  - {a['name']}")

            # === Phase 3: 基于关键词的语义匹配规则 ===
            print("\n=== 3. 语义匹配规则设计 ===")
            
            # 定义关键词到动作的映射规则
            keyword_action_map = {
                # 人员伤亡相关
                "伤": ["伤员现场急救", "伤情评估", "转运救治"],
                "亡": ["事故中伤亡情况鉴定处理", "伤员现场急救"],
                "死": ["事故中伤亡情况鉴定处理"],
                "受伤": ["伤员现场急救", "伤情评估", "转运救治"],
                
                # 火灾相关
                "火": ["扑灭车辆火灾", "扑救车辆火灾"],
                "燃烧": ["扑灭车辆火灾", "扑救车辆火灾"],
                "起火": ["扑灭车辆火灾", "扑救车辆火灾"],
                
                # 危化品相关
                "泄漏": ["处置危化品泄漏", "检测危化品泄漏对空气/水质的污染", "稀释危化品泄漏物"],
                "危化": ["处置危化品泄漏", "转移事故中未泄漏的危化品", "避免二次污染"],
                "苯": ["处置危化品泄漏", "检测危化品泄漏对空气/水质的污染"],
                "液化": ["处置危化品泄漏", "稀释危化品泄漏物"],
                "污染": ["检测危化品泄漏对空气/水质的污染", "清理现场污染物", "跟踪环境指标"],
                
                # 交通管制相关
                "拥堵": ["执行交通管制", "车辆分流引导", "疏导分流车辆"],
                "堵塞": ["执行交通管制", "车辆分流引导"],
                "中断": ["执行交通管制", "车辆分流引导", "发布交通管制信息"],
                "管制": ["执行交通管制", "发布交通管制信息"],
                
                # 救援相关
                "被困": ["破拆救援被困人员"],
                "救援": ["破拆救援被困人员", "伤员现场急救"],
                
                # 道路损坏相关
                "路面": ["修复事故导致的路面破损", "清理路面残骸"],
                "破损": ["修复事故导致的路面破损", "抢修受损道路设施"],
                "损坏": ["修复事故导致的路面破损", "抢修受损道路设施"],
                
                # 车辆处理相关
                "车辆": ["拖移事故故障车辆", "清理路面障碍物"],
                "清障": ["拖移事故故障车辆", "清理路面障碍物"],
                
                # 现场处置相关
                "现场": ["统筹现场处置", "协调多部门联动", "隔离事故现场"],
                "疏散": ["引导群众疏散", "防止无关人员进入"],
                "警戒": ["划定事故警戒区域", "隔离事故现场"],
            }
            
            # === Phase 4: 执行匹配并生成关系 ===
            print("\n=== 4. 执行语义匹配 ===")
            
            # 获取所有实际存在的 Action 名称
            existing_actions = set(a['name'] for a in actions)
            
            matches_to_create = []
            
            for cons in orphan:
                cons_name = cons['name']
                matched_actions = set()
                
                # 遍历关键词规则
                for keyword, suggested_actions in keyword_action_map.items():
                    if keyword in cons_name:
                        for action in suggested_actions:
                            if action in existing_actions:
                                matched_actions.add(action)
                
                if matched_actions:
                    matches_to_create.append({
                        'consequence': cons_name,
                        'actions': list(matched_actions)
                    })
            
            print(f"成功匹配 {len(matches_to_create)} 个 Consequence 节点")
            
            print("\n[匹配结果预览]:")
            for m in matches_to_create[:10]:
                print(f"  {m['consequence']}")
                for a in m['actions']:
                    print(f"    -> {a}")

            # === Phase 5: 写入数据库 ===
            print("\n=== 5. 写入数据库 ===")
            
            total_created = 0
            for match in matches_to_create:
                for action in match['actions']:
                    result = session.run("""
                        MATCH (c:Consequence {name: $cons_name})
                        MATCH (a:Action {name: $action_name})
                        MERGE (c)-[r:CONSISTS_OF]->(a)
                        RETURN count(r) as created
                    """, cons_name=match['consequence'], action_name=action)
                    total_created += result.single()['created']
            
            print(f"✅ 成功创建 {total_created} 条 CONSISTS_OF 关系")

            # === Phase 6: 验证结果 ===
            print("\n=== 6. 验证结果 ===")
            
            # 重新统计
            new_count = session.run("""
                MATCH (c:Consequence)-[:CONSISTS_OF]->(a:Action)
                RETURN count(*) as count
            """).single()['count']
            
            print(f"当前 CONSISTS_OF 关系总数: {new_count}")
            
            # 测试完整路径
            full_path = session.run("""
                MATCH path = (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
                RETURN count(path) as count
            """).single()['count']
            
            print(f"完整因果链 (Event -> Consequence -> Action) 路径数: {full_path}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.close()

if __name__ == "__main__":
    analyze_and_enrich()
