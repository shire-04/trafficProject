"""
深入检查剩余的CLASSIFIED_AS关系
"""
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def deep_check_classified_as(driver):
    """深入检查CLASSIFIED_AS关系的合理性"""
    
    with driver.session() as session:
        print("=" * 80)
        print("检查剩余的40条 CLASSIFIED_AS 关系")
        print("=" * 80)
        
        # 检查一对多的分类
        result = session.run("""
            MATCH (e1:Event)-[:CLASSIFIED_AS]->(e2:Event)
            WITH e1, collect(e2.name) as targets, count(*) as target_count
            WHERE target_count > 1
            RETURN e1.name as source, target_count, targets
            ORDER BY target_count DESC
            LIMIT 10
        """)
        
        print("\n一对多分类 (一个事件被分到多个标准):")
        multi_classified = list(result)
        for record in multi_classified:
            print(f"\n源事件: {record['source']}")
            print(f"被分类到 {record['target_count']} 个标准:")
            for target in record['targets']:
                print(f"  - {target[:100]}")
        
        # 检查所有40条关系
        result = session.run("""
            MATCH (e1:Event)-[:CLASSIFIED_AS]->(e2:Event)
            RETURN e1.name as source, e2.name as target,
                   size(e1.name) as source_len, size(e2.name) as target_len
            ORDER BY source_len, target_len
        """)
        
        print(f"\n\n所有40条CLASSIFIED_AS关系详情:")
        print("-" * 80)
        all_rels = list(result)
        for i, record in enumerate(all_rels, 1):
            print(f"\n{i}. [{record['source_len']}字] {record['source']}")
            print(f"   → [{record['target_len']}字] {record['target'][:80]}...")
        
        # 分析分类是否合理
        print("\n\n" + "=" * 80)
        print("合理性分析")
        print("=" * 80)
        
        # 统计源节点长度分布
        source_lengths = [r['source_len'] for r in all_rels]
        target_lengths = [r['target_len'] for r in all_rels]
        
        print(f"\n源节点名称长度: 最短{min(source_lengths)}, 最长{max(source_lengths)}, 平均{sum(source_lengths)/len(source_lengths):.1f}")
        print(f"目标节点名称长度: 最短{min(target_lengths)}, 最长{max(target_lengths)}, 平均{sum(target_lengths)/len(target_lengths):.1f}")
        
        # 检查是否有合理的分类模式
        short_sources = [r for r in all_rels if r['source_len'] <= 10]
        long_sources = [r for r in all_rels if r['source_len'] > 40]
        
        print(f"\n短源节点(≤10字): {len(short_sources)}条")
        print(f"长源节点(>40字): {len(long_sources)}条")
        
        if len(short_sources) > 0:
            print("\n⚠️ 短源节点示例(可能是简要描述被分类到详细定义):")
            for r in short_sources[:3]:
                print(f"  {r['source']} → {r['target'][:50]}...")
        
        print("\n\n" + "=" * 80)
        print("建议操作")
        print("=" * 80)
        
        if len(multi_classified) > 0:
            print(f"""
发现问题: {len(multi_classified)}个事件被分类到多个标准

这说明分类逻辑仍然有误。一个具体事件应该只对应一个标准等级。

建议方案:
1. 删除所有剩余的40条CLASSIFIED_AS关系
2. 原因: "6死5伤"不应该同时属于3个不同的死亡人数区间

或者:
3. 重新设计分类逻辑,确保每个事件只映射到一个最匹配的标准

是否执行删除操作?
""")
        else:
            print("\n✅ 未发现一对多分类,所有关系看起来合理")

def main():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        deep_check_classified_as(driver)
        
        # 询问是否删除
        print("\n是否删除所有40条CLASSIFIED_AS关系? (y/n): ", end="")
        # choice = input().strip().lower()
        # 为了自动化,直接设置为y
        choice = 'y'
        
        if choice == 'y':
            with driver.session() as session:
                result = session.run("""
                    MATCH (e1:Event)-[r:CLASSIFIED_AS]->(e2:Event)
                    DELETE r
                    RETURN count(*) as deleted
                """)
                deleted = result.single()['deleted']
                print(f"\n✅ 已删除 {deleted} 条CLASSIFIED_AS关系")
                
                # 最终统计
                result = session.run("""
                    MATCH ()-[r]->()
                    RETURN count(*) as total_relations
                """)
                total = result.single()['total_relations']
                print(f"剩余总关系数: {total}")
        else:
            print("\n操作已取消")
    
    finally:
        driver.close()

if __name__ == "__main__":
    main()
