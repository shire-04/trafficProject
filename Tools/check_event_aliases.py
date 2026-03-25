from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_FILE = PROJECT_ROOT / "data_clean" / "neo4j_import" / "国家交通应急预案_neo4j_nodes.csv"
ALIAS_FILE = PROJECT_ROOT / "data_clean" / "event_aliases.csv"


def load_event_nodes() -> dict[str, str]:
    event_nodes: dict[str, str] = {}
    with NODE_FILE.open("r", encoding="utf-8-sig", errors="ignore") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("entity_type_cn") == "突发事件" and "Event" in str(row.get(":LABEL", "")):
                event_nodes[str(row.get("id:ID", "")).strip()] = str(row.get("name", "")).strip()
    return event_nodes


def load_alias_coverage() -> dict[str, set[str]]:
    alias_map: dict[str, set[str]] = {}
    with ALIAS_FILE.open("r", encoding="utf-8-sig", errors="ignore") as file:
        reader = csv.DictReader(file)
        for row in reader:
            entity_id = str(row.get("entity_id", "")).strip()
            alias = str(row.get("alias", "")).strip()
            if not entity_id or not alias:
                continue
            alias_map.setdefault(entity_id, set()).add(alias)
    return alias_map


def main() -> None:
    event_nodes = load_event_nodes()
    alias_map = load_alias_coverage()

    missing = [
        {"entity_id": entity_id, "entity_name": entity_name}
        for entity_id, entity_name in event_nodes.items()
        if entity_id not in alias_map
    ]

    alias_counts = {
        entity_id: len(alias_map.get(entity_id, set()))
        for entity_id in event_nodes
    }

    min_alias_count = min(alias_counts.values()) if alias_counts else 0
    max_alias_count = max(alias_counts.values()) if alias_counts else 0

    print({
        "event_count": len(event_nodes),
        "covered_event_count": sum(1 for entity_id in event_nodes if entity_id in alias_map),
        "missing_event_count": len(missing),
        "min_alias_count": min_alias_count,
        "max_alias_count": max_alias_count,
        "missing": missing,
    })


if __name__ == "__main__":
    main()
