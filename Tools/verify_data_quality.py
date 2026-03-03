"""
验证知识图谱中的数据质量问题
"""
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def check_data_quality(driver):
    """检查数据质量问题"""
    
    with driver.session() as session:
        print("=" * 60)
        print("1. 检查 Event -[:CLASSIFIED_AS]-> Event 关系")
        print("=" * 60)
        
        # 检查CLASSIFIED_AS是否合理
        result = session.run("""
            MATCH (e1:Event)-[:CLASSIFIED_AS]->(e2:Event)
            RETURN e1.name as source, e2.name as target, 
                   size(e1.name) as source_len, size(e2.name) as target_len
            ORDER BY source_len
            LIMIT 10
        """)
        
        print("\n前10条 CLASSIFIED_AS 关系:")
        for record in result:
            print(f"\n源节点 ({record['source_len']}字符): {record['source'][:80]}...")
            print(f"目标节点 ({record['target_len']}字符): {record['target'][:80]}...")
        
        # 统计目标节点
        result = session.run("""
            MATCH (e1:Event)-[:CLASSIFIED_AS]->(e2:Event)
            RETURN e2.name as target, count(*) as count
            ORDER BY count DESC
            LIMIT 5
        """)
        
        print("\n\n最常见的分类目标:")
        for record in result:
            print(f"- [{record['count']}次] {record['target'][:100]}...")
        
        print("\n" + "=" * 60)
        print("2. 检查 Consequence -[:NEXT_STEP]-> Event 关系")
        print("=" * 60)
        
        # 检查自循环
        result = session.run("""
            MATCH (c:Consequence)-[:NEXT_STEP]->(e:Event)
            WHERE c.name = e.name
            RETURN count(*) as self_loop_count
        """)
        
        self_loops = result.single()['self_loop_count']
        print(f"\n自循环数量: {self_loops} / 32")
        
        # 检查非自循环的关系
        result = session.run("""
            MATCH (c:Consequence)-[:NEXT_STEP]->(e:Event)
            WHERE c.name <> e.name
            RETURN c.name as consequence, e.name as event
            LIMIT 10
        """)
        
        records = list(result)
        if records:
            print(f"\n有意义的关系 ({len(records)}条):")
            for record in records:
                print(f"- {record['consequence']} → {record['event']}")
        else:
            print("\n❌ 所有关系都是自循环!")
        
        print("\n" + "=" * 60)
        print("3. 检查 Event -[:TRIGGERS]-> Action 关系")
        print("=" * 60)
        
        # 检查TRIGGERS的目标是否真的是Action
        result = session.run("""
            MATCH (e:Event)-[:TRIGGERS]->(a:Action)
            RETURN a.name as action, count(*) as trigger_count
            ORDER BY trigger_count DESC
            LIMIT 5
        """)
        
        print("\n被触发最多的'Action':")
        for record in result:
            print(f"- [{record['trigger_count']}次] {record['action']}")
        
        # 检查这些Action的出度
        result = session.run("""
            MATCH (e:Event)-[:TRIGGERS]->(a:Action)
            WITH a, count(*) as in_degree
            OPTIONAL MATCH (a)-[r]->()
            RETURN a.name as action, in_degree, count(r) as out_degree
            ORDER BY in_degree DESC
            LIMIT 5
        """)
        
        print("\n这些Action的连接情况:")
        for record in result:
            print(f"- {record['action']}")
            print(f"  入度: {record['in_degree']}, 出度: {record['out_degree']}")
        
        print("\n" + "=" * 60)
        print("4. 推荐的修复方案")
        print("=" * 60)
        
        print("""
方案建议:

1. Event -[:CLASSIFIED_AS]-> Event (140条)
   问题: 具体事件(如'6死5伤')被错误地CLASSIFIED_AS到冗长的标准定义
   原因: 这些"标准定义"实际上也是Event节点,但应该是分类标准(如等级)
   建议: 
   - 删除这140条CLASSIFIED_AS关系 
   - 或将目标节点改为专门的 Standard/Level 节点类型

2. Consequence -[:NEXT_STEP]-> Event (32条)
   问题: 全部是自循环关系,无实际意义
   建议: 直接删除这32条关系

3. Event -[:TRIGGERS]-> Action (524条)
   问题: 目标节点名称像是事件等级(如"特别重大事件"),不是具体动作
   原因: 节点标签分类错误,这些应该是Standard节点而非Action
   建议: 
   - 重新标记这些节点为合适的类型
   - 或将关系改为 Event -[:CLASSIFIED_AS]-> Standard

是否需要执行清理操作?
""")

def main():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        check_data_quality(driver)
    finally:
        driver.close()

if __name__ == "__main__":
    main()
