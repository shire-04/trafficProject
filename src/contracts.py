from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class CasualtyEstimate:
    """伤亡估计，unknown 表示信息缺失。"""

    deaths: Optional[int] = None
    injuries: Optional[int] = None
    missing: Optional[int] = None
    unknown: bool = True


@dataclass
class MatchedNode:
    """实体规范化匹配结果。"""

    surface_form: str = ""
    entity_type: str = ""
    normalized_name: str = ""
    node_id: str = ""
    match_confidence: float = 0.0
    match_reason: str = ""


@dataclass
class ExtractedEntities:
    """Dispatcher Agent 提取的结构化实体。"""

    incident_type_raw: str = ""
    incident_type: str = ""
    matched_events: List[MatchedNode] = field(default_factory=list)
    severity: str = "UNKNOWN"
    severity_reason: str = ""
    severity_confidence: float = 0.0
    difficulty: str = "UNKNOWN"
    difficulty_reason: str = ""
    difficulty_confidence: float = 0.0
    weather: str = ""
    hazards: List[str] = field(default_factory=list)
    vehicles: List[str] = field(default_factory=list)
    location_features: List[str] = field(default_factory=list)
    casualty_estimate: CasualtyEstimate = field(default_factory=CasualtyEstimate)
    evidence_from_image: List[str] = field(default_factory=list)
    extract_confidence: float = 0.0


@dataclass
class IncidentInput:
    """原始接警输入。"""

    raw_text: str
    image_bytes: Optional[bytes] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Neo4jConstraint:
    """图谱返回的硬约束。"""

    rule: str
    source_node: str = ""
    relation: str = ""
    target_node: str = ""


@dataclass
class ChromaEvidence:
    """向量检索证据。"""

    content: str
    file_name: str
    chunk_id: str
    distance: float


@dataclass
class RetrievalContext:
    """Retrieval & Logic Agent 输出上下文。"""

    neo4j_constraints: List[Neo4jConstraint] = field(default_factory=list)
    chroma_evidence: List[ChromaEvidence] = field(default_factory=list)
    severity: str = "UNKNOWN"
    severity_source: str = "NONE"


@dataclass
class StrategyDraft:
    """Commander 单方案草案。"""

    focus: str
    steps: List[str] = field(default_factory=list)
    required_resources: List[str] = field(default_factory=list)
    legal_references: List[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    """Evaluator 审查结果。"""

    status: str = "REJECTED"
    reason: str = ""
    violated_constraints: List[str] = field(default_factory=list)
    missing_actions: List[str] = field(default_factory=list)
    risk_notes: List[str] = field(default_factory=list)
    retry_count: int = 0
    failure_type: str = ""
    executability_score: float = 0.0
    safety_score: float = 0.0
    compliance_score: float = 0.0
    overall_score: float = 0.0
    score_threshold: float = 0.0
    score_delta: float = 0.0
    ineffective_revision_count: int = 0


@dataclass
class RoutingDecision:
    """Auto 模式的路由决策信息。"""

    requested_mode: str = ""
    effective_mode: str = ""
    route_target: str = ""
    difficulty: str = "UNKNOWN"
    reason: str = ""
    confidence: float = 0.0
    used_llm: bool = False
    fallback_to_g5: bool = False
    fallback_reason: str = ""
    rule_hit_count: int = 0
    rule_hits: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    """编排器最终输出。"""

    incident: IncidentInput
    entities: ExtractedEntities
    context: RetrievalContext
    draft: StrategyDraft
    review: ReviewResult
    final_strategy: str
    initial_draft: Optional[StrategyDraft] = None
    routing: Optional[RoutingDecision] = None
    human_handoff: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
