"""
资源关系补全脚本 - 基于语义规则为 Action 补充 REQUIRES 关系
"""
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def analyze_and_enrich_resources():
    print("=" * 80)
    print("资源关系补全分析")
    print("=" * 80)
    
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        with driver.session() as session:
            # === Phase 1: 现状分析 ===
            print("\n【1. 现状分析】")
            print("-" * 80)
            
            # 获取所有资源
            resources = session.run("""
                MATCH (r:Resource)
                RETURN r.name as name
                ORDER BY r.name
            """).data()
            
            print(f"现有资源类型: {len(resources)} 种")
            for r in resources:
                print(f"  - {r['name']}")
            
            # 获取已有资源关系的统计
            resource_usage = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                RETURN r.name as resource, count(a) as action_count
                ORDER BY action_count DESC
            """).data()
            
            print(f"\n当前资源使用情况:")
            for r in resource_usage:
                print(f"  {r['resource']}: {r['action_count']} 个动作")
            
            # 无资源的动作示例
            no_resource_actions = session.run("""
                MATCH (a:Action)
                WHERE NOT (a)-[:REQUIRES]->(:Resource)
                RETURN a.name as name
                LIMIT 20
            """).data()
            
            print(f"\n无资源定义的动作示例:")
            for a in no_resource_actions[:10]:
                print(f"  - {a['name']}")
            
            # === Phase 2: 设计语义匹配规则 ===
            print("\n【2. 语义匹配规则设计】")
            print("-" * 80)
            
            # 定义动作关键词到资源的映射规则
            action_resource_map = {
                # === 人力资源 ===
                "交警": [
                    "交通管制", "事故勘查", "车辆分流", "违法行为查处", "执法", "巡逻",
                    "现场勘查", "责任认定", "疏导", "引导车辆", "维持秩序"
                ],
                "辅警": [
                    "维护现场秩序", "摆放交通设施", "疏散", "警戒", "协助"
                ],
                "医护人员": [
                    "急救", "伤员", "伤情", "救治", "转运", "医疗", "抢救"
                ],
                "消防救援人员": [
                    "破拆", "救援", "火灾", "危化品", "泄漏", "灭火", "扑救"
                ],
                "应急管理人员": [
                    "统筹", "协调", "指挥", "调度", "联动", "应急", "现场处置"
                ],
                "生态环境监测人员": [
                    "检测", "监测", "环境", "污染", "水质", "空气"
                ],
                "道路养护人员": [
                    "修复", "养护", "路面", "清理", "抢修"
                ],
                "法医": [
                    "伤亡", "鉴定", "尸检", "死亡"
                ],
                
                # === 车辆资源 ===
                "清障拖车": [
                    "拖移", "清障", "故障车辆", "事故车辆"
                ],
                "救护车": [
                    "转运", "救护", "伤员", "急救", "医疗"
                ],
                "消防车": [
                    "灭火", "扑救", "火灾", "稀释", "泄漏", "水源"
                ],
                "危化品转运车": [
                    "转移", "危化品", "危险品"
                ],
                "交警执法车": [
                    "执法", "巡逻", "快速抵达", "现场"
                ],
                "工程抢险车": [
                    "抢修", "工程", "道路", "设施"
                ],
                "环境监测车": [
                    "检测", "监测", "环境"
                ],
                
                # === 设施资源 ===
                "交通锥桶": [
                    "划定", "警戒", "引导", "绕行", "隔离"
                ],
                "可变情报板": [
                    "发布", "信息", "预警", "提示"
                ],
                "交通信号灯": [
                    "信号", "配时", "疏导", "调整"
                ],
                "警戒带/隔离护栏": [
                    "隔离", "警戒", "封锁", "防止"
                ],
                "警示灯/反光标识": [
                    "警示", "标识", "提醒"
                ],
                "破拆工具（液压钳、切割机）": [
                    "破拆", "切割", "救援"
                ],
                "防化服/防毒面具": [
                    "防化", "防毒", "保护", "危化品"
                ],
                "喷雾水枪/泡沫枪": [
                    "喷雾", "泡沫", "稀释", "覆盖"
                ],
                "沙袋/围堰材料": [
                    "围堰", "沙袋", "封堵", "拦截"
                ],
            }
            
            print("已定义 {} 种资源的匹配规则".format(len(action_resource_map)))
            
            # === Phase 3: 执行匹配 ===
            print("\n【3. 执行语义匹配】")
            print("-" * 80)
            
            # 获取所有无资源的动作
            actions_without_resource = session.run("""
                MATCH (a:Action)
                WHERE NOT (a)-[:REQUIRES]->(:Resource)
                RETURN a.name as name
            """).data()
            
            matches = []
            for action in actions_without_resource:
                action_name = action['name']
                matched_resources = set()
                
                # 遍历所有资源的关键词规则
                for resource, keywords in action_resource_map.items():
                    for keyword in keywords:
                        if keyword in action_name:
                            matched_resources.add(resource)
                            break
                
                if matched_resources:
                    matches.append({
                        'action': action_name,
                        'resources': list(matched_resources)
                    })
            
            print(f"成功匹配 {len(matches)} 个动作")
            
            # 按资源分组统计
            resource_counts = {}
            for m in matches:
                for r in m['resources']:
                    resource_counts[r] = resource_counts.get(r, 0) + 1
            
            print("\n新增资源关系分布预览:")
            for resource, count in sorted(resource_counts.items(), key=lambda x: x[1], reverse=True):
                print(f"  {resource}: +{count} 个动作")
            
            print("\n匹配结果示例 (前10个):")
            for m in matches[:10]:
                print(f"  {m['action']}")
                for r in m['resources']:
                    print(f"    -> {r}")
            
            # === Phase 4: 用户确认 ===
            print("\n【4. 准备写入数据库】")
            print("-" * 80)
            print(f"即将为 {len(matches)} 个动作补充资源关系")
            print(f"预计新增 {sum(len(m['resources']) for m in matches)} 条 REQUIRES 关系")
            
            response = input("\n是否执行写入? (yes/no): ").strip().lower()
            
            if response != 'yes':
                print("操作已取消")
                return
            
            # === Phase 5: 写入数据库 ===
            print("\n【5. 写入数据库】")
            print("-" * 80)
            
            created_count = 0
            failed_count = 0
            
            for match in matches:
                action_name = match['action']
                for resource_name in match['resources']:
                    try:
                        result = session.run("""
                            MATCH (a:Action {name: $action_name})
                            MATCH (r:Resource {name: $resource_name})
                            MERGE (a)-[rel:REQUIRES]->(r)
                            RETURN count(rel) as created
                        """, action_name=action_name, resource_name=resource_name)
                        
                        if result.single()['created'] > 0:
                            created_count += 1
                    except Exception as e:
                        failed_count += 1
                        print(f"  ⚠️ 失败: {action_name} -> {resource_name}: {e}")
            
            print(f"\n✅ 成功创建 {created_count} 条 REQUIRES 关系")
            if failed_count > 0:
                print(f"⚠️ 失败 {failed_count} 条 (可能是资源节点不存在)")
            
            # === Phase 6: 验证结果 ===
            print("\n【6. 验证结果】")
            print("-" * 80)
            
            # 重新统计
            new_stats = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                RETURN count(DISTINCT a) as actions_with_resource,
                       count(*) as total_requires
            """).single()
            
            total_actions = session.run("MATCH (a:Action) RETURN count(a) as c").single()['c']
            coverage = (new_stats['actions_with_resource'] / total_actions * 100) if total_actions > 0 else 0
            
            print(f"有资源定义的动作: {new_stats['actions_with_resource']} / {total_actions} ({coverage:.1f}%)")
            print(f"REQUIRES 关系总数: {new_stats['total_requires']}")
            
            print("\n资源使用统计:")
            resource_usage_new = session.run("""
                MATCH (a:Action)-[:REQUIRES]->(r:Resource)
                RETURN r.name as resource, count(a) as action_count
                ORDER BY action_count DESC
            """).data()
            
            for r in resource_usage_new[:10]:
                print(f"  {r['resource']}: {r['action_count']} 个动作")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.close()
    
    print("\n" + "=" * 80)
    print("资源补全完成")
    print("=" * 80)

if __name__ == "__main__":
    analyze_and_enrich_resources()
