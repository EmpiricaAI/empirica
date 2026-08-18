"""
Empirica Lessons - Epistemic Procedural Knowledge Schema

This module defines the data structures for storing and retrieving
procedural knowledge with epistemic metadata. Lessons capture not just
HOW to do something, but the epistemic state changes that result.

Architecture:
- HOT layer: In-memory graph (nanoseconds)
- WARM layer: SQLite metadata (microseconds)
- SEARCH layer: Qdrant vectors (milliseconds)
- COLD layer: YAML full content (10ms)
"""

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class LessonPhase(Enum):
    """Epistemic phase of a lesson step"""

    NOETIC = "noetic"  # Investigation, reading, understanding
    PRAXIC = "praxic"  # Action, execution, doing


class StepCriticality(Enum):
    """How critical is getting this step right"""

    CRITICAL = "critical"  # Failure here = lesson fails
    IMPORTANT = "important"  # Should get right, recoverable
    OPTIONAL = "optional"  # Nice to have


class PrerequisiteType(Enum):
    """Types of prerequisites a lesson can have"""

    LESSON = "lesson"  # Must have completed another lesson
    SKILL = "skill"  # Must have a skill (composite of lessons)
    TOOL = "tool"  # Must have access to a tool
    CONTEXT = "context"  # Must have certain context (file, repo, etc.)
    EPISTEMIC = "epistemic"  # Must have epistemic state (know >= X)


class RelationType(Enum):
    """Types of relationships between lessons"""

    REQUIRES = "requires"  # Must do X before Y
    ENABLES = "enables"  # Doing X unlocks Y
    RELATED_TO = "related_to"  # Conceptually similar
    SUPERSEDES = "supersedes"  # X is newer version of Y
    DERIVED_FROM = "derived_from"  # X was created from Y


@dataclass
class EpistemicDelta:
    """
    Expected change in epistemic vectors from completing a lesson.
    This is the KEY insight - lessons don't just teach procedures,
    they predictably improve specific epistemic dimensions.
    """

    know: float = 0.0  # Domain knowledge improvement
    do: float = 0.0  # Capability improvement
    context: float = 0.0  # Situational understanding
    clarity: float = 0.0  # Task clarity
    coherence: float = 0.0  # Mental model coherence
    signal: float = 0.0  # Signal/noise discrimination
    uncertainty: float = 0.0  # Uncertainty reduction (negative = good)

    def to_dict(self) -> dict[str, float]:
        """Convert delta to dictionary representation."""
        return {
            "know": self.know,
            "do": self.do,
            "context": self.context,
            "clarity": self.clarity,
            "coherence": self.coherence,
            "signal": self.signal,
            "uncertainty": self.uncertainty,
        }

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> "EpistemicDelta":
        """Create delta from dictionary representation."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class LessonStep:
    """
    A single step in a procedural lesson.
    Each step has epistemic phase (noetic vs praxic) and criticality.
    """

    order: int
    phase: LessonPhase
    action: str  # Human-readable description
    target: str | None = None  # UI element, file, command target
    code: str | None = None  # Executable code (JS, bash, etc.)
    critical: bool = False  # If True, must succeed
    expected_outcome: str | None = None
    error_recovery: str | None = None  # What to do if step fails
    timeout_ms: int | None = None  # Max time for this step

    # Cortex cache integration (migration 037)
    query_pattern: dict | None = None  # Qdrant query spec for this step's data
    cache_tier: str | None = None  # frozen|cold|search|warm|hot
    requires_auth: str | None = None  # what API keys/auth this step needs

    def to_dict(self) -> dict:
        """Convert step to dictionary representation."""
        return {
            "order": self.order,
            "phase": self.phase.value,
            "action": self.action,
            "target": self.target,
            "code": self.code,
            "critical": self.critical,
            "expected_outcome": self.expected_outcome,
            "error_recovery": self.error_recovery,
            "timeout_ms": self.timeout_ms,
            "query_pattern": self.query_pattern,
            "cache_tier": self.cache_tier,
            "requires_auth": self.requires_auth,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LessonStep":
        """Create step from dictionary representation."""
        return cls(
            order=d["order"],
            phase=LessonPhase(d["phase"]),
            action=d["action"],
            target=d.get("target"),
            code=d.get("code"),
            critical=d.get("critical", False),
            expected_outcome=d.get("expected_outcome"),
            error_recovery=d.get("error_recovery"),
            timeout_ms=d.get("timeout_ms"),
            query_pattern=d.get("query_pattern"),
            cache_tier=d.get("cache_tier"),
            requires_auth=d.get("requires_auth"),
        )


@dataclass
class Prerequisite:
    """A prerequisite for executing a lesson"""

    type: PrerequisiteType
    id: str  # ID of required item
    name: str  # Human-readable name
    required_level: float = 0.5  # Minimum level needed (0-1)

    def to_dict(self) -> dict:
        """Convert prerequisite to dictionary representation."""
        return {"type": self.type.value, "id": self.id, "name": self.name, "required_level": self.required_level}

    @classmethod
    def from_dict(cls, d: dict) -> "Prerequisite":
        """Create prerequisite from dictionary representation."""
        return cls(
            type=PrerequisiteType(d["type"]), id=d["id"], name=d["name"], required_level=d.get("required_level", 0.5)
        )


@dataclass
class Correction:
    """
    A correction received during lesson creation or replay.
    Corrections are HIGH VALUE - they represent human expertise
    fixing AI mistakes.
    """

    step_order: int
    original_action: str
    corrected_action: str
    reason: str
    corrector_type: Literal["human", "ai"]
    corrector_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Convert correction to dictionary representation."""
        return {
            "step_order": self.step_order,
            "original_action": self.original_action,
            "corrected_action": self.corrected_action,
            "reason": self.reason,
            "corrector_type": self.corrector_type,
            "corrector_id": self.corrector_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Correction":
        """Create correction from dictionary representation."""
        return cls(**d)


@dataclass
class LessonRelation:
    """A relationship between this lesson and another entity"""

    relation_type: RelationType
    target_type: str  # 'lesson', 'skill', 'domain'
    target_id: str
    weight: float = 1.0  # Relationship strength

    def to_dict(self) -> dict:
        """Convert relation to dictionary representation."""
        return {
            "relation_type": self.relation_type.value,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LessonRelation":
        """Create relation from dictionary representation."""
        return cls(
            relation_type=RelationType(d["relation_type"]),
            target_type=d["target_type"],
            target_id=d["target_id"],
            weight=d.get("weight", 1.0),
        )


@dataclass
class LessonValidation:
    """Validation and quality metrics for a lesson"""

    replay_count: int = 0  # Times successfully replayed
    success_rate: float = 0.0  # Success rate (0-1)
    avg_completion_time_ms: int = 0  # Average time to complete
    test_cases: list[str] = field(default_factory=list)
    success_criteria: str = ""
    last_validated: float | None = None

    def to_dict(self) -> dict:
        """Convert validation to dictionary representation."""
        return {
            "replay_count": self.replay_count,
            "success_rate": self.success_rate,
            "avg_completion_time_ms": self.avg_completion_time_ms,
            "test_cases": self.test_cases,
            "success_criteria": self.success_criteria,
            "last_validated": self.last_validated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LessonValidation":
        """Create validation from dictionary representation."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class LessonEpistemic:
    """Epistemic metadata about the lesson itself"""

    source_confidence: float  # How confident was the teacher
    teaching_quality: float  # How clear is the lesson
    reproducibility: float  # How reliably can it be replayed
    expected_delta: EpistemicDelta  # What vectors it improves

    def to_dict(self) -> dict:
        """Convert epistemic metadata to dictionary representation."""
        return {
            "source_confidence": self.source_confidence,
            "teaching_quality": self.teaching_quality,
            "reproducibility": self.reproducibility,
            "expected_delta": self.expected_delta.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LessonEpistemic":
        """Create epistemic metadata from dictionary representation."""
        return cls(
            source_confidence=d["source_confidence"],
            teaching_quality=d["teaching_quality"],
            reproducibility=d["reproducibility"],
            expected_delta=EpistemicDelta.from_dict(d["expected_delta"]),
        )


@dataclass
class Lesson:
    """
    Complete lesson representation.

    A lesson is epistemic procedural knowledge - it captures:
    1. WHAT to do (steps)
    2. WHY it works (epistemic delta)
    3. WHEN to use it (prerequisites)
    4. HOW it relates (knowledge graph relations)
    5. HOW WELL it works (validation metrics)
    """

    id: str
    name: str
    version: str
    description: str

    # Epistemic metadata
    epistemic: LessonEpistemic

    # Prerequisites
    prerequisites: list[Prerequisite] = field(default_factory=list)

    # Procedural steps
    steps: list[LessonStep] = field(default_factory=list)

    # Relations in knowledge graph
    relations: list[LessonRelation] = field(default_factory=list)

    # Corrections received
    corrections: list[Correction] = field(default_factory=list)

    # Validation metrics
    validation: LessonValidation = field(default_factory=LessonValidation)

    # Pricing tier (for marketplace)
    suggested_tier: Literal["free", "verified", "pro", "enterprise"] = "free"
    suggested_price: float = 0.0

    # Metadata
    created_by: str | None = None  # AI or human ID
    created_timestamp: float = field(default_factory=time.time)
    updated_timestamp: float = field(default_factory=time.time)

    # Tags for search
    tags: list[str] = field(default_factory=list)
    domain: str | None = None  # e.g., 'browser-automation', 'git', 'api'

    # ── Composable epistemic patterns (migration 037) ──

    # Abstraction and sharing
    abstraction_level: Literal["personal", "project", "domain", "cross_org"] = "personal"
    sharing_policy: Literal["private", "project", "org", "public", "licensed"] = "private"
    abstract_pattern: str | None = None  # canonical pattern name for cross-cutting
    parent_lesson_id: str | None = None  # lesson this was abstracted from

    # EKG connections
    entity_ids: list[str] = field(default_factory=list)  # connected entity IDs
    project_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None

    # Trigger model
    trigger_type: str | None = None  # schedule|state_change|event|manual|suggestion
    trigger_config: dict | None = None  # schedule spec, vector thresholds, etc.

    # Output rendering
    output_format: str = "markdown"  # markdown|html|infographic|slides|audio|video
    output_renderer: str = "template"  # template|llm|notebooklm|google_workspace
    output_config: dict | None = None  # renderer-specific config

    # Feedback loop
    execution_count: int = 0
    feedback_score: float = 0.0
    last_executed: float | None = None
    last_feedback: float | None = None

    @staticmethod
    def generate_id(name: str, version: str) -> str:
        """Generate deterministic lesson ID from name and version"""
        content = f"{name}:{version}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Full serialization for COLD storage (YAML)"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "epistemic": self.epistemic.to_dict(),
            "prerequisites": [p.to_dict() for p in self.prerequisites],
            "steps": [s.to_dict() for s in self.steps],
            "relations": [r.to_dict() for r in self.relations],
            "corrections": [c.to_dict() for c in self.corrections],
            "validation": self.validation.to_dict(),
            "suggested_tier": self.suggested_tier,
            "suggested_price": self.suggested_price,
            "created_by": self.created_by,
            "created_timestamp": self.created_timestamp,
            "updated_timestamp": self.updated_timestamp,
            "tags": self.tags,
            "domain": self.domain,
            # Composable fields
            "abstraction_level": self.abstraction_level,
            "sharing_policy": self.sharing_policy,
            "abstract_pattern": self.abstract_pattern,
            "parent_lesson_id": self.parent_lesson_id,
            "entity_ids": self.entity_ids,
            "project_id": self.project_id,
            "org_id": self.org_id,
            "user_id": self.user_id,
            "trigger_type": self.trigger_type,
            "trigger_config": self.trigger_config,
            "output_format": self.output_format,
            "output_renderer": self.output_renderer,
            "output_config": self.output_config,
            "execution_count": self.execution_count,
            "feedback_score": self.feedback_score,
            "last_executed": self.last_executed,
            "last_feedback": self.last_feedback,
        }

    def to_hot_dict(self) -> dict:
        """Minimal serialization for HOT cache (in-memory)"""
        return {
            "id": self.id,
            "name": self.name,
            "expected_delta": self.epistemic.expected_delta.to_dict(),
            "prereq_ids": [p.id for p in self.prerequisites if p.type == PrerequisiteType.LESSON],
            "enables": [r.target_id for r in self.relations if r.relation_type == RelationType.ENABLES],
            "requires": [r.target_id for r in self.relations if r.relation_type == RelationType.REQUIRES],
        }

    def to_warm_dict(self) -> dict:
        """Metadata for WARM storage (SQLite)"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "domain": self.domain,
            "tags": ",".join(self.tags),
            "source_confidence": self.epistemic.source_confidence,
            "teaching_quality": self.epistemic.teaching_quality,
            "reproducibility": self.epistemic.reproducibility,
            "step_count": len(self.steps),
            "prereq_count": len(self.prerequisites),
            "replay_count": self.validation.replay_count,
            "success_rate": self.validation.success_rate,
            "suggested_tier": self.suggested_tier,
            "suggested_price": self.suggested_price,
            "created_by": self.created_by,
            "created_timestamp": self.created_timestamp,
            "updated_timestamp": self.updated_timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        """Deserialize from COLD storage.

        Every field `to_dict` writes must be read back here. The migration-037
        block (sharing/abstraction, EKG links, trigger, output, feedback) was
        written to YAML and never deserialized, so a lesson published at
        `sharing_policy='org'` was reconstructed at the dataclass default
        `'private'` — silently, and on the surface that decides propagation. It
        did not fail to share loudly; it reported itself unshared. Verified
        2026-08-18: both storage layers held `org`/`cross_org` while
        `lesson-load` and `lesson-search` returned `private`/`personal`.

        Round-trip is pinned by a test — the sibling of this defect is a field
        added to `to_dict` and forgotten here, which looks like nothing at all.
        """
        return cls(
            id=d["id"],
            name=d["name"],
            version=d["version"],
            description=d["description"],
            epistemic=LessonEpistemic.from_dict(d["epistemic"]),
            prerequisites=[Prerequisite.from_dict(p) for p in d.get("prerequisites", [])],
            steps=[LessonStep.from_dict(s) for s in d.get("steps", [])],
            relations=[LessonRelation.from_dict(r) for r in d.get("relations", [])],
            corrections=[Correction.from_dict(c) for c in d.get("corrections", [])],
            validation=LessonValidation.from_dict(d.get("validation", {})),
            suggested_tier=d.get("suggested_tier", "free"),
            suggested_price=d.get("suggested_price", 0.0),
            created_by=d.get("created_by"),
            created_timestamp=d.get("created_timestamp", time.time()),
            updated_timestamp=d.get("updated_timestamp", time.time()),
            tags=d.get("tags", []),
            domain=d.get("domain"),
            # ── migration 037 ── governance first: these decide propagation.
            abstraction_level=d.get("abstraction_level", "personal"),
            sharing_policy=d.get("sharing_policy", "private"),
            abstract_pattern=d.get("abstract_pattern"),
            parent_lesson_id=d.get("parent_lesson_id"),
            entity_ids=d.get("entity_ids", []),
            project_id=d.get("project_id"),
            org_id=d.get("org_id"),
            user_id=d.get("user_id"),
            trigger_type=d.get("trigger_type"),
            trigger_config=d.get("trigger_config"),
            output_format=d.get("output_format", "markdown"),
            output_renderer=d.get("output_renderer", "template"),
            output_config=d.get("output_config"),
            execution_count=d.get("execution_count", 0),
            feedback_score=d.get("feedback_score", 0.0),
            last_executed=d.get("last_executed"),
            last_feedback=d.get("last_feedback"),
        )


# Knowledge Graph Node types for type safety
@dataclass
class KnowledgeGraphNode:
    """A node in the epistemic procedural knowledge graph"""

    id: str
    node_type: Literal["lesson", "skill", "domain", "tool", "agent"]
    name: str
    epistemic_delta: EpistemicDelta | None = None

    def to_dict(self) -> dict:
        """Convert knowledge graph node to dictionary representation."""
        return {
            "id": self.id,
            "node_type": self.node_type,
            "name": self.name,
            "epistemic_delta": self.epistemic_delta.to_dict() if self.epistemic_delta else None,
        }


@dataclass
class KnowledgeGraphEdge:
    """An edge in the epistemic procedural knowledge graph"""

    source_id: str
    target_id: str
    relation_type: RelationType
    weight: float = 1.0

    def to_dict(self) -> dict:
        """Convert knowledge graph edge to dictionary representation."""
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type.value,
            "weight": self.weight,
        }
