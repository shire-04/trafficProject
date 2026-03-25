import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPORT_DIR = PROJECT_ROOT / "data_clean" / "neo4j_import"
NODES_CSV = IMPORT_DIR / "国家交通应急预案_neo4j_nodes.csv"
RELATIONSHIPS_CSV = IMPORT_DIR / "国家交通应急预案_neo4j_relationships.csv"
DEFAULT_DB = "neo4j"
DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "trafficv2")


@dataclass
class NodeRow:
    node_id: str
    name: str
    entity_type_cn: str
    labels: List[str]


@dataclass
class RelationshipRow:
    start_id: str
    end_id: str
    relation_cn: str
    relation_type: str


def get_driver():
    return GraphDatabase.driver(DEFAULT_URI, auth=(DEFAULT_USER, DEFAULT_PASSWORD))


def read_nodes() -> List[NodeRow]:
    rows: List[NodeRow] = []
    with NODES_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            raw_labels = (row.get(":LABEL") or "").split(";")
            labels = [label.strip() for label in raw_labels if label.strip()]
            rows.append(
                NodeRow(
                    node_id=(row.get("id:ID") or "").strip(),
                    name=(row.get("name") or "").strip(),
                    entity_type_cn=(row.get("entity_type_cn") or "").strip(),
                    labels=labels,
                )
            )
    return rows


def read_relationships() -> List[RelationshipRow]:
    rows: List[RelationshipRow] = []
    with RELATIONSHIPS_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rows.append(
                RelationshipRow(
                    start_id=(row.get(":START_ID") or "").strip(),
                    end_id=(row.get(":END_ID") or "").strip(),
                    relation_cn=(row.get("relation_cn") or "").strip(),
                    relation_type=(row.get(":TYPE") or "").strip(),
                )
            )
    return rows


def ensure_database_exists(driver, database_name: str) -> None:
    with driver.session(database="system") as session:
        session.run(f"CREATE DATABASE {database_name} IF NOT EXISTS")


def sanitize_labels(labels: Sequence[str]) -> str:
    safe_labels: List[str] = []
    for label in labels:
        cleaned = label.replace("`", "").strip()
        if cleaned:
            safe_labels.append(f"`{cleaned}`")
    if not safe_labels:
        safe_labels.append("`PlanEntity`")
    return ":".join(safe_labels)


def chunked(items: Sequence, size: int) -> Iterable[Sequence]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def import_nodes(driver, database_name: str, nodes: Sequence[NodeRow]) -> int:
    count = 0
    for batch in chunked(list(nodes), 200):
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for node in batch:
            grouped.setdefault(sanitize_labels(node.labels), []).append(
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "entity_type_cn": node.entity_type_cn,
                }
            )

        with driver.session(database=database_name) as session:
            for label_string, rows in grouped.items():
                query = f"""
                UNWIND $rows AS row
                MERGE (n:{label_string} {{id: row.node_id}})
                SET n.name = row.name,
                    n.entity_type_cn = row.entity_type_cn,
                    n.source = '国家交通应急预案',
                    n.graph_version = 'v2'
                """
                session.run(query, rows=rows)
                count += len(rows)
    return count


def import_relationships(driver, database_name: str, relationships: Sequence[RelationshipRow]) -> int:
    count = 0
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for rel in relationships:
        relation_type = rel.relation_type.replace("`", "").strip()
        if not relation_type:
            continue
        grouped.setdefault(relation_type, []).append(
            {
                "start_id": rel.start_id,
                "end_id": rel.end_id,
                "relation_cn": rel.relation_cn,
            }
        )

    with driver.session(database=database_name) as session:
        for relation_type, rows in grouped.items():
            query = f"""
            UNWIND $rows AS row
            MATCH (a {{id: row.start_id}})
            MATCH (b {{id: row.end_id}})
            MERGE (a)-[r:`{relation_type}`]->(b)
            SET r.relation_cn = row.relation_cn,
                r.source = '国家交通应急预案',
                r.graph_version = 'v2'
            """
            session.run(query, rows=rows)
            count += len(rows)
    return count


def prepare(database_name: str) -> None:
    if not NODES_CSV.exists() or not RELATIONSHIPS_CSV.exists():
        raise FileNotFoundError("导入 CSV 文件不存在，请检查 data_clean/neo4j_import 目录。")

    nodes = read_nodes()
    relationships = read_relationships()

    if not nodes:
        raise ValueError("节点 CSV 为空，无法导入。")
    if not relationships:
        raise ValueError("关系 CSV 为空，无法导入。")

    driver = get_driver()
    try:
        ensure_database_exists(driver, database_name)
        imported_nodes = import_nodes(driver, database_name, nodes)
        imported_relationships = import_relationships(driver, database_name, relationships)
    finally:
        driver.close()

    print(f"数据库 {database_name} 已准备完成。")
    print(f"导入节点数: {imported_nodes}")
    print(f"导入关系数: {imported_relationships}")


def validate(database_name: str) -> None:
    driver = get_driver()
    try:
        with driver.session(database=database_name) as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            labels = session.run(
                "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS c ORDER BY label"
            ).data()
            rel_types = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(*) AS c ORDER BY rel_type"
            ).data()
    finally:
        driver.close()

    print(f"数据库: {database_name}")
    print(f"节点总数: {node_count}")
    print(f"关系总数: {rel_count}")
    print("标签统计:")
    for row in labels:
        print(f"- {row['label']}: {row['c']}")
    print("关系统计:")
    for row in rel_types:
        print(f"- {row['rel_type']}: {row['c']}")


def usage() -> None:
    print("用法: python Tools/importData/import_national_plan_to_neo4j.py [prepare|validate] [database_name]")
    print("说明: 当你使用独立 Neo4j 实例时，通常 database_name 保持默认值 neo4j 即可。")


def main(args: Sequence[str]) -> int:
    if len(args) < 2:
        usage()
        return 1

    command = args[1].strip().lower()
    database_name = args[2].strip() if len(args) > 2 else DEFAULT_DB

    if command == "prepare":
        prepare(database_name)
        return 0
    if command == "validate":
        validate(database_name)
        return 0

    usage()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
