import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from neo4j import GraphDatabase


DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "trafficv2")
DEFAULT_TARGET_DB = os.getenv("NEO4J_DB", "traffic_plan_v2")


def get_driver() -> GraphDatabase.driver:
    return GraphDatabase.driver(DEFAULT_URI, auth=(DEFAULT_USER, DEFAULT_PASSWORD))


def safe_data(session, query: str, **params: Any) -> List[Dict[str, Any]]:
    return session.run(query, **params).data()


def safe_scalar(session, query: str, key: str, **params: Any) -> Any:
    record = session.run(query, **params).single()
    if not record:
        return None
    return record.data().get(key)


def inspect_system(driver) -> Dict[str, Any]:
    with driver.session(database="system") as session:
        databases = safe_data(
            session,
            "SHOW DATABASES YIELD name, type, aliases, access, address, role, writer, currentStatus, default, home RETURN name, type, aliases, access, address, role, writer, currentStatus, default, home ORDER BY name",
        )
    return {"databases": databases}


def inspect_database(driver, database_name: str) -> Dict[str, Any]:
    with driver.session(database=database_name) as session:
        node_count = safe_scalar(session, "MATCH (n) RETURN count(n) AS c", "c")
        relationship_count = safe_scalar(session, "MATCH ()-[r]->() RETURN count(r) AS c", "c")

        label_counts = safe_data(
            session,
            "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY count DESC, label",
        )
        relationship_type_counts = safe_data(
            session,
            "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(*) AS count ORDER BY count DESC, rel_type",
        )
        schema_patterns = safe_data(
            session,
            "MATCH (a)-[r]->(b) UNWIND labels(a) AS source_label UNWIND labels(b) AS target_label RETURN source_label, type(r) AS rel_type, target_label, count(*) AS count ORDER BY count DESC, source_label, rel_type, target_label LIMIT 50",
        )
        top_out_degree = safe_data(
            session,
            "MATCH (n)-[r]->() WITH n, count(r) AS out_degree RETURN labels(n) AS labels, n.id AS id, n.name AS name, out_degree ORDER BY out_degree DESC, name LIMIT 10",
        )
        top_in_degree = safe_data(
            session,
            "MATCH (n)<-[r]-() WITH n, count(r) AS in_degree RETURN labels(n) AS labels, n.id AS id, n.name AS name, in_degree ORDER BY in_degree DESC, name LIMIT 10",
        )
        sample_nodes = safe_data(
            session,
            "MATCH (n) RETURN labels(n) AS labels, n.id AS id, n.name AS name, properties(n) AS props ORDER BY rand() LIMIT 12",
        )
        sample_relationships = safe_data(
            session,
            "MATCH (a)-[r]->(b) RETURN labels(a) AS source_labels, a.id AS source_id, a.name AS source_name, type(r) AS rel_type, labels(b) AS target_labels, b.id AS target_id, b.name AS target_name, properties(r) AS rel_props ORDER BY rand() LIMIT 20",
        )
        event_samples = safe_data(
            session,
            "MATCH (e:Event) RETURN e.id AS id, e.name AS name ORDER BY rand() LIMIT 12",
        )
        consequence_samples = safe_data(
            session,
            "MATCH (c:Consequence) RETURN c.id AS id, c.name AS name ORDER BY rand() LIMIT 12",
        )
        action_samples = safe_data(
            session,
            "MATCH (a:Action) RETURN a.id AS id, a.name AS name ORDER BY rand() LIMIT 12",
        )
        resource_samples = safe_data(
            session,
            "MATCH (r:Resource) RETURN r.id AS id, r.name AS name ORDER BY rand() LIMIT 12",
        )
        constraints = safe_data(session, "SHOW CONSTRAINTS")
        indexes = safe_data(session, "SHOW INDEXES")

    return {
        "database": database_name,
        "node_count": node_count,
        "relationship_count": relationship_count,
        "label_counts": label_counts,
        "relationship_type_counts": relationship_type_counts,
        "schema_patterns": schema_patterns,
        "top_out_degree": top_out_degree,
        "top_in_degree": top_in_degree,
        "sample_nodes": sample_nodes,
        "sample_relationships": sample_relationships,
        "event_samples": event_samples,
        "consequence_samples": consequence_samples,
        "action_samples": action_samples,
        "resource_samples": resource_samples,
        "constraints": constraints,
        "indexes": indexes,
    }


def resolve_target_database(system_info: Dict[str, Any], requested_name: str) -> str:
    database_names = {item.get("name") for item in system_info.get("databases", [])}
    if requested_name in database_names:
        return requested_name

    for item in system_info.get("databases", []):
        if item.get("default"):
            return item.get("name") or requested_name
    return requested_name


def main(args: List[str]) -> int:
    requested_db = args[1].strip() if len(args) > 1 and str(args[1]).strip() else DEFAULT_TARGET_DB
    output_path = Path(args[2]).resolve() if len(args) > 2 and str(args[2]).strip() else None

    driver = get_driver()
    try:
        system_info = inspect_system(driver)
        target_database = resolve_target_database(system_info, requested_db)
        database_info = inspect_database(driver, target_database)
    finally:
        driver.close()

    payload = {
        "connection": {
            "uri": DEFAULT_URI,
            "user": DEFAULT_USER,
            "requested_database": requested_db,
            "inspected_database": target_database,
        },
        "system": system_info,
        "database_report": database_info,
    }

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if output_path:
        output_path.write_text(rendered, encoding="utf-8")
        print(f"报告已写入: {output_path}")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
