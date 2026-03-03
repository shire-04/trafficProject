"""
验证文档中的示例查询
"""
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def verify_examples():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        with driver.session() as session:
            print("=" * 80)
            print("验证文档示例查询")
            print("=" * 80)
            
            # 问题 1: 验证 TRIGGERS 关系
            print("\n【问题 1: Event -> TRIGGERS -> Action】")
            print("-" * 80)
            
            # 先检查该事件是否存在
            check_event = session.run("""
                MATCH (e:Event)
                WHERE e.name CONTAINS "货车" AND e.name CONTAINS "苯"
                RETURN e.name as name
                LIMIT 5
            """).data()
            
            print(f"包含'货车'和'苯'的事件:")
            for e in check_event:
                print(f"  - {e['name']}")
            
            # 查询任意一个事件的 TRIGGERS 关系
            print("\n随机抽样 TRIGGERS 关系:")
            sample_triggers = session.run("""
                MATCH (e:Event)-[:TRIGGERS]->(a:Action)
                RETURN e.name as event, a.name as action
                LIMIT 5
            """).data()
            
            if sample_triggers:
                for t in sample_triggers:
                    print(f"  {t['event']} -> {t['action']}")
            else:
                print("  ⚠️ 没有找到任何 TRIGGERS 关系！")
            
            # 问题 2: 验证资源关系
            print("\n【问题 2: Action -> REQUIRES -> Resource】")
            print("-" * 80)
            
            # 检查"伤员现场急救"
            check_action = session.run("""
                MATCH (a:Action)
                WHERE a.name CONTAINS "伤员" AND a.name CONTAINS "急救"
                RETURN a.name as name
                LIMIT 5
            """).data()
            
            print(f"包含'伤员'和'急救'的动作:")
            for a in check_action:
                print(f"  - {a['name']}")
                
                # 查询它的资源
                resources = session.run("""
                    MATCH (a:Action {name: $name})-[:REQUIRES]->(r:Resource)
                    RETURN r.name as resource
                """, name=a['name']).data()
                
                if resources:
                    for r in resources:
                        print(f"      -> {r['resource']}")
                else:
                    print(f"      (无 REQUIRES 关系)")
            
            # 问题 3: 验证 NEXT_STEP 关系
            print("\n【问题 3: Action -> NEXT_STEP -> Action】")
            print("-" * 80)
            
            # 检查"执行交通管制"
            check_action2 = session.run("""
                MATCH (a:Action)
                WHERE a.name CONTAINS "交通管制"
                RETURN a.name as name
                LIMIT 5
            """).data()
            
            print(f"包含'交通管制'的动作:")
            for a in check_action2:
                print(f"  - {a['name']}")
                
                # 查询它的 NEXT_STEP
                next_steps = session.run("""
                    MATCH (a:Action {name: $name})-[:NEXT_STEP]->(a2:Action)
                    RETURN a2.name as next_action
                    LIMIT 5
                """, name=a['name']).data()
                
                if next_steps:
                    for n in next_steps:
                        print(f"      -> {n['next_action']}")
                else:
                    print(f"      (无 NEXT_STEP 关系)")
            
            # 补充: 分析 NEXT_STEP 的实际模式
            print("\n【补充分析: NEXT_STEP 关系的实际连接模式】")
            print("-" * 80)
            
            next_step_pattern = session.run("""
                MATCH (source)-[r:NEXT_STEP]->(target)
                RETURN DISTINCT 
                    labels(source)[0] as source_label,
                    labels(target)[0] as target_label,
                    count(*) as count
                ORDER BY count DESC
            """).data()
            
            print("NEXT_STEP 关系的实际连接模式:")
            for p in next_step_pattern:
                print(f"  {p['source_label']} -[NEXT_STEP]-> {p['target_label']}: {p['count']} 条")
            
            # 给出正确的 NEXT_STEP 示例
            print("\n正确的 NEXT_STEP 示例 (Action -> Action):")
            correct_next_step = session.run("""
                MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
                RETURN a1.name as from_action, a2.name as to_action
                LIMIT 5
            """).data()
            
            for n in correct_next_step:
                print(f"  {n['from_action']}")
                print(f"    -> {n['to_action']}")
            
            # 补充: 找出正确的 Event -> Action 示例
            print("\n【补充: 正确的 Event -> Action 示例】")
            print("-" * 80)
            
            # 通过 LEADS_TO + CONSISTS_OF 路径
            correct_path = session.run("""
                MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)-[:CONSISTS_OF]->(a:Action)
                RETURN e.name as event, c.name as consequence, a.name as action
                LIMIT 3
            """).data()
            
            print("Event -> Consequence -> Action 路径:")
            for p in correct_path:
                print(f"  事件: {p['event']}")
                print(f"    -> 后果: {p['consequence']}")
                print(f"       -> 动作: {p['action']}")
                print()
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.close()

if __name__ == "__main__":
    verify_examples()
