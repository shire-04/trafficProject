"""
全面清理知识图谱中的无效关系
"""
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def analyze_and_clean(driver):
    """分析并清理所有无效关系"""
    
    with driver.session() as session:
        print("=" * 80)
        print("阶段 1: 全面分析所有关系类型")
        print("=" * 80)
        
        # 获取所有关系类型
        result = session.run("""
            MATCH ()-[r]->()
            RETURN type(r) as rel_type, count(*) as count
            ORDER BY count DESC
        """)
        
        all_rels = [(r['rel_type'], r['count']) for r in result]
        print(f"\n发现 {len(all_rels)} 种关系类型:")
        for rel_type, count in all_rels:
            print(f"  - {rel_type}: {count}条")
        
        print("\n" + "=" * 80)
        print("阶段 2: 检查每种关系的数据质量")
        print("=" * 80)
        
        issues = []
        
        for rel_type, count in all_rels:
            print(f"\n检查 {rel_type} ({count}条)...")
            
            # 检查自循环
            result = session.run(f"""
                MATCH (n)-[r:{rel_type}]->(m)
                WHERE id(n) = id(m)
                RETURN count(*) as self_loops
            """)
            self_loops = result.single()['self_loops']
            
            if self_loops > 0:
                print(f"  ⚠️  发现 {self_loops} 个自循环")
                issues.append({
                    'type': 'self_loop',
                    'rel_type': rel_type,
                    'count': self_loops,
                    'severity': 'HIGH'
                })
            
            # 检查源节点和目标节点的标签分布
            result = session.run(f"""
                MATCH (n)-[r:{rel_type}]->(m)
                RETURN labels(n)[0] as source_label, labels(m)[0] as target_label, 
                       count(*) as count
                ORDER BY count DESC
                LIMIT 5
            """)
            
            patterns = list(result)
            if patterns:
                print(f"  连接模式:")
                for p in patterns:
                    print(f"    {p['source_label']} -> {p['target_label']}: {p['count']}条")
                    
                    # 检查是否有标签命名不一致的问题
                    if p['target_label'] and rel_type == 'REQUIRES':
                        # 检查REQUIRES是否真的指向Resource
                        if p['target_label'] != 'Resource':
                            issues.append({
                                'type': 'wrong_target_label',
                                'rel_type': rel_type,
                                'expected': 'Resource',
                                'actual': p['target_label'],
                                'count': p['count'],
                                'severity': 'MEDIUM'
                            })
                    
                    # 检查TRIGGERS的目标
                    if rel_type == 'TRIGGERS' and p['target_label'] == 'Action':
                        # 采样检查目标节点的名称
                        sample = session.run(f"""
                            MATCH (n)-[:{rel_type}]->(m:{p['target_label']})
                            RETURN m.name as name
                            LIMIT 1
                        """).single()
                        
                        if sample and ('级' in sample['name'] or '事件' in sample['name']):
                            print(f"    ⚠️  目标Action像是等级标准: {sample['name']}")
                            issues.append({
                                'type': 'mislabeled_node',
                                'rel_type': rel_type,
                                'issue': 'Action节点实际是等级标准',
                                'sample': sample['name'],
                                'severity': 'HIGH'
                            })
        
        print("\n" + "=" * 80)
        print("阶段 3: 问题汇总")
        print("=" * 80)
        
        high_issues = [i for i in issues if i['severity'] == 'HIGH']
        medium_issues = [i for i in issues if i['severity'] == 'MEDIUM']
        
        print(f"\n发现 {len(high_issues)} 个高优先级问题, {len(medium_issues)} 个中优先级问题\n")
        
        for issue in high_issues:
            if issue['type'] == 'self_loop':
                print(f"🔴 {issue['rel_type']}: {issue['count']}个自循环 (应删除)")
            elif issue['type'] == 'mislabeled_node':
                print(f"🔴 {issue['rel_type']}: {issue['issue']}")
                print(f"   示例: {issue['sample']}")
        
        for issue in medium_issues:
            if issue['type'] == 'wrong_target_label':
                print(f"🟡 {issue['rel_type']}: 目标应该是{issue['expected']}, 实际是{issue['actual']} ({issue['count']}条)")
        
        print("\n" + "=" * 80)
        print("阶段 4: 执行清理操作")
        print("=" * 80)
        
        total_deleted = 0
        
        # 1. 删除所有自循环
        for issue in [i for i in issues if i['type'] == 'self_loop']:
            rel_type = issue['rel_type']
            print(f"\n删除 {rel_type} 的自循环...")
            
            result = session.run(f"""
                MATCH (n)-[r:{rel_type}]->(m)
                WHERE id(n) = id(m)
                DELETE r
                RETURN count(*) as deleted
            """)
            deleted = result.single()['deleted']
            total_deleted += deleted
            print(f"  ✅ 已删除 {deleted} 条自循环关系")
        
        # 2. 删除 Event -[:CLASSIFIED_AS]-> Event (数据质量问题)
        print(f"\n删除有问题的 CLASSIFIED_AS 关系...")
        result = session.run("""
            MATCH (e1:Event)-[r:CLASSIFIED_AS]->(e2:Event)
            WHERE size(e1.name) < 20 AND size(e2.name) > 40
            DELETE r
            RETURN count(*) as deleted
        """)
        deleted = result.single()['deleted']
        total_deleted += deleted
        print(f"  ✅ 已删除 {deleted} 条错误的分类关系")
        
        # 3. 检查剩余的CLASSIFIED_AS
        result = session.run("""
            MATCH (e1:Event)-[r:CLASSIFIED_AS]->(e2:Event)
            RETURN count(*) as remaining
        """)
        remaining = result.single()['remaining']
        if remaining > 0:
            print(f"  ℹ️  保留 {remaining} 条合理的CLASSIFIED_AS关系")
        
        # 4. 删除误标记的 Event -[:TRIGGERS]-> Action (实际是等级)
        print(f"\n删除误标记的 TRIGGERS 关系...")
        result = session.run("""
            MATCH (e:Event)-[r:TRIGGERS]->(a:Action)
            WHERE a.name CONTAINS '级' OR a.name CONTAINS '事件（'
            DELETE r
            RETURN count(*) as deleted
        """)
        deleted = result.single()['deleted']
        total_deleted += deleted
        print(f"  ✅ 已删除 {deleted} 条指向等级标准的关系")
        
        # 5. 删除其他明显错误的关系
        print(f"\n删除其他无效关系...")
        
        # Event -[:MITIGATES]-> Consequence (语义错误,应该是Action缓解Consequence)
        result = session.run("""
            MATCH (e:Event)-[r:MITIGATES]->(c:Consequence)
            DELETE r
            RETURN count(*) as deleted
        """)
        deleted = result.single()['deleted']
        if deleted > 0:
            total_deleted += deleted
            print(f"  ✅ 已删除 {deleted} 条 Event-MITIGATES->Consequence (语义错误)")
        
        # Consequence -[:REQUIRES]-> Action (应该用CONSISTS_OF)
        result = session.run("""
            MATCH (c:Consequence)-[r:REQUIRES]->(a:Action)
            DELETE r
            RETURN count(*) as deleted
        """)
        deleted = result.single()['deleted']
        if deleted > 0:
            total_deleted += deleted
            print(f"  ✅ 已删除 {deleted} 条 Consequence-REQUIRES->Action (应该用CONSISTS_OF)")
        
        print("\n" + "=" * 80)
        print(f"清理完成! 共删除 {total_deleted} 条无效关系")
        print("=" * 80)
        
        # 最终统计
        print("\n生成清理后的统计报告...")
        result = session.run("""
            MATCH ()-[r]->()
            RETURN type(r) as rel_type, count(*) as count
            ORDER BY count DESC
        """)
        
        print("\n清理后的关系统计:")
        total_rels = 0
        for record in result:
            print(f"  - {record['rel_type']}: {record['count']}条")
            total_rels += record['count']
        
        print(f"\n总关系数: {total_rels}")
        
        return total_deleted, total_rels

def main():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        print("开始全面数据质量检查和清理...\n")
        deleted, total = analyze_and_clean(driver)
        
        print("\n" + "=" * 80)
        print("建议后续操作:")
        print("=" * 80)
        print("""
1. 运行 full_graph_report.py 生成新的统计报告
2. 运行 generate_real_doc.py 重新生成文档
3. 更新 .github/copilot-instructions.md 中的Schema信息
        """)
        
    finally:
        driver.close()

if __name__ == "__main__":
    main()
