import csv
from pathlib import Path


EVENT_ALIAS_CSV_PATH = Path(__file__).resolve().parents[1] / "data_clean" / "event_aliases.csv"
EVENT_RELATIONSHIP_CSV_PATH = Path(__file__).resolve().parents[1] / "data_clean" / "neo4j_import" / "国家交通应急预案_neo4j_relationships.csv"


class EventAliasStore:
    """Event 节点别名存储，用于实体匹配和检索查询扩展。"""

    def __init__(self, alias_csv_path: Path | None = None, relationship_csv_path: Path | None = None):
        self.alias_csv_path = alias_csv_path or EVENT_ALIAS_CSV_PATH
        self.relationship_csv_path = relationship_csv_path or EVENT_RELATIONSHIP_CSV_PATH
        self.aliases_by_id: dict[str, list[str]] = {}
        self.aliases_by_name: dict[str, list[str]] = {}
        self.entities_by_id: dict[str, dict[str, str]] = {}
        self.parent_ids_by_id: dict[str, list[str]] = {}
        self.child_ids_by_id: dict[str, list[str]] = {}
        self._load()
        self._load_relationships()

    def _load(self) -> None:
        if not self.alias_csv_path.exists():
            return

        with self.alias_csv_path.open("r", encoding="utf-8-sig", errors="ignore") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                entity_id = str(row.get("entity_id", "")).strip()
                entity_name = str(row.get("entity_name", "")).strip()
                entity_type = str(row.get("entity_type", "")).strip()
                alias = str(row.get("alias", "")).strip()
                if not entity_id or not entity_name or not alias:
                    continue

                self.entities_by_id.setdefault(
                    entity_id,
                    {
                        "node_id": entity_id,
                        "name": entity_name,
                        "entity_type": entity_type or "Event",
                    },
                )

                if alias not in self.aliases_by_id.setdefault(entity_id, []):
                    self.aliases_by_id[entity_id].append(alias)

                if alias not in self.aliases_by_name.setdefault(entity_name, []):
                    self.aliases_by_name[entity_name].append(alias)

    def get_aliases(self, entity_id: str = "", entity_name: str = "") -> list[str]:
        result: list[str] = []
        for alias in self.aliases_by_id.get(str(entity_id or "").strip(), []):
            if alias not in result:
                result.append(alias)
        for alias in self.aliases_by_name.get(str(entity_name or "").strip(), []):
            if alias not in result:
                result.append(alias)
        return result

    def _load_relationships(self) -> None:
        if not self.relationship_csv_path.exists():
            return

        with self.relationship_csv_path.open("r", encoding="utf-8-sig", errors="ignore") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                relation_type = str(row.get(":TYPE", "")).strip()
                start_id = str(row.get(":START_ID", "")).strip()
                end_id = str(row.get(":END_ID", "")).strip()
                if relation_type != "CAUSES" or not start_id or not end_id:
                    continue
                if not start_id.startswith("EVT_") or not end_id.startswith("EVT_"):
                    continue

                if end_id not in self.parent_ids_by_id.setdefault(start_id, []):
                    self.parent_ids_by_id[start_id].append(end_id)
                if start_id not in self.child_ids_by_id.setdefault(end_id, []):
                    self.child_ids_by_id[end_id].append(start_id)

    def get_parent_ids(self, entity_id: str) -> list[str]:
        return list(self.parent_ids_by_id.get(str(entity_id or "").strip(), []))

    def get_child_ids(self, entity_id: str) -> list[str]:
        return list(self.child_ids_by_id.get(str(entity_id or "").strip(), []))

    def get_ancestor_ids(self, entity_id: str) -> list[str]:
        start_id = str(entity_id or "").strip()
        if not start_id:
            return []

        result: list[str] = []
        stack = list(self.get_parent_ids(start_id))
        while stack:
            current_id = stack.pop()
            if current_id in result:
                continue
            result.append(current_id)
            stack.extend(self.get_parent_ids(current_id))
        return result

    def is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        normalized_ancestor = str(ancestor_id or "").strip()
        normalized_descendant = str(descendant_id or "").strip()
        if not normalized_ancestor or not normalized_descendant or normalized_ancestor == normalized_descendant:
            return False
        return normalized_ancestor in self.get_ancestor_ids(normalized_descendant)

    def get_hierarchy_depth(self, entity_id: str) -> int:
        normalized_id = str(entity_id or "").strip()
        if not normalized_id:
            return 0

        parents = self.get_parent_ids(normalized_id)
        if not parents:
            return 0
        return 1 + max((self.get_hierarchy_depth(parent_id) for parent_id in parents), default=0)

    def build_matcher_index(self) -> list[dict]:
        items: list[dict] = []
        for entity_id, entity_info in self.entities_by_id.items():
            items.append(
                {
                    "node_id": entity_id,
                    "name": entity_info.get("name", ""),
                    "entity_type": entity_info.get("entity_type", "Event") or "Event",
                    "aliases": self.get_aliases(entity_id=entity_id, entity_name=entity_info.get("name", "")),
                    "parent_ids": self.get_parent_ids(entity_id),
                    "child_ids": self.get_child_ids(entity_id),
                }
            )
        items.sort(key=lambda item: item.get("name", ""))
        return items
