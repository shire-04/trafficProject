"""
为所有关系连接模式生成真实示例
"""
from neo4j import GraphDatabase
import random

# Neo4j连接配置
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def get_real_examples(driver):
    """为每种关系模式获取真实示例"""
    
    examples = {}
    
    with driver.session() as session:
        # 1. Action -[:NEXT_STEP]-> Action (1,405条)
        result = session.run("""
            MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
            RETURN a1.name as source, a2.name as target
            LIMIT 3
        """)
        examples['Action-NEXT_STEP-Action'] = [dict(r) for r in result]
        
        # 2. Action -[:REQUIRES]-> Resource (970条)
        result = session.run("""
            MATCH (a:Action)-[:REQUIRES]->(r:Resource)
            RETURN a.name as source, r.name as target
            LIMIT 3
        """)
        examples['Action-REQUIRES-Resource'] = [dict(r) for r in result]
        
        # 3. Event -[:TRIGGERS]-> Action (524条)
        result = session.run("""
            MATCH (e:Event)-[:TRIGGERS]->(a:Action)
            RETURN e.name as source, a.name as target
            LIMIT 3
        """)
        examples['Event-TRIGGERS-Action'] = [dict(r) for r in result]
        
        # 4. Consequence -[:CONSISTS_OF]-> Action (457条)
        result = session.run("""
            MATCH (c:Consequence)-[:CONSISTS_OF]->(a:Action)
            RETURN c.name as source, a.name as target
            LIMIT 3
        """)
        examples['Consequence-CONSISTS_OF-Action'] = [dict(r) for r in result]
        
        # 5. Event -[:LEADS_TO]-> Consequence (140条)
        result = session.run("""
            MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)
            RETURN e.name as source, c.name as target
            LIMIT 3
        """)
        examples['Event-LEADS_TO-Consequence'] = [dict(r) for r in result]
        
        # 6. Event -[:CLASSIFIED_AS]-> Event (140条)
        result = session.run("""
            MATCH (e1:Event)-[:CLASSIFIED_AS]->(e2:Event)
            RETURN e1.name as source, e2.name as target
            LIMIT 3
        """)
        examples['Event-CLASSIFIED_AS-Event'] = [dict(r) for r in result]
        
        # 7. Event -[:REQUIRES]-> Action (103条)
        result = session.run("""
            MATCH (e:Event)-[:REQUIRES]->(a:Action)
            RETURN e.name as source, a.name as target
            LIMIT 3
        """)
        examples['Event-REQUIRES-Action'] = [dict(r) for r in result]
        
        # 8. Action -[:MITIGATES]-> Consequence (50条)
        result = session.run("""
            MATCH (a:Action)-[:MITIGATES]->(c:Consequence)
            RETURN a.name as source, c.name as target
            LIMIT 3
        """)
        examples['Action-MITIGATES-Consequence'] = [dict(r) for r in result]
        
        # 9. Consequence -[:NEXT_STEP]-> Event (32条)
        result = session.run("""
            MATCH (c:Consequence)-[:NEXT_STEP]->(e:Event)
            RETURN c.name as source, e.name as target
            LIMIT 3
        """)
        examples['Consequence-NEXT_STEP-Event'] = [dict(r) for r in result]
        
        # 10. Event -[:MITIGATES]-> Consequence (20条)
        result = session.run("""
            MATCH (e:Event)-[:MITIGATES]->(c:Consequence)
            RETURN e.name as source, c.name as target
            LIMIT 3
        """)
        examples['Event-MITIGATES-Consequence'] = [dict(r) for r in result]
        
        # 11. Consequence -[:REQUIRES]-> Action (12条)
        result = session.run("""
            MATCH (c:Consequence)-[:REQUIRES]->(a:Action)
            RETURN c.name as source, a.name as target
            LIMIT 3
        """)
        examples['Consequence-REQUIRES-Action'] = [dict(r) for r in result]
    
    return examples

def generate_markdown(examples):
    """生成Markdown格式的文档"""
    
    md_content = """
### 3.1 Action -[:NEXT_STEP]-> Action (1,405条)
**用途**: 任务分解,将高层任务分解为具体执行步骤

**Cypher查询**:
```cypher
MATCH (a1:Action)-[:NEXT_STEP]->(a2:Action)
RETURN a1.name, a2.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Action-NEXT_STEP-Action']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.2 Action -[:REQUIRES]-> Resource (970条)
**用途**: 资源调度,定义动作所需的人力、车辆、设施资源

**Cypher查询**:
```cypher
MATCH (a:Action)-[:REQUIRES]->(r:Resource)
RETURN a.name, r.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Action-REQUIRES-Resource']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.3 Event -[:TRIGGERS]-> Action (524条)
**用途**: 事件分类触发,标记事件的等级和类型

**Cypher查询**:
```cypher
MATCH (e:Event)-[:TRIGGERS]->(a:Action)
RETURN e.name, a.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Event-TRIGGERS-Action']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.4 Consequence -[:CONSISTS_OF]-> Action (457条)
**用途**: 后果包含动作,定义处理特定后果所需的具体措施

**Cypher查询**:
```cypher
MATCH (c:Consequence)-[:CONSISTS_OF]->(a:Action)
RETURN c.name, a.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Consequence-CONSISTS_OF-Action']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.5 Event -[:LEADS_TO]-> Consequence (140条)
**用途**: 因果推理,事件导致的直接后果

**Cypher查询**:
```cypher
MATCH (e:Event)-[:LEADS_TO]->(c:Consequence)
RETURN e.name, c.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Event-LEADS_TO-Consequence']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.6 Event -[:CLASSIFIED_AS]-> Event (140条)
**用途**: 事件分类,将具体事件归类到标准等级

**Cypher查询**:
```cypher
MATCH (e1:Event)-[:CLASSIFIED_AS]->(e2:Event)
RETURN e1.name, e2.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Event-CLASSIFIED_AS-Event']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.7 Event -[:REQUIRES]-> Action (103条)
**用途**: 事件直接触发的应对动作

**Cypher查询**:
```cypher
MATCH (e:Event)-[:REQUIRES]->(a:Action)
RETURN e.name, a.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Event-REQUIRES-Action']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.8 Action -[:MITIGATES]-> Consequence (50条)
**用途**: 动作缓解后果,定义哪些措施可以减轻特定后果

**Cypher查询**:
```cypher
MATCH (a:Action)-[:MITIGATES]->(c:Consequence)
RETURN a.name, c.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Action-MITIGATES-Consequence']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.9 Consequence -[:NEXT_STEP]-> Event (32条)
**用途**: 后果演变,某个后果可能导致新的事件

**Cypher查询**:
```cypher
MATCH (c:Consequence)-[:NEXT_STEP]->(e:Event)
RETURN c.name, e.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Consequence-NEXT_STEP-Event']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.10 Event -[:MITIGATES]-> Consequence (20条)
**用途**: 事件缓解后果 (较少使用)

**Cypher查询**:
```cypher
MATCH (e:Event)-[:MITIGATES]->(c:Consequence)
RETURN e.name, c.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Event-MITIGATES-Consequence']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    md_content += """
---

### 3.11 Consequence -[:REQUIRES]-> Action (12条)
**用途**: 后果需要的动作 (较少使用,多数通过CONSISTS_OF表达)

**Cypher查询**:
```cypher
MATCH (c:Consequence)-[:REQUIRES]->(a:Action)
RETURN c.name, a.name
LIMIT 3
```

**真实示例**:
"""
    for ex in examples['Consequence-REQUIRES-Action']:
        md_content += f"- `{ex['source']}` → `{ex['target']}`\n"
    
    return md_content

def main():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        print("正在查询Neo4j获取真实示例...")
        examples = get_real_examples(driver)
        
        print("生成Markdown内容...")
        md_content = generate_markdown(examples)
        
        # 输出到文件
        with open('核心推理路径示例.md', 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print("✅ 已生成: 核心推理路径示例.md")
        print(f"总字符数: {len(md_content)}")
        
    finally:
        driver.close()

if __name__ == "__main__":
    main()
