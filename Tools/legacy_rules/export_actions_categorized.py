#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导出所有Action节点，按类别、长度、触发频率分类整理
"""
from neo4j import GraphDatabase
import codecs

def export_actions_categorized():
    driver = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'moxiao0906'))
    session = driver.session()
    
    # 获取所有Action及其触发次数
    result = session.run('''
        MATCH (a:Action)
        OPTIONAL MATCH (a)<-[:TRIGGERS|CONSISTS_OF]-(source)
        WITH a, count(source) as trigger_count
        RETURN a.name as name, trigger_count, size(a.name) as length
        ORDER BY trigger_count DESC, length, name
    ''')
    
    actions = list(result)
    
    # 按类别分类
    categories = {
        '交通管制类': ['管制', '封闭', '分流', '疏导', '引导', '限行'],
        '医疗救护类': ['救援', '救护', '急救', '抢救', '医疗', '伤员', '伤情'],
        '现场处置类': ['现场', '处置', '清理', '勘查', '勘察', '破拆'],
        '消防灭火类': ['消防', '灭火', '扑救', '扑灭', '火灾'],
        '危化品处置类': ['危化', '泄漏', '稀释', '堵漏', '中和', '防化'],
        '环境监测类': ['环境', '监测', '检测', '污染'],
        '信息通报类': ['发布', '通报', '报告', '通知', '公布'],
        '资源调度类': ['调度', '调集', '协调', '组织', '启动', '调用'],
        '善后处理类': ['善后', '理赔', '赔偿', '调查', '总结', '评估'],
        '法律法规类': ['法', '条例', '规定', '办法', '预案'],
        '装备物资类': ['装备', '物资', '储备', '配备'],
        '其他': []
    }
    
    categorized_actions = {cat: [] for cat in categories.keys()}
    
    for action in actions:
        name = action['name']
        trigger = action['trigger_count']
        length = action['length']
        
        assigned = False
        for category, keywords in categories.items():
            if category == '其他':
                continue
            if any(kw in name for kw in keywords):
                categorized_actions[category].append({
                    'name': name,
                    'trigger': trigger,
                    'length': length
                })
                assigned = True
                break
        
        if not assigned:
            categorized_actions['其他'].append({
                'name': name,
                'trigger': trigger,
                'length': length
            })
    
    # 写入文件
    output_file = '处置措施分类整理.md'
    with codecs.open(output_file, 'w', 'utf-8') as f:
        f.write('# 交通应急处置措施分类整理\n\n')
        f.write(f'**总数**: 660种\n\n')
        f.write(f'**生成时间**: 2026年1月28日\n\n')
        f.write('**数据来源**: Neo4j知识图谱 (V3.1)\n\n')
        f.write('---\n\n')
        
        # 目录
        f.write('## 目录\n\n')
        for i, (category, items) in enumerate(categorized_actions.items(), 1):
            f.write(f'{i}. [{category}](#{i}-{category.replace("类", "")}) ({len(items)}种)\n')
        f.write('\n---\n\n')
        
        # 详细列表
        for i, (category, items) in enumerate(categorized_actions.items(), 1):
            f.write(f'## {i}. {category} ({len(items)}种)\n\n')
            
            if not items:
                f.write('*暂无数据*\n\n')
                continue
            
            # 按触发次数分组
            high_freq = [item for item in items if item['trigger'] >= 10]
            medium_freq = [item for item in items if 3 <= item['trigger'] < 10]
            low_freq = [item for item in items if 1 <= item['trigger'] < 3]
            never_triggered = [item for item in items if item['trigger'] == 0]
            
            if high_freq:
                f.write(f'### 高频措施 (被引用≥10次, {len(high_freq)}种)\n\n')
                for j, item in enumerate(high_freq, 1):
                    f.write(f'{j}. **[{item["trigger"]}次]** {item["name"]}\n')
                f.write('\n')
            
            if medium_freq:
                f.write(f'### 中频措施 (被引用3-9次, {len(medium_freq)}种)\n\n')
                for j, item in enumerate(medium_freq, 1):
                    f.write(f'{j}. [{item["trigger"]}次] {item["name"]}\n')
                f.write('\n')
            
            if low_freq:
                f.write(f'### 低频措施 (被引用1-2次, {len(low_freq)}种)\n\n')
                for j, item in enumerate(low_freq, 1):
                    f.write(f'{j}. [{item["trigger"]}次] {item["name"]}\n')
                f.write('\n')
            
            if never_triggered:
                f.write(f'### ⚠️ 未被引用 (0次, {len(never_triggered)}种)\n\n')
                f.write('*这些措施从未被任何事件或后果触发，可能是数据导入错误或法规名称误标记*\n\n')
                for j, item in enumerate(never_triggered, 1):
                    if j <= 20:  # 只显示前20个
                        f.write(f'{j}. {item["name"]}\n')
                if len(never_triggered) > 20:
                    f.write(f'... (还有{len(never_triggered)-20}个，已省略)\n')
                f.write('\n')
            
            f.write('---\n\n')
        
        # 统计摘要
        f.write('## 统计摘要\n\n')
        f.write('| 类别 | 数量 | 占比 | 高频(≥10次) | 中频(3-9次) | 低频(1-2次) | 未引用(0次) |\n')
        f.write('|------|------|------|-------------|-------------|-------------|-------------|\n')
        
        total = len(actions)
        for category, items in categorized_actions.items():
            count = len(items)
            percentage = count * 100.0 / total
            high = len([i for i in items if i['trigger'] >= 10])
            medium = len([i for i in items if 3 <= i['trigger'] < 10])
            low = len([i for i in items if 1 <= i['trigger'] < 3])
            never = len([i for i in items if i['trigger'] == 0])
            
            f.write(f'| {category} | {count} | {percentage:.1f}% | {high} | {medium} | {low} | {never} |\n')
        
        f.write(f'| **总计** | **{total}** | **100%** | - | - | - | - |\n\n')
        
        # 长度分布
        f.write('## 措施长度分布\n\n')
        f.write('| 长度范围 | 数量 | 占比 | 说明 |\n')
        f.write('|----------|------|------|------|\n')
        
        length_ranges = [
            ('<10字', 0, 10, '极简'),
            ('10-20字', 10, 20, '简短'),
            ('20-50字', 20, 50, '中等'),
            ('50-100字', 50, 100, '详细'),
            ('≥100字', 100, 999, '极详细')
        ]
        
        for label, min_len, max_len, desc in length_ranges:
            count = len([a for a in actions if min_len <= a['length'] < max_len])
            percentage = count * 100.0 / total
            f.write(f'| {label} | {count} | {percentage:.1f}% | {desc} |\n')
        
        f.write('\n---\n\n')
        f.write('## 数据说明\n\n')
        f.write('1. **触发次数**: 该措施被Event或Consequence通过TRIGGERS/CONSISTS_OF关系引用的次数\n')
        f.write('2. **高频措施**: 核心应急措施，在多个案例中被反复使用\n')
        f.write('3. **未引用措施**: 可能是法规名称误标记、孤立知识或数据导入错误\n')
        f.write('4. **分类方法**: 基于关键词匹配，一个措施只归入第一个匹配的类别\n\n')
    
    session.close()
    driver.close()
    
    print(f'✅ 已生成分类整理文件: {output_file}')
    print(f'   总计: {total} 个措施')
    print(f'   类别: {len(categorized_actions)} 个')

if __name__ == '__main__':
    export_actions_categorized()
