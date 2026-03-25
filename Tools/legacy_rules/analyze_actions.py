#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析Action节点的具体性和分类统计
"""
from neo4j import GraphDatabase

def analyze_actions():
    driver = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'moxiao0906'))
    session = driver.session()
    
    print("=" * 80)
    print("交通应急处置措施 (Action) 详细分析报告")
    print("=" * 80)
    
    # 1. 基础统计
    print("\n【1. 基础统计】")
    result = session.run('MATCH (a:Action) RETURN count(a) as total')
    total = result.single()['total']
    print(f"处置措施总数: {total}")
    
    # 2. 按名称长度分类
    print("\n【2. 按名称长度分类】")
    print("-" * 80)
    
    result = session.run('''
        MATCH (a:Action)
        WITH a, size(a.name) as len
        RETURN 
            CASE 
                WHEN len < 10 THEN '<10字(极简)'
                WHEN len < 20 THEN '10-20字(简短)'
                WHEN len < 50 THEN '20-50字(中等)'
                WHEN len < 100 THEN '50-100字(详细)'
                ELSE '>=100字(极详细)'
            END as category,
            count(*) as count,
            round(count(*) * 100.0 / $total, 1) as percentage
        ORDER BY category
    ''', total=total)
    
    for record in result:
        print(f"{record['category']:20s} {record['count']:4d} ({record['percentage']:5.1f}%)")
    
    # 3. 抽样检查：具体性分析
    print("\n【3. 具体性分析 - 随机抽样20个措施】")
    print("-" * 80)
    
    result = session.run('''
        MATCH (a:Action)
        WITH a, rand() as r
        ORDER BY r
        LIMIT 20
        RETURN a.name as name, size(a.name) as len
        ORDER BY len
    ''')
    
    for i, record in enumerate(result, 1):
        name = record['name']
        length = record['len']
        print(f"{i:2d}. [{length:3d}字] {name}")
    
    # 4. 最常被触发的措施 (高频措施)
    print("\n【4. 最常被触发的措施 (Top 15)】")
    print("-" * 80)
    
    result = session.run('''
        MATCH (a:Action)<-[:TRIGGERS|CONSISTS_OF]-(source)
        WITH a, count(*) as trigger_count
        ORDER BY trigger_count DESC
        LIMIT 15
        RETURN a.name as name, trigger_count, size(a.name) as len
    ''')
    
    for i, record in enumerate(result, 1):
        print(f"{i:2d}. [引用{record['trigger_count']:3d}次] {record['name'][:60]}{'...' if record['len'] > 60 else ''}")
    
    # 5. 检查是否有法规名称误标为Action
    print("\n【5. 疑似法规名称的Action (含'法'/'条例'/'规定'等关键词)】")
    print("-" * 80)
    
    result = session.run('''
        MATCH (a:Action)
        WHERE a.name CONTAINS '法' OR a.name CONTAINS '条例' OR 
              a.name CONTAINS '规定' OR a.name CONTAINS '办法' OR
              a.name CONTAINS '预案'
        RETURN a.name as name
        LIMIT 20
    ''')
    
    regulations = list(result)
    if regulations:
        print(f"找到 {len(regulations)} 个疑似法规名称:")
        for i, record in enumerate(regulations, 1):
            print(f"{i:2d}. {record['name'][:80]}")
    else:
        print("✅ 未发现明显的法规名称误标")
    
    # 6. 按语义分类统计 (基于关键词)
    print("\n【6. 按语义分类统计 (基于关键词)】")
    print("-" * 80)
    
    categories = {
        '交通管制': ['管制', '封闭', '分流', '疏导'],
        '救援救护': ['救援', '救护', '急救', '抢救', '破拆'],
        '现场处置': ['现场', '处置', '清理', '勘查'],
        '信息发布': ['发布', '通报', '报告', '通知'],
        '资源调度': ['调度', '调集', '协调', '组织'],
        '环境安全': ['环境', '监测', '危化', '防护'],
        '后续处理': ['善后', '理赔', '调查', '总结']
    }
    
    for category, keywords in categories.items():
        query = ' OR '.join([f"a.name CONTAINS '{kw}'" for kw in keywords])
        result = session.run(f'''
            MATCH (a:Action)
            WHERE {query}
            RETURN count(a) as count
        ''')
        count = result.single()['count']
        percentage = count * 100.0 / total
        print(f"{category:12s} {count:4d} ({percentage:5.1f}%)")
    
    # 7. 完整案例措施展示
    print("\n【7. 典型事件的完整处置措施链】")
    print("-" * 80)
    
    result = session.run('''
        MATCH (e:Event)-[:TRIGGERS]->(a:Action)
        WHERE size(e.name) < 30
        WITH e, collect(a.name)[0..5] as actions
        WHERE size(actions) >= 3
        RETURN e.name as event, actions
        LIMIT 3
    ''')
    
    for i, record in enumerate(result, 1):
        print(f"\n案例{i}: 事件 = {record['event']}")
        for j, action in enumerate(record['actions'], 1):
            print(f"  {j}. {action[:70]}{'...' if len(action) > 70 else ''}")
    
    session.close()
    driver.close()
    
    print("\n" + "=" * 80)
    print("分析完成")
    print("=" * 80)

if __name__ == '__main__':
    analyze_actions()
