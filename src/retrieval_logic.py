import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Sequence

from neo4j import GraphDatabase

from contracts import (
    ChromaEvidence,
    ExtractedEntities,
    IncidentInput,
    Neo4jConstraint,
    RetrievalContext,
)
from entity_aliases import EventAliasStore
from vectorDB import ChromaDBVectorStore, PRODUCTION_COLLECTION_NAME


DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "trafficv2")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

logger = logging.getLogger(__name__)

EVENT_ALIAS_CSV_PATH = Path(__file__).resolve().parents[1] / "data_clean" / "event_aliases.csv"


def _is_enabled(env_name: str) -> bool:
    return str(os.getenv(env_name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _short_text(value: object, limit: int = 240) -> str:
    text = str(value or "").strip().replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _debug_log(event: str, **payload: object) -> None:
    if not _is_enabled("TRAFFIC_DEBUG"):
        return
    logger.info("%s | %s", event, json.dumps(payload, ensure_ascii=False, default=str))


def _char_ngrams(text: str, size: int = 2) -> set[str]:
    cleaned = re.sub(r"\s+", "", str(text or "").strip())
    if not cleaned:
        return set()
    if len(cleaned) <= size:
        return {cleaned}
    return {cleaned[index:index + size] for index in range(len(cleaned) - size + 1)}


def _jaccard_similarity(left: str, right: str) -> float:
    left_ngrams = _char_ngrams(left)
    right_ngrams = _char_ngrams(right)
    if not left_ngrams or not right_ngrams:
        return 0.0
    union = left_ngrams | right_ngrams
    if not union:
        return 0.0
    return len(left_ngrams & right_ngrams) / len(union)


class DualRetrievalService:
    """统一封装 Neo4j 图谱检索与 Chroma 佐证检索。"""

    SEVERITY_TO_LEVEL_NAME = {
        "特别重大": "Ⅰ级（特别重大）",
        "重大": "Ⅱ级（重大）",
        "较大": "Ⅲ级（较大）",
        "一般": "Ⅳ级（一般）",
    }

    def __init__(
        self,
        neo4j_uri: str = DEFAULT_NEO4J_URI,
        neo4j_user: str = DEFAULT_NEO4J_USER,
        neo4j_password: str = DEFAULT_NEO4J_PASSWORD,
        neo4j_database: str = DEFAULT_NEO4J_DATABASE,
        chroma_db_path: str = "./chroma_data",
        chroma_collection_name: str = PRODUCTION_COLLECTION_NAME,
    ):
        self.neo4j_database = neo4j_database
        self.driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        self.vector_store = ChromaDBVectorStore(
            db_path=chroma_db_path,
            collection_name=chroma_collection_name,
        )
        self.alias_store = EventAliasStore(EVENT_ALIAS_CSV_PATH)

    def close(self) -> None:
        self.driver.close()

    @staticmethod
    def _append_unique(items: List[str], value: str) -> None:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in items:
            items.append(cleaned)

    @staticmethod
    def _limit_items(values: Sequence[str], limit: int) -> List[str]:
        result: List[str] = []
        for value in values:
            cleaned = str(value or "").strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
            if len(result) >= limit:
                break
        return result

    def _select_relevant_aliases(
        self,
        aliases: Sequence[str],
        reference_texts: Sequence[str],
        limit: int,
    ) -> List[str]:
        scored_aliases: List[tuple[float, str]] = []
        normalized_references = [str(text or "").strip() for text in reference_texts if str(text or "").strip()]
        for alias in aliases:
            cleaned_alias = str(alias or "").strip()
            if not cleaned_alias:
                continue
            similarity = max((_jaccard_similarity(cleaned_alias, reference) for reference in normalized_references), default=0.0)
            literal_bonus = 0.0
            if any(cleaned_alias in reference or reference in cleaned_alias for reference in normalized_references):
                literal_bonus = 0.2
            scored_aliases.append((similarity + literal_bonus, cleaned_alias))

        scored_aliases.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
        return [alias for _, alias in scored_aliases[:limit]]

    def _collect_core_scene_terms(self, entities: ExtractedEntities) -> List[str]:
        scene_terms: List[str] = []
        if entities.weather:
            self._append_unique(scene_terms, entities.weather)

        for value in self._limit_items(entities.hazards, 3):
            self._append_unique(scene_terms, value)
        for value in self._limit_items(entities.vehicles, 2):
            self._append_unique(scene_terms, value)
        for value in self._limit_items(entities.location_features, 2):
            self._append_unique(scene_terms, value)
        for value in self._limit_items(entities.evidence_from_image, 2):
            self._append_unique(scene_terms, value)

        return scene_terms

    def build_query_terms(self, incident: IncidentInput, entities: ExtractedEntities) -> List[str]:
        terms: List[str] = []

        self._append_unique(terms, entities.incident_type_raw)
        self._append_unique(terms, entities.incident_type)

        scene_terms = self._collect_core_scene_terms(entities)
        for value in scene_terms:
            self._append_unique(terms, value)

        reference_texts = [
            entities.incident_type_raw,
            entities.incident_type,
            incident.raw_text,
            *scene_terms,
        ]

        if entities.matched_events:
            for index, matched_event in enumerate(entities.matched_events[:2]):
                self._append_unique(terms, matched_event.normalized_name)
                alias_limit = 2 if index == 0 else 1
                aliases = self.alias_store.get_aliases(matched_event.node_id, matched_event.normalized_name)
                for alias in self._select_relevant_aliases(aliases, reference_texts, alias_limit):
                    self._append_unique(terms, alias)
        elif entities.incident_type:
            aliases = self.alias_store.get_aliases(entity_name=entities.incident_type)
            for alias in self._select_relevant_aliases(aliases, reference_texts, 2):
                self._append_unique(terms, alias)

        if entities.severity != 'UNKNOWN':
            self._append_unique(terms, entities.severity)

        return terms[:12]

    def _build_evidence_query(
        self,
        incident: IncidentInput,
        entities: ExtractedEntities,
        graph_context: Dict[str, List[Dict]],
        severity: str,
        query_terms: Sequence[str],
    ) -> str:
        parts: List[str] = []

        self._append_unique(parts, incident.raw_text)
        for term in list(query_terms)[:8]:
            self._append_unique(parts, term)

        for item in graph_context.get('warnings', [])[:1]:
            self._append_unique(parts, item.get('name', ''))

        for item in graph_context.get('responses', [])[:1]:
            self._append_unique(parts, item.get('name', ''))

        for item in graph_context.get('actions', [])[:3]:
            self._append_unique(parts, item.get('action_name', ''))

        if severity != 'UNKNOWN':
            self._append_unique(parts, severity)

        return "；".join(parts)

    def _match_events(self, terms: Sequence[str]) -> List[Dict]:
        if not terms:
            return []

        query = """
        MATCH (e:Event)
        WHERE any(term IN $terms WHERE e.name CONTAINS term OR term CONTAINS e.name)
        RETURN DISTINCT e.id AS id, e.name AS name
        ORDER BY e.name
        """
        with self.driver.session(database=self.neo4j_database) as session:
            return session.run(query, terms=list(terms)).data()

    def _expand_events(self, event_ids: Sequence[str]) -> List[Dict]:
        if not event_ids:
            return []

        query = """
        MATCH (e:Event)
        WHERE e.id IN $event_ids
        OPTIONAL MATCH (e)-[:CAUSES]->(target:Event)
        RETURN DISTINCT coalesce(target.id, e.id) AS id, coalesce(target.name, e.name) AS name
        ORDER BY name
        """
        with self.driver.session(database=self.neo4j_database) as session:
            return session.run(query, event_ids=list(event_ids)).data()

    def _fetch_graph_context(self, event_ids: Sequence[str]) -> Dict[str, List[Dict]]:
        return self._fetch_graph_context_for_severity(event_ids, "")

    def _fetch_graph_context_for_severity(self, event_ids: Sequence[str], severity: str) -> Dict[str, List[Dict]]:
        if not event_ids:
            return {
                'warnings': [],
                'responses': [],
                'actions': [],
                'implemented_by': [],
                'resources': [],
            }

        level_name = self.SEVERITY_TO_LEVEL_NAME.get((severity or "").strip(), "")

        with self.driver.session(database=self.neo4j_database) as session:
            warnings = []
            responses = []
            if level_name:
                warnings = session.run(
                    """
                    MATCH (level:EventLevel)-[:TRIGGERS]->(warning:Warning)
                    WHERE level.name = $level_name
                    RETURN DISTINCT warning.name AS name
                    ORDER BY name
                    """,
                    level_name=level_name,
                ).data()

                responses = session.run(
                    """
                    MATCH (level:EventLevel)-[:TRIGGERS]->(response:Response)
                    WHERE level.name = $level_name
                    RETURN DISTINCT response.name AS name
                    ORDER BY name
                    """,
                    level_name=level_name,
                ).data()

            actions = session.run(
                """
                MATCH (source)-[:TRIGGERS]->(action:Action)
                WHERE source.id IN $event_ids OR source.name IN $event_names
                RETURN DISTINCT source.name AS source_name, action.name AS action_name
                ORDER BY source_name, action_name
                """,
                event_ids=list(event_ids),
                event_names=[item['name'] for item in self._expand_events(event_ids)],
            ).data()

            action_names = [item['action_name'] for item in actions]
            response_names = [item['name'] for item in responses]
            warning_names = [item['name'] for item in warnings]
            source_names = list(dict.fromkeys(action_names + response_names + warning_names))

            implemented_by = []
            resources = []
            if source_names:
                implemented_by = session.run(
                    """
                    MATCH (source)-[:IMPLEMENTED_BY]->(department:Department)
                    WHERE source.name IN $source_names
                    RETURN DISTINCT source.name AS source_name, department.name AS department_name
                    ORDER BY source_name, department_name
                    """,
                    source_names=source_names,
                ).data()

            if action_names:
                resources = session.run(
                    """
                    MATCH (action:Action)-[:REQUIRES]->(resource:Resource)
                    WHERE action.name IN $action_names
                    RETURN DISTINCT action.name AS action_name, resource.name AS resource_name
                    ORDER BY action_name, resource_name
                    """,
                    action_names=action_names,
                ).data()

        return {
            'warnings': warnings,
            'responses': responses,
            'actions': actions,
            'implemented_by': implemented_by,
            'resources': resources,
        }

    def _build_constraints(self, matched_events: Sequence[Dict], graph_context: Dict[str, List[Dict]]) -> List[Neo4jConstraint]:
        constraints: List[Neo4jConstraint] = []

        for item in graph_context.get('warnings', []):
            constraints.append(
                Neo4jConstraint(
                    rule=f"触发预警：{item['name']}",
                    source_node='EventLevel',
                    relation='TRIGGERS',
                    target_node=item['name'],
                )
            )

        for item in graph_context.get('responses', []):
            constraints.append(
                Neo4jConstraint(
                    rule=f"触发响应：{item['name']}",
                    source_node='EventLevel',
                    relation='TRIGGERS',
                    target_node=item['name'],
                )
            )

        for item in graph_context.get('actions', []):
            constraints.append(
                Neo4jConstraint(
                    rule=f"推荐动作：{item['action_name']}",
                    source_node=item['source_name'],
                    relation='TRIGGERS',
                    target_node=item['action_name'],
                )
            )

        for item in graph_context.get('implemented_by', []):
            constraints.append(
                Neo4jConstraint(
                    rule=f"实施主体：{item['department_name']}",
                    source_node=item['source_name'],
                    relation='IMPLEMENTED_BY',
                    target_node=item['department_name'],
                )
            )

        for item in graph_context.get('resources', []):
            constraints.append(
                Neo4jConstraint(
                    rule=f"调用资源：{item['resource_name']}",
                    source_node=item['action_name'],
                    relation='REQUIRES',
                    target_node=item['resource_name'],
                )
            )

        return constraints

    def retrieve(self, incident: IncidentInput, entities: ExtractedEntities) -> RetrievalContext:
        query_terms = self.build_query_terms(incident, entities)
        matched_events = [
            {"id": item.node_id, "name": item.normalized_name}
            for item in entities.matched_events
            if item.node_id and item.normalized_name
        ]
        if not matched_events:
            matched_events = self._match_events(query_terms)
        expanded_events = self._expand_events([item['id'] for item in matched_events])
        event_ids = [item['id'] for item in expanded_events] or [item['id'] for item in matched_events]

        severity_info = {
            'severity': entities.severity,
            'source': 'AGENT1' if entities.severity != 'UNKNOWN' else 'NONE',
        }
        graph_context = self._fetch_graph_context_for_severity(event_ids, severity_info['severity'])
        constraints = self._build_constraints(matched_events or expanded_events, graph_context)
        evidence_query = self._build_evidence_query(
            incident=incident,
            entities=entities,
            graph_context=graph_context,
            severity=severity_info['severity'],
            query_terms=query_terms,
        )

        evidence_results = self.vector_store.search_evidence(
            query_text=evidence_query,
            n_results=5,
            accident_type=entities.incident_type or None,
            weather=entities.weather or None,
            severity=None if severity_info['severity'] == 'UNKNOWN' else severity_info['severity'],
        )

        if not evidence_results:
            evidence_results = self.vector_store.search_evidence(query_text=incident.raw_text, n_results=5)

        evidences = [
            ChromaEvidence(
                content=item['content'],
                file_name=item['file_name'],
                chunk_id=str(item['chunk_id']),
                distance=float(item['distance']),
            )
            for item in evidence_results
        ]

        _debug_log(
            "retrieval_result",
            incident_type=entities.incident_type,
            incident_type_raw=entities.incident_type_raw,
            query_term_count=len(query_terms),
            matched_event_count=len(matched_events),
            expanded_event_count=len(expanded_events),
            constraint_count=len(constraints),
            evidence_count=len(evidences),
            severity=severity_info['severity'],
            severity_source=severity_info['source'],
            evidence_query_preview=_short_text(evidence_query, limit=500),
        )

        return RetrievalContext(
            neo4j_constraints=constraints,
            chroma_evidence=evidences,
            severity=severity_info['severity'],
            severity_source=severity_info['source'],
        )
