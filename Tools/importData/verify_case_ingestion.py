import json
import os
import sys
from pathlib import Path

from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vectorDB import ChromaDBVectorStore, PRODUCTION_COLLECTION_NAME  # noqa: E402


NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "trafficv2")
NEO4J_DB = os.getenv("NEO4J_DB", "neo4j")


def verify_neo4j() -> dict:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session(database=NEO4J_DB) as session:
            count = session.run(
                "MATCH ()-[r]->() WHERE r.source = '案例抽取导入' RETURN count(r) AS c"
            ).single()["c"]
            sample = session.run(
                "MATCH (a)-[r]->(b) WHERE r.source = '案例抽取导入' RETURN a.name AS a, type(r) AS t, b.name AS b LIMIT 5"
            ).data()
        return {
            "relationship_count": count,
            "sample": sample,
        }
    finally:
        driver.close()


def verify_chromadb() -> dict:
    store = ChromaDBVectorStore(
        db_path=str(PROJECT_ROOT / "chroma_data"),
        collection_name=PRODUCTION_COLLECTION_NAME,
    )
    return store.get_quality_report()


def main() -> int:
    payload = {
        "neo4j": verify_neo4j(),
        "chroma": verify_chromadb(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
