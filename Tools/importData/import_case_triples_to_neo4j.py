import csv
import os
import sys
import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "trafficv2")
DEFAULT_DB = os.getenv("NEO4J_DB", "neo4j")
DEFAULT_SOURCE_TAG = os.getenv("CASE_IMPORT_SOURCE_TAG", "案例抽取导入")
DEFAULT_GRAPH_VERSION = os.getenv("CASE_IMPORT_GRAPH_VERSION", "case_v1")
DEFAULT_NODES_CSV = PROJECT_ROOT / "data_clean" / "case_extract_output" / "case_new_nodes.csv"
DEFAULT_RELATIONSHIPS_CSV = PROJECT_ROOT / "data_clean" / "case_extract_output" / "case_new_relationships.csv"
DEFAULT_MISSING_ENDPOINTS_REPORT = PROJECT_ROOT / "data_clean" / "case_extract_output" / "missing_endpoints.csv"
DEFAULT_BASELINE_NODES_CSV = PROJECT_ROOT / "data_clean" / "neo4j_import" / "国家交通应急预案_neo4j_nodes.csv"
DEFAULT_EVENT_ALIASES_CSV = PROJECT_ROOT / "data_clean" / "event_aliases.csv"


def chunked(items: Sequence[Dict[str, str]], size: int) -> Iterable[Sequence[Dict[str, str]]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


class CaseGraphImporter:
    def __init__(self, uri: str, user: str, password: str, database: str, source_tag: str, graph_version: str):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database
        self.source_tag = source_tag
        self.graph_version = graph_version

    def close(self) -> None:
        self.driver.close()

    def import_data(
        self,
        nodes_csv_path: Path,
        relationships_csv_path: Path,
        skip_nodes: bool = False,
        dry_run: bool = False,
        fail_on_missing_endpoints: bool = True,
        missing_report_path: Path | None = None,
    ) -> Dict[str, Any]:
        if not skip_nodes and not nodes_csv_path.exists():
            raise FileNotFoundError(f"节点 CSV 不存在: {nodes_csv_path}")
        if not relationships_csv_path.exists():
            raise FileNotFoundError(f"关系 CSV 不存在: {relationships_csv_path}")

        node_rows = self._read_nodes(nodes_csv_path) if not skip_nodes else []
        relation_rows = self._read_relationships(relationships_csv_path)

        node_ids_in_csv = {row["node_id"] for row in node_rows if row.get("node_id")}
        missing_rows = self._find_missing_relationship_endpoints(relation_rows, node_ids_in_csv)

        if missing_rows:
            report_path = missing_report_path or DEFAULT_MISSING_ENDPOINTS_REPORT
            self._write_missing_endpoints_report(report_path, missing_rows)
            if fail_on_missing_endpoints:
                raise ValueError(
                    f"发现 {len(missing_rows)} 条关系端点缺失，已输出报告: {report_path}"
                )

        if dry_run:
            return {
                "dry_run": True,
                "nodes_csv_rows": len(node_rows),
                "relationships_csv_rows": len(relation_rows),
                "missing_endpoint_rows": len(missing_rows),
                "missing_endpoint_report": str((missing_report_path or DEFAULT_MISSING_ENDPOINTS_REPORT).resolve()) if missing_rows else "",
                "imported_nodes": 0,
                "imported_relationships": 0,
                "created_nodes": 0,
                "created_relationships": 0,
            }

        node_result = self._import_nodes(node_rows) if not skip_nodes else {"processed": 0, "created": 0}
        relation_result = self._import_relationships(relation_rows)
        return {
            "dry_run": False,
            "nodes_csv_rows": len(node_rows),
            "relationships_csv_rows": len(relation_rows),
            "missing_endpoint_rows": len(missing_rows),
            "missing_endpoint_report": str((missing_report_path or DEFAULT_MISSING_ENDPOINTS_REPORT).resolve()) if missing_rows else "",
            "imported_nodes": int(node_result.get("processed", 0)),
            "imported_relationships": int(relation_result.get("processed", 0)),
            "created_nodes": int(node_result.get("created", 0)),
            "created_relationships": int(relation_result.get("created", 0)),
        }

    def _read_nodes(self, nodes_csv_path: Path) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        with nodes_csv_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                node_id = str(row.get("id:ID") or "").strip()
                labels = str(row.get(":LABEL") or "").strip()
                if not node_id or not labels:
                    continue
                rows.append(
                    {
                        "node_id": node_id,
                        "labels": labels,
                        "name": str(row.get("name") or "").strip(),
                        "entity_type_cn": str(row.get("entity_type_cn") or "").strip(),
                    }
                )
        return rows

    def _read_relationships(self, relationships_csv_path: Path) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        with relationships_csv_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                start_id = str(row.get(":START_ID") or "").strip()
                end_id = str(row.get(":END_ID") or "").strip()
                relation_type = str(row.get(":TYPE") or "").strip().upper()
                if not start_id or not end_id or not relation_type:
                    continue
                rows.append(
                    {
                        "start_id": start_id,
                        "end_id": end_id,
                        "relation_type": relation_type,
                        "relation_cn": str(row.get("relation_cn") or "").strip(),
                        "case_id": str(row.get("case_id") or "").strip(),
                        "case_title": str(row.get("case_title") or "").strip(),
                        "evidence": str(row.get("evidence") or "").strip(),
                        "confidence": str(row.get("confidence") or "").strip(),
                    }
                )
        return rows

    def _get_existing_node_ids(self, candidate_ids: Set[str]) -> Set[str]:
        if not candidate_ids:
            return set()
        existing_ids: Set[str] = set()
        id_list = list(candidate_ids)
        with self.driver.session(database=self.database) as session:
            for batch in chunked([{"node_id": value} for value in id_list], 500):
                result = session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (n {id: row.node_id})
                    RETURN n.id AS node_id
                    """,
                    rows=list(batch),
                )
                for record in result:
                    node_id = str(record.get("node_id") or "").strip()
                    if node_id:
                        existing_ids.add(node_id)
        return existing_ids

    def _find_missing_relationship_endpoints(
        self,
        relation_rows: List[Dict[str, str]],
        node_ids_in_csv: Set[str],
    ) -> List[Dict[str, str]]:
        all_endpoint_ids: Set[str] = set()
        for row in relation_rows:
            all_endpoint_ids.add(str(row.get("start_id") or "").strip())
            all_endpoint_ids.add(str(row.get("end_id") or "").strip())
        all_endpoint_ids.discard("")

        existing_ids = self._get_existing_node_ids(all_endpoint_ids)
        available_ids = existing_ids | node_ids_in_csv
        missing_rows: List[Dict[str, str]] = []
        for row in relation_rows:
            start_id = str(row.get("start_id") or "").strip()
            end_id = str(row.get("end_id") or "").strip()
            start_missing = bool(start_id) and start_id not in available_ids
            end_missing = bool(end_id) and end_id not in available_ids
            if not start_missing and not end_missing:
                continue

            if start_missing and end_missing:
                missing_type = "both_missing"
            elif start_missing:
                missing_type = "start_missing"
            else:
                missing_type = "end_missing"

            missing_rows.append(
                {
                    "start_id": start_id,
                    "end_id": end_id,
                    "relation_type": str(row.get("relation_type") or "").strip(),
                    "case_id": str(row.get("case_id") or "").strip(),
                    "case_title": str(row.get("case_title") or "").strip(),
                    "missing_type": missing_type,
                }
            )
        return missing_rows

    def _write_missing_endpoints_report(self, report_path: Path, rows: List[Dict[str, str]]) -> None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open(mode="w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["start_id", "end_id", "relation_type", "case_id", "case_title", "missing_type"],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def _import_nodes(self, node_rows: List[Dict[str, str]]) -> Dict[str, int]:
        grouped_rows: Dict[str, List[Dict[str, str]]] = {}
        for row in node_rows:
            labels = str(row.get("labels") or "").strip()
            if not labels:
                continue
            grouped_rows.setdefault(labels, []).append(
                {
                    "node_id": str(row.get("node_id") or "").strip(),
                    "name": str(row.get("name") or "").strip(),
                    "entity_type_cn": str(row.get("entity_type_cn") or "").strip(),
                }
            )

        processed_count = 0
        created_count = 0
        with self.driver.session(database=self.database) as session:
            for labels, rows in grouped_rows.items():
                safe_labels = ":".join(f"`{label.strip().replace('`', '')}`" for label in labels.split(";") if label.strip())
                for batch in chunked(rows, 200):
                    query = f"""
                    UNWIND $rows AS row
                    MERGE (n:{safe_labels} {{id: row.node_id}})
                    ON CREATE SET n.name = row.name,
                                  n.entity_type_cn = row.entity_type_cn,
                                  n.source = $source_tag,
                                  n.graph_version = $graph_version
                    ON MATCH SET n.name = coalesce(n.name, row.name),
                                 n.entity_type_cn = coalesce(n.entity_type_cn, row.entity_type_cn)
                    RETURN count(*) AS processed
                    """
                    result = session.run(
                        query,
                        rows=list(batch),
                        source_tag=self.source_tag,
                        graph_version=self.graph_version,
                    )
                    record = result.single() or {}
                    summary = result.consume()
                    processed_count += int(record.get("processed") or 0)
                    created_count += int(summary.counters.nodes_created)
        return {"processed": processed_count, "created": created_count}

    def _import_relationships(self, relationship_rows: List[Dict[str, str]]) -> Dict[str, int]:
        grouped_rows: Dict[str, List[Dict[str, str]]] = {}
        for row in relationship_rows:
            relation_type = str(row.get("relation_type") or "").strip().upper()
            if not relation_type:
                continue
            grouped_rows.setdefault(relation_type, []).append(
                {
                    "start_id": str(row.get("start_id") or "").strip(),
                    "end_id": str(row.get("end_id") or "").strip(),
                    "relation_cn": str(row.get("relation_cn") or "").strip(),
                    "case_id": str(row.get("case_id") or "").strip(),
                    "case_title": str(row.get("case_title") or "").strip(),
                    "evidence": str(row.get("evidence") or "").strip(),
                    "confidence": str(row.get("confidence") or "").strip(),
                }
            )

        processed_count = 0
        created_count = 0
        with self.driver.session(database=self.database) as session:
            for relation_type, rows in grouped_rows.items():
                safe_relation_type = relation_type.replace("`", "").strip()
                for batch in chunked(rows, 200):
                    query = f"""
                    UNWIND $rows AS row
                    MATCH (a {{id: row.start_id}})
                    MATCH (b {{id: row.end_id}})
                    MERGE (a)-[r:`{safe_relation_type}` {{case_id: row.case_id, source: $source_tag}}]->(b)
                    ON CREATE SET r.relation_cn = row.relation_cn,
                                  r.case_title = row.case_title,
                                  r.evidence = row.evidence,
                                  r.confidence = row.confidence,
                                  r.graph_version = $graph_version
                    ON MATCH SET r.relation_cn = coalesce(r.relation_cn, row.relation_cn),
                                 r.case_title = coalesce(r.case_title, row.case_title),
                                 r.evidence = coalesce(r.evidence, row.evidence),
                                 r.confidence = coalesce(r.confidence, row.confidence),
                                 r.graph_version = coalesce(r.graph_version, $graph_version)
                    RETURN count(*) AS processed
                    """
                    result = session.run(
                        query,
                        rows=list(batch),
                        source_tag=self.source_tag,
                        graph_version=self.graph_version,
                    )
                    record = result.single() or {}
                    summary = result.consume()
                    processed_count += int(record.get("processed") or 0)
                    created_count += int(summary.counters.relationships_created)
        return {"processed": processed_count, "created": created_count}

    def export_node_baseline_csv(self, output_path: Path) -> Dict[str, int]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        query = """
        MATCH (n)
        WHERE n.id IS NOT NULL
          AND any(label IN labels(n) WHERE label IN ['Event', 'Action', 'Resource', 'Department'])
        RETURN n.id AS node_id,
               coalesce(n.name, '') AS name,
               coalesce(n.entity_type_cn, '') AS entity_type_cn,
               labels(n) AS labels
        ORDER BY node_id
        """

        rows: List[Dict[str, str]] = []
        with self.driver.session(database=self.database) as session:
            result = session.run(query)
            for record in result:
                node_id = str(record.get("node_id") or "").strip()
                if not node_id:
                    continue
                labels = [str(label).strip() for label in (record.get("labels") or []) if str(label).strip()]
                preferred_order = ["PlanEntity", "Event", "Action", "Resource", "Department"]
                ordered_labels = [label for label in preferred_order if label in labels]
                extra_labels = sorted([label for label in labels if label not in preferred_order])
                merged_labels = ordered_labels + extra_labels
                rows.append(
                    {
                        "id:ID": node_id,
                        "name": str(record.get("name") or "").strip(),
                        "entity_type_cn": str(record.get("entity_type_cn") or "").strip(),
                        ":LABEL": ";".join(merged_labels),
                    }
                )

        with output_path.open(mode="w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["id:ID", "name", "entity_type_cn", ":LABEL"])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        return {"row_count": len(rows)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导入案例抽取形成的节点和关系 CSV 到 Neo4j。")
    parser.add_argument("nodes_csv", nargs="?", default=str(DEFAULT_NODES_CSV), help="节点 CSV 路径")
    parser.add_argument("relationships_csv", nargs="?", default=str(DEFAULT_RELATIONSHIPS_CSV), help="关系 CSV 路径")
    parser.add_argument("database", nargs="?", default=DEFAULT_DB, help="数据库名")
    parser.add_argument("--source-tag", default=DEFAULT_SOURCE_TAG, help="写入关系与节点的批次来源标签")
    parser.add_argument("--graph-version", default=DEFAULT_GRAPH_VERSION, help="写入的图谱版本标识")
    parser.add_argument("--skip-nodes", action="store_true", help="跳过节点导入，仅导入关系")
    parser.add_argument("--dry-run", action="store_true", help="仅做端点校验和统计，不写入数据库")
    parser.add_argument(
        "--allow-missing-endpoints",
        action="store_true",
        help="允许关系端点缺失，不阻断导入（仍会输出缺失报告）",
    )
    parser.add_argument(
        "--missing-report",
        default=str(DEFAULT_MISSING_ENDPOINTS_REPORT),
        help="缺失端点报告 CSV 输出路径",
    )
    parser.add_argument(
        "--node-baseline-csv",
        default=str(DEFAULT_BASELINE_NODES_CSV),
        help="导入完成后回写的节点基线 CSV 路径",
    )
    parser.add_argument(
        "--skip-refresh-node-baseline",
        action="store_true",
        help="跳过导入后节点基线 CSV 回写",
    )
    parser.add_argument(
        "--event-aliases-csv",
        default=str(DEFAULT_EVENT_ALIASES_CSV),
        help="导入后自动更新的事件别名总表路径",
    )
    parser.add_argument(
        "--event-alias-patch-csv",
        default="",
        help="事件别名补丁 CSV 路径（为空时默认使用 relationships_csv 同目录下 event_aliases_patch.csv）",
    )
    parser.add_argument(
        "--skip-merge-event-aliases",
        action="store_true",
        help="跳过导入后事件别名补丁合并",
    )
    return parser


def resolve_csv_path(arg: str) -> Path:
    candidate = Path(arg)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def merge_event_alias_patch(alias_csv_path: Path, patch_csv_path: Path) -> Dict[str, int]:
    alias_csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["entity_id", "entity_name", "entity_type", "alias", "alias_type", "source"]
    merged_rows: List[Dict[str, str]] = []
    merged_keys: Set[Tuple[str, str]] = set()

    if alias_csv_path.exists():
        with alias_csv_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                normalized_row = {
                    "entity_id": str(row.get("entity_id") or "").strip(),
                    "entity_name": str(row.get("entity_name") or "").strip(),
                    "entity_type": str(row.get("entity_type") or "").strip() or "Event",
                    "alias": str(row.get("alias") or "").strip(),
                    "alias_type": str(row.get("alias_type") or "").strip() or "scene_expression",
                    "source": str(row.get("source") or "").strip(),
                }
                if not normalized_row["entity_id"] or not normalized_row["alias"]:
                    continue
                key = (normalized_row["entity_id"], normalized_row["alias"])
                if key in merged_keys:
                    continue
                merged_keys.add(key)
                merged_rows.append(normalized_row)

    patch_rows = 0
    appended_rows = 0
    if patch_csv_path.exists():
        with patch_csv_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                patch_rows += 1
                normalized_row = {
                    "entity_id": str(row.get("entity_id") or "").strip(),
                    "entity_name": str(row.get("entity_name") or "").strip(),
                    "entity_type": str(row.get("entity_type") or "").strip() or "Event",
                    "alias": str(row.get("alias") or "").strip(),
                    "alias_type": str(row.get("alias_type") or "").strip() or "scene_expression",
                    "source": str(row.get("source") or "").strip(),
                }
                if not normalized_row["entity_id"] or not normalized_row["alias"]:
                    continue
                key = (normalized_row["entity_id"], normalized_row["alias"])
                if key in merged_keys:
                    continue
                merged_keys.add(key)
                merged_rows.append(normalized_row)
                appended_rows += 1

    merged_rows.sort(key=lambda item: (item["entity_id"], item["alias"], item["entity_name"]))
    with alias_csv_path.open(mode="w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in merged_rows:
            writer.writerow(row)

    return {
        "patch_rows": patch_rows,
        "appended": appended_rows,
        "total_rows": len(merged_rows),
    }


def main(args: Sequence[str]) -> int:
    parser = build_parser()
    parsed = parser.parse_args(list(args[1:]))
    nodes_csv_path = resolve_csv_path(parsed.nodes_csv)
    relationships_csv_path = resolve_csv_path(parsed.relationships_csv)
    database = str(parsed.database).strip() or DEFAULT_DB
    source_tag = str(parsed.source_tag).strip() or DEFAULT_SOURCE_TAG
    graph_version = str(parsed.graph_version).strip() or DEFAULT_GRAPH_VERSION
    missing_report_path = resolve_csv_path(parsed.missing_report)
    node_baseline_csv_path = resolve_csv_path(parsed.node_baseline_csv)
    event_aliases_csv_path = resolve_csv_path(parsed.event_aliases_csv)

    if str(parsed.event_alias_patch_csv or "").strip():
        event_alias_patch_csv_path = resolve_csv_path(parsed.event_alias_patch_csv)
    else:
        event_alias_patch_csv_path = relationships_csv_path.parent / "event_aliases_patch.csv"

    importer = CaseGraphImporter(
        DEFAULT_URI,
        DEFAULT_USER,
        DEFAULT_PASSWORD,
        database,
        source_tag=source_tag,
        graph_version=graph_version,
    )
    baseline_sync_result: Dict[str, int] | None = None
    alias_merge_result: Dict[str, int] | None = None
    try:
        result = importer.import_data(
            nodes_csv_path=nodes_csv_path,
            relationships_csv_path=relationships_csv_path,
            skip_nodes=bool(parsed.skip_nodes),
            dry_run=bool(parsed.dry_run),
            fail_on_missing_endpoints=not bool(parsed.allow_missing_endpoints),
            missing_report_path=missing_report_path,
        )
        if not bool(parsed.dry_run) and not bool(parsed.skip_refresh_node_baseline):
            baseline_sync_result = importer.export_node_baseline_csv(node_baseline_csv_path)
        if not bool(parsed.dry_run) and not bool(parsed.skip_merge_event_aliases):
            alias_merge_result = merge_event_alias_patch(event_aliases_csv_path, event_alias_patch_csv_path)
    finally:
        importer.close()

    mode_text = "预检查完成（dry-run）" if result.get("dry_run") else "案例图谱导入完成"
    print(f"{mode_text}，数据库: {database}")
    print(f"节点 CSV: {nodes_csv_path}")
    print(f"关系 CSV: {relationships_csv_path}")
    print(f"CSV 节点行数: {result['nodes_csv_rows']}")
    print(f"CSV 关系行数: {result['relationships_csv_rows']}")
    print(f"source_tag: {source_tag}")
    print(f"graph_version: {graph_version}")
    print(f"缺失端点关系数: {result['missing_endpoint_rows']}")
    if result.get("missing_endpoint_report"):
        print(f"缺失端点报告: {result['missing_endpoint_report']}")
    print(f"处理节点数: {result['imported_nodes']}")
    print(f"处理关系数: {result['imported_relationships']}")
    print(f"新建节点数: {result['created_nodes']}")
    print(f"新建关系数: {result['created_relationships']}")
    if baseline_sync_result is not None:
        print(f"节点基线已同步: {node_baseline_csv_path}")
        print(f"节点基线行数: {baseline_sync_result.get('row_count', 0)}")
    if alias_merge_result is not None:
        print(f"别名补丁来源: {event_alias_patch_csv_path}")
        print(f"别名总表已同步: {event_aliases_csv_path}")
        print(f"别名补丁行数: {alias_merge_result.get('patch_rows', 0)}")
        print(f"新增别名行数: {alias_merge_result.get('appended', 0)}")
        print(f"别名总表行数: {alias_merge_result.get('total_rows', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
