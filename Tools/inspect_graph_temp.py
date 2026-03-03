from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "moxiao0906")

def inspect_quality():
    print(f"正在连接 Neo4j: {URI} 进行深度质量检查...")
    driver = GraphDatabase.driver(URI, auth=AUTH)
    
    try:
        with driver.session() as session:
            # 1. 检查标签混乱情况 (Case sensitivity)
            print("\n=== 1. 标签大小写混用检查 ===")
            result = session.run("CALL db.labels()")
            labels = [r["label"] for r in result]
            print(f"所有标签: {labels}")
            
            # 检查 action vs Action
            for label in labels:
                count = session.run(f"MATCH (n:`{label}`) RETURN count(n) as c").single()["c"]
                print(f"Label `{label}`: {count} nodes")

            # 2. 检查空属性节点 (Empty/Null Name)
            print("\n=== 2. 空名称节点检查 (Nodes with empty/null name) ===")
            for label in labels:
                query = f"""
                MATCH (n:`{label}`) 
                WHERE n.name IS NULL OR n.name = '' OR trim(n.name) = ''
                RETURN count(n) as c, collect(id(n))[0..5] as sample_ids
                """
                record = session.run(query).single()
                if record["c"] > 0:
                    print(f"❌ Label `{label}` has {record['c']} nodes with empty names! Sample IDs: {record['sample_ids']}")
                else:
                    print(f"✅ Label `{label}` names are clean.")

            # 3. 检查孤立节点 (Disconnected Nodes)
            print("\n=== 3. 孤立节点检查 (Disconnected Nodes) ===")
            query = """
            MATCH (n)
            WHERE NOT (n)--()
            RETURN labels(n) as labels, count(*) as c
            ORDER BY c DESC
            """
            result = session.run(query)
            found_isolated = False
            for record in result:
                found_isolated = True
                print(f"⚠️  Isolated Nodes {record['labels']}: {record['c']} nodes")
            if not found_isolated:
                print("✅ No isolated nodes found.")

            # 4. 检查重复节点 (Duplicate Names per Label)
            print("\n=== 4. 重复名称节点检查 (Duplicate Names) ===")
            for label in ["Event", "Action", "Resource", "Consequence"]:
                if label in labels:
                    query = f"""
                    MATCH (n:`{label}`)
                    WITH n.name as name, count(*) as c
                    WHERE c > 1
                    RETURN name, c
                    ORDER BY c DESC LIMIT 5
                    """
                    result = session.run(query)
                    print(f"Checking duplicates for `{label}`:")
                    has_dupes = False
                    for record in result:
                        has_dupes = True
                        print(f"  - '{record['name']}': {record['c']} copies")
                    if not has_dupes:
                        print("  ✅ No duplicates found.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    inspect_quality()
