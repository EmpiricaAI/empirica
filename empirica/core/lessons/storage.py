"""
Empirica Lessons - Multi-Layer Storage Manager

Coordinates 4 storage layers for epistemic procedural knowledge:
- HOT: In-memory graph (nanoseconds) - relationships, deltas
- WARM: SQLite (microseconds) - metadata, queryable
- SEARCH: Qdrant (milliseconds) - semantic similarity
- COLD: YAML files (10ms) - full lesson content

This module is the single entry point for all lesson storage operations.
"""

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

import yaml

from .hot_cache import HotLessonEntry, get_hot_cache
from .schema import (
    Lesson,
)

logger = logging.getLogger(__name__)


def _format_lesson_node(lesson: dict) -> str:
    """Format a lesson with confidence icon and name."""
    conf = lesson.get("confidence", 0)
    conf_icon = "●" if conf >= 0.85 else "◐" if conf >= 0.70 else "○"
    return f"{conf_icon} {lesson['name']} [{conf:.2f}]"


def _print_lesson_tree(lessons, lesson_id, lines, printed, prefix="", is_last=True, rel_label=None):
    """Recursively print lesson tree with ASCII formatting."""
    if lesson_id in printed:
        return
    printed.add(lesson_id)

    lesson = lessons.get(lesson_id)
    if not lesson:
        return

    connector = "└── " if is_last else "├── "
    if rel_label:
        lines.append(f"{prefix}{connector}[{rel_label}] {_format_lesson_node(lesson)}")
    else:
        lines.append(f"{prefix}{_format_lesson_node(lesson)}")

    children = []
    for e in lesson.get("enables", []):
        children.append((e["id"], "enables"))
    for p in lesson.get("prerequisite_for", []):
        children.append((p["id"], "prereq"))
    for imp in lesson.get("improves", []):
        children.append((imp["id"], "improves"))

    children = [(cid, rel) for cid, rel in children if cid not in printed]

    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, (child_id, rel) in enumerate(children):
        child_is_last = i == len(children) - 1
        _print_lesson_tree(lessons, child_id, lines, printed, child_prefix, child_is_last, rel)


def _print_related_connections(lessons, lines):
    """Print bidirectional related connections between lessons."""
    lines.append("")
    lines.append("RELATED CONNECTIONS:")
    related_printed = set()
    for lid, lesson in lessons.items():
        for rel in lesson.get("related", []):
            pair = tuple(sorted([lid, rel["id"]]))
            if pair not in related_printed:
                related_printed.add(pair)
                l1 = lessons[lid]["name"].replace("NotebookLM: ", "")
                l2 = lessons[rel["id"]]["name"].replace("NotebookLM: ", "")
                lines.append(f"  {l1} <--related--> {l2}")


class LessonStorageManager:
    """
    Multi-layer storage manager for lessons.

    Provides a unified API for storing and retrieving lessons across
    all 4 layers, with automatic layer selection for optimal performance.
    """

    def __init__(self, db_conn=None, cold_storage_path: Path | None = None, qdrant_client=None):
        """
        Initialize the storage manager.

        Args:
            db_conn: SQLite connection (WARM layer)
            cold_storage_path: Path to YAML storage directory (COLD layer)
            qdrant_client: Qdrant client for vector search (SEARCH layer)
        """
        # WARM layer - SQLite
        if db_conn is None:
            from empirica.data.session_database import SessionDatabase

            self._db = SessionDatabase()
            self._conn = self._db.adapter.conn
        else:
            self._conn = db_conn
            self._db = None

        # COLD layer - YAML files
        if cold_storage_path is None:
            cold_storage_path = Path(".empirica/lessons")
        self._cold_path = Path(cold_storage_path)
        self._cold_path.mkdir(parents=True, exist_ok=True)

        # HOT layer - In-memory
        self._hot = get_hot_cache()

        # SEARCH layer - Qdrant (optional)
        self._qdrant = qdrant_client
        self._qdrant_collection = "empirica_lessons"
        self._ensure_qdrant_collection()

        # Initialize hot cache from warm storage
        self._load_hot_cache()

    def _ensure_qdrant_collection(self):
        """Ensure the lessons collection exists in Qdrant"""
        if not self._qdrant:
            return
        try:
            from qdrant_client.models import Distance, VectorParams

            from empirica.core.qdrant.embeddings import get_vector_size

            # Check if collection exists
            collections = self._qdrant.get_collections().collections
            exists = any(c.name == self._qdrant_collection for c in collections)
            if not exists:
                # Use vector size from embeddings provider (auto-detects model)
                vector_size = get_vector_size()
                self._qdrant.create_collection(
                    collection_name=self._qdrant_collection,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
                logger.info(f"Created Qdrant collection: {self._qdrant_collection} (dim={vector_size})")
        except Exception as e:
            logger.warning(f"Could not ensure Qdrant collection: {e}")

    def _load_hot_cache(self) -> int:
        """Load all lessons into hot cache from warm storage for fast access."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT l.id, l.name, l.domain,
                   GROUP_CONCAT(DISTINCT led.vector_name || ':' || led.delta_value) as deltas,
                   GROUP_CONCAT(DISTINCT lp.prereq_id) as prereqs
            FROM lessons l
            LEFT JOIN lesson_epistemic_deltas led ON l.id = led.lesson_id
            LEFT JOIN lesson_prerequisites lp ON l.id = lp.lesson_id AND lp.prereq_type = 'lesson'
            GROUP BY l.id
        """)

        count = 0
        for row in cursor.fetchall():
            lesson_id, name, domain, deltas_str, prereqs_str = row

            # Parse deltas
            expected_delta = {}
            if deltas_str:
                for d in deltas_str.split(","):
                    if ":" in d:
                        k, v = d.split(":")
                        expected_delta[k] = float(v)

            # Parse prereqs
            prereq_ids = prereqs_str.split(",") if prereqs_str else []

            # Get relations from knowledge_graph
            cursor.execute(
                """
                SELECT target_id, relation_type FROM knowledge_graph
                WHERE source_type = 'lesson' AND source_id = ?
                AND target_type = 'lesson'
            """,
                (lesson_id,),
            )

            enables = []
            requires = []
            for target_id, rel_type in cursor.fetchall():
                if rel_type == "enables":
                    enables.append(target_id)
                elif rel_type == "requires":
                    requires.append(target_id)

            hot_dict = {
                "id": lesson_id,
                "name": name,
                "domain": domain,
                "expected_delta": expected_delta,
                "prereq_ids": prereq_ids,
                "enables": enables,
                "requires": requires,
            }

            self._hot.load_lesson(hot_dict)
            count += 1

        logger.info(f"Loaded {count} lessons into hot cache")
        return count

    # ==================== CREATE ====================

    def create_lesson(self, lesson: Lesson) -> dict:
        """
        Create a new lesson across all layers.

        Returns dict with status and IDs.
        """
        start = time.time()

        # 1. COLD layer - YAML file (full content)
        cold_path = self._write_cold(lesson)

        # 2. WARM layer - SQLite (metadata)
        self._write_warm(lesson)

        # 3. HOT layer - In-memory (relationships)
        self._hot.load_lesson(lesson.to_hot_dict())

        # 4. SEARCH layer - Qdrant (vectors, optional)
        qdrant_id = None
        if self._qdrant:
            qdrant_id = self._write_search(lesson)

        # 5. Knowledge graph edges
        self._write_knowledge_graph_edges(lesson)

        elapsed = (time.time() - start) * 1000

        return {
            "ok": True,
            "lesson_id": lesson.id,
            "cold_path": str(cold_path),
            "qdrant_id": qdrant_id,
            "elapsed_ms": elapsed,
        }

    def _write_cold(self, lesson: Lesson) -> Path:
        """Write full lesson to YAML file"""
        path = self._cold_path / f"{lesson.id}.yaml"
        with open(path, "w") as f:
            yaml.dump(lesson.to_dict(), f, default_flow_style=False, sort_keys=False)
        logger.debug(f"COLD: Wrote {path}")
        return path

    def _write_warm(self, lesson: Lesson):
        """Write lesson metadata to SQLite"""
        cursor = self._conn.cursor()

        # Main lesson record
        warm = lesson.to_warm_dict()
        cursor.execute(
            """
            INSERT OR REPLACE INTO lessons
            (id, name, version, description, domain, tags,
             source_confidence, teaching_quality, reproducibility,
             step_count, prereq_count, replay_count, success_rate,
             suggested_tier, suggested_price, created_by,
             created_timestamp, updated_timestamp, lesson_data,
             abstraction_level, sharing_policy, abstract_pattern,
             parent_lesson_id, entity_ids, project_id, org_id, user_id,
             trigger_type, trigger_config, output_format, output_renderer,
             output_config, execution_count, feedback_score,
             last_executed, last_feedback)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                warm["id"],
                warm["name"],
                warm["version"],
                warm["description"],
                warm["domain"],
                warm["tags"],
                warm["source_confidence"],
                warm["teaching_quality"],
                warm["reproducibility"],
                warm["step_count"],
                warm["prereq_count"],
                warm["replay_count"],
                warm["success_rate"],
                warm["suggested_tier"],
                warm["suggested_price"],
                warm["created_by"],
                warm["created_timestamp"],
                warm["updated_timestamp"],
                json.dumps(lesson.to_dict()),  # Full JSON for reference
                lesson.abstraction_level,
                lesson.sharing_policy,
                lesson.abstract_pattern,
                lesson.parent_lesson_id,
                json.dumps(lesson.entity_ids) if lesson.entity_ids else None,
                lesson.project_id,
                lesson.org_id,
                lesson.user_id,
                lesson.trigger_type,
                json.dumps(lesson.trigger_config) if lesson.trigger_config else None,
                lesson.output_format,
                lesson.output_renderer,
                json.dumps(lesson.output_config) if lesson.output_config else None,
                lesson.execution_count,
                lesson.feedback_score,
                lesson.last_executed,
                lesson.last_feedback,
            ),
        )

        # Steps
        for step in lesson.steps:
            step_id = f"{lesson.id}:step:{step.order}"
            cursor.execute(
                """
                INSERT OR REPLACE INTO lesson_steps
                (id, lesson_id, step_order, phase, action, target, code,
                 critical, expected_outcome, error_recovery, timeout_ms,
                 query_pattern, cache_tier, requires_auth)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    step_id,
                    lesson.id,
                    step.order,
                    step.phase.value,
                    step.action,
                    step.target,
                    step.code,
                    step.critical,
                    step.expected_outcome,
                    step.error_recovery,
                    step.timeout_ms,
                    json.dumps(step.query_pattern) if hasattr(step, "query_pattern") and step.query_pattern else None,
                    getattr(step, "cache_tier", None),
                    getattr(step, "requires_auth", None),
                ),
            )

        # Epistemic deltas
        delta = lesson.epistemic.expected_delta
        for vec_name, value in delta.to_dict().items():
            if value != 0:
                delta_id = f"{lesson.id}:delta:{vec_name}"
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO lesson_epistemic_deltas
                    (id, lesson_id, vector_name, delta_value)
                    VALUES (?, ?, ?, ?)
                """,
                    (delta_id, lesson.id, vec_name, value),
                )

        # Prerequisites
        for prereq in lesson.prerequisites:
            prereq_id = f"{lesson.id}:prereq:{prereq.id}"
            cursor.execute(
                """
                INSERT OR REPLACE INTO lesson_prerequisites
                (id, lesson_id, prereq_type, prereq_id, prereq_name, required_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (prereq_id, lesson.id, prereq.type.value, prereq.id, prereq.name, prereq.required_level),
            )

        # Corrections
        for correction in lesson.corrections:
            corr_id = f"{lesson.id}:corr:{correction.step_order}:{int(correction.timestamp)}"
            cursor.execute(
                """
                INSERT OR REPLACE INTO lesson_corrections
                (id, lesson_id, step_order, original_action, corrected_action,
                 reason, corrector_type, corrector_id, created_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    corr_id,
                    lesson.id,
                    correction.step_order,
                    correction.original_action,
                    correction.corrected_action,
                    correction.reason,
                    correction.corrector_type,
                    correction.corrector_id,
                    correction.timestamp,
                ),
            )

        self._conn.commit()
        logger.debug(f"WARM: Wrote lesson {lesson.id} to SQLite")

    def _write_search(self, lesson: Lesson) -> str | None:
        """Write lesson to Qdrant for semantic search"""
        try:
            from qdrant_client.models import PointStruct

            # Generate embedding from lesson content
            embedding_text = f"{lesson.name} {lesson.description} {' '.join(lesson.tags)}"
            vector = self._generate_embedding(embedding_text)

            point_id = hashlib.md5(lesson.id.encode()).hexdigest()

            self._qdrant.upsert(
                collection_name=self._qdrant_collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "lesson_id": lesson.id,
                            "name": lesson.name,
                            "description": lesson.description,
                            "domain": lesson.domain,
                            "tags": lesson.tags,
                            "source_confidence": lesson.epistemic.source_confidence,
                            "teaching_quality": lesson.epistemic.teaching_quality,
                        },
                    )
                ],
            )
            logger.debug(f"SEARCH: Wrote lesson {lesson.id} to Qdrant")
            return point_id
        except Exception as e:
            logger.warning(f"SEARCH: Failed to write to Qdrant: {e}")
            return None

    def _write_knowledge_graph_edges(self, lesson: Lesson):
        """Write lesson relationships to knowledge graph"""
        cursor = self._conn.cursor()
        now = time.time()

        for rel in lesson.relations:
            edge_id = f"{lesson.id}:{rel.relation_type.value}:{rel.target_id}"
            cursor.execute(
                """
                INSERT OR REPLACE INTO knowledge_graph
                (id, source_type, source_id, relation_type, target_type, target_id,
                 weight, created_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    edge_id,
                    "lesson",
                    lesson.id,
                    rel.relation_type.value,
                    rel.target_type,
                    rel.target_id,
                    rel.weight,
                    now,
                ),
            )

        self._conn.commit()

    def _generate_embedding(self, text: str) -> list[float]:
        """Generate embedding vector for text using the core embeddings provider"""
        from empirica.core.qdrant.embeddings import get_embedding

        return get_embedding(text)

    # ==================== READ ====================

    def get_lesson(self, lesson_id: str, layer: str = "auto") -> Lesson | HotLessonEntry | None:
        """
        Get lesson by ID.

        Args:
            lesson_id: Lesson ID
            layer: Which layer to read from ('hot', 'warm', 'cold', 'auto')

        Returns:
            Lesson object or None
        """
        if layer == "auto":
            # Try hot first (fastest)
            hot_entry = self._hot.get_lesson(lesson_id)
            if hot_entry:
                # Hot cache hit - but we need full data
                # Load from cold for complete lesson
                return self._read_cold(lesson_id)
            else:
                # Try warm
                return self._read_warm(lesson_id)

        elif layer == "hot":
            return self._hot.get_lesson(lesson_id)
        elif layer == "warm":
            return self._read_warm(lesson_id)
        elif layer == "cold":
            return self._read_cold(lesson_id)
        else:
            raise ValueError(f"Unknown layer: {layer}")

    def _read_cold(self, lesson_id: str) -> Lesson | None:
        """Read full lesson from YAML file"""
        path = self._cold_path / f"{lesson_id}.yaml"
        if not path.exists():
            return None

        with open(path) as f:
            data = yaml.safe_load(f)

        return Lesson.from_dict(data)

    def _read_warm(self, lesson_id: str) -> Lesson | None:
        """Read lesson from SQLite (may have less detail than cold)"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT lesson_data FROM lessons WHERE id = ?
        """,
            (lesson_id,),
        )

        row = cursor.fetchone()
        if not row:
            return None

        data = json.loads(row[0])
        return Lesson.from_dict(data)

    # ==================== SEARCH ====================

    def search_lessons(
        self, query: str | None = None, domain: str | None = None, improves_vector: str | None = None, limit: int = 10
    ) -> list[dict]:
        """
        Search for lessons across layers.

        Args:
            query: Semantic search query (uses Qdrant)
            domain: Filter by domain (uses hot/warm)
            improves_vector: Find lessons that improve this vector (uses hot)
            limit: Max results

        Returns:
            List of lesson summaries
        """
        results = []

        def _governance(lesson) -> dict:
            """The fields that decide whether a lesson leaves the practice.

            `sharing_policy` is the whole distinction between a lesson and a
            finding, so it has to be visible on the surface a practitioner would
            use to CHECK it. It was absent from every projection here, which
            meant the only way to confirm a lesson was actually shared was to
            open the YAML by hand — and a verification that requires bypassing
            the serving surface does not get done.
            """
            return {
                "sharing_policy": getattr(lesson, "sharing_policy", None),
                "abstraction_level": getattr(lesson, "abstraction_level", None),
                "abstract_pattern": getattr(lesson, "abstract_pattern", None),
            }

        # Vector-based search
        if improves_vector:
            lesson_ids = self._hot.lessons_that_improve(improves_vector, threshold=0.1, limit=limit)
            for lid in lesson_ids:
                lesson = self.get_lesson(lid)
                if lesson:
                    epistemic = getattr(lesson, "epistemic", None)
                    delta_val = epistemic.expected_delta.to_dict().get(improves_vector, 0) if epistemic else 0
                    results.append(
                        {
                            "id": lesson.id,
                            "name": lesson.name,
                            "description": getattr(lesson, "description", ""),
                            "improves": improves_vector,
                            "delta": delta_val,
                            **_governance(lesson),
                        }
                    )

        # Domain filter
        elif domain:
            lesson_ids = self._hot.find_by_domain(domain)
            for lid in list(lesson_ids)[:limit]:
                lesson = self.get_lesson(lid)
                if lesson:
                    results.append(
                        {
                            "id": lesson.id,
                            "name": lesson.name,
                            "description": getattr(lesson, "description", ""),
                            "domain": domain,
                            **_governance(lesson),
                        }
                    )

        # Semantic search
        elif query and self._qdrant:
            vector = self._generate_embedding(query)
            try:
                # Use query_points API (Qdrant 1.7+)
                response = self._qdrant.query_points(collection_name=self._qdrant_collection, query=vector, limit=limit)
                for point in response.points:
                    results.append(
                        {
                            "id": point.payload.get("lesson_id"),
                            "name": point.payload.get("name"),
                            "description": point.payload.get("description"),
                            "score": point.score,
                        }
                    )
            except Exception as e:
                logger.warning(f"Qdrant search failed: {e}")

        # Fallback - list all from warm
        else:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, name, description, domain,
                       sharing_policy, abstraction_level, abstract_pattern
                FROM lessons
                ORDER BY created_timestamp DESC
                LIMIT ?
            """,
                (limit,),
            )
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "domain": row[3],
                        "sharing_policy": row[4],
                        "abstraction_level": row[5],
                        "abstract_pattern": row[6],
                    }
                )

        return results

    # ==================== LEARNING PATH ====================

    def get_learning_path(self, target_lesson_id: str, completed_lessons: set[str] | None = None) -> list[str]:
        """
        Get ordered list of lessons to complete before target.

        Uses hot cache for fast graph traversal.
        """
        if completed_lessons is None:
            completed_lessons = set()

        return self._hot.get_learning_path(target_lesson_id, completed_lessons)

    def find_best_lesson_for_gap(self, epistemic_state: dict[str, float], threshold: float = 0.6) -> list[dict]:
        """
        Find lessons that address epistemic gaps.

        Returns list of (lesson_id, vector, improvement) sorted by impact.
        """
        gaps = self._hot.find_best_for_gap(epistemic_state, threshold)
        results = []
        for lesson_id, vector, improvement in gaps:
            lesson = self.get_lesson(lesson_id)
            if lesson:
                results.append(
                    {
                        "lesson_id": lesson_id,
                        "name": lesson.name,
                        "addresses": vector,
                        "expected_improvement": improvement,
                        "description": getattr(lesson, "description", ""),
                    }
                )
        return results

    # ==================== REPLAY TRACKING ====================

    def start_replay(
        self,
        lesson_id: str,
        session_id: str,
        ai_id: str | None = None,
        epistemic_before: dict[str, float] | None = None,
    ) -> str:
        """Start tracking a lesson replay"""
        cursor = self._conn.cursor()
        replay_id = str(uuid.uuid4())

        lesson = self.get_lesson(lesson_id)
        total_steps = len(getattr(lesson, "steps", [])) if lesson else 0

        cursor.execute(
            """
            INSERT INTO lesson_replays
            (id, lesson_id, session_id, ai_id, started_timestamp,
             total_steps, epistemic_before)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                replay_id,
                lesson_id,
                session_id,
                ai_id,
                time.time(),
                total_steps,
                json.dumps(epistemic_before) if epistemic_before else None,
            ),
        )
        self._conn.commit()
        return replay_id

    def complete_replay(
        self,
        replay_id: str,
        success: bool,
        steps_completed: int,
        epistemic_after: dict[str, float] | None = None,
        error_message: str | None = None,
    ):
        """Mark a lesson replay as complete"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE lesson_replays
            SET completed_timestamp = ?,
                success = ?,
                steps_completed = ?,
                epistemic_after = ?,
                error_message = ?
            WHERE id = ?
        """,
            (
                time.time(),
                success,
                steps_completed,
                json.dumps(epistemic_after) if epistemic_after else None,
                error_message,
                replay_id,
            ),
        )

        # Update lesson stats if successful
        if success:
            cursor.execute(
                """
                UPDATE lessons
                SET replay_count = replay_count + 1,
                    success_rate = (
                        SELECT CAST(SUM(CASE WHEN success THEN 1 ELSE 0 END) AS REAL) / COUNT(*)
                        FROM lesson_replays
                        WHERE lesson_id = (SELECT lesson_id FROM lesson_replays WHERE id = ?)
                    )
                WHERE id = (SELECT lesson_id FROM lesson_replays WHERE id = ?)
            """,
                (replay_id, replay_id),
            )

        self._conn.commit()

    # ==================== IMMUNE SYSTEM: CONFIDENCE DECAY ====================

    def decay_related_lessons(
        self,
        finding_text: str,
        domain: str | None = None,
        decay_amount: float = 0.05,
        min_confidence: float = 0.3,
        keywords_threshold: int = 2,
    ) -> list[dict]:
        """
        Decay confidence of lessons related to a finding.

        When a finding is logged that corrects or supersedes lesson content,
        the related lessons' confidence should decay. This implements the
        "immune system" pattern for knowledge health.

        CENTRAL TOLERANCE: If domain is provided, only decay lessons in that
        domain. This prevents "autoimmune" attacks where findings about one
        topic incorrectly decay lessons about unrelated topics.

        Args:
            finding_text: The finding content
            domain: Optional domain to scope decay (central tolerance)
            decay_amount: How much to decay confidence (default 0.05)
            min_confidence: Minimum confidence floor (default 0.3)
            keywords_threshold: Minimum keyword matches to trigger decay

        Returns:
            List of affected lessons with decay info
        """
        import re

        # Extract keywords from finding (simple tokenization)
        # Remove common words and keep significant terms
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "shall",
            "can",
            "need",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "under",
            "again",
            "further",
            "then",
            "once",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "each",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "nor",
            "not",
            "only",
            "own",
            "same",
            "so",
            "than",
            "too",
            "very",
            "just",
            "and",
            "but",
            "or",
            "if",
            "because",
            "until",
            "while",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "use",
            "using",
            "used",
        }

        # Tokenize and filter
        words = re.findall(r"\b[a-zA-Z]{3,}\b", finding_text.lower())
        keywords = [w for w in words if w not in stop_words]

        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        if not unique_keywords:
            return []

        affected_lessons = []

        # Read all lessons from YAML cold storage (source of truth)
        for yaml_file in self._cold_path.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)

                if not data:
                    continue

                lesson_domain = data.get("domain", "")

                # CENTRAL TOLERANCE: Skip if domain filter doesn't match
                if domain and lesson_domain.lower() != domain.lower():
                    continue

                name = data.get("name", "")
                description = data.get("description", "")
                tags = " ".join(data.get("tags", []))
                epistemic = data.get("epistemic", {})
                current_conf = epistemic.get("source_confidence", 1.0)

                # Count keyword matches for this lesson
                lesson_text = f"{name} {description} {tags}".lower()
                match_count = sum(1 for kw in unique_keywords if kw in lesson_text)

                # Only decay if enough keywords match
                if match_count >= keywords_threshold:
                    # Calculate new confidence (with floor)
                    new_conf = max(min_confidence, current_conf - decay_amount)

                    if new_conf < current_conf:
                        # Update the confidence in YAML
                        data["epistemic"]["source_confidence"] = new_conf
                        data["updated_timestamp"] = time.time()

                        # Write back to YAML (atomic write)
                        temp_path = yaml_file.with_suffix(".yaml.tmp")
                        with open(temp_path, "w") as f:
                            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
                        temp_path.replace(yaml_file)

                        lesson_id = yaml_file.stem
                        affected_lessons.append(
                            {
                                "lesson_id": lesson_id,
                                "name": name,
                                "previous_confidence": current_conf,
                                "new_confidence": new_conf,
                                "decay_amount": current_conf - new_conf,
                                "matched_keywords": match_count,
                                "reason": "finding_supersedes",
                            }
                        )

                        logger.info(
                            f"IMMUNE: Decayed lesson '{name}' confidence "
                            f"{current_conf:.2f} → {new_conf:.2f} "
                            f"({match_count} keyword matches)"
                        )

            except Exception as e:
                logger.warning(f"IMMUNE: Failed to process {yaml_file}: {e}")
                continue

        return affected_lessons

    # ==================== KNOWLEDGE GRAPH: LESSON RELATIONSHIPS ====================

    def add_edge(
        self, source_id: str, target_id: str, relation_type: str, weight: float = 1.0, metadata: dict | None = None
    ) -> str | None:
        """
        Create an edge between two lessons in the knowledge graph.

        Relation types:
        - 'enables': source must be done before target can be effective
        - 'prerequisite': source is required before target
        - 'related': lessons cover similar/overlapping topics
        - 'supersedes': source replaces/updates target
        - 'improves': source enhances effectiveness of target

        Args:
            source_id: Source lesson ID
            target_id: Target lesson ID
            relation_type: Type of relationship
            weight: Strength of relationship (0.0-1.0)
            metadata: Optional additional data

        Returns:
            Edge ID if created, None if failed
        """
        import hashlib
        import json

        # Generate deterministic edge ID
        edge_id = hashlib.md5(f"{source_id}:{relation_type}:{target_id}".encode()).hexdigest()[:16]

        cursor = self._conn.cursor()

        # Check if edge already exists (use unique constraint columns)
        cursor.execute(
            """
            SELECT id FROM knowledge_graph
            WHERE source_type = 'lesson' AND source_id = ?
            AND relation_type = ?
            AND target_type = 'lesson' AND target_id = ?
        """,
            (source_id, relation_type, target_id),
        )
        existing = cursor.fetchone()
        if existing:
            # Update weight if edge exists
            cursor.execute(
                """
                UPDATE knowledge_graph
                SET weight = ?, metadata = ?
                WHERE id = ?
            """,
                (weight, json.dumps(metadata) if metadata else None, existing[0]),
            )
            self._conn.commit()
            logger.debug(f"Updated edge {existing[0]}: {relation_type}")
            return existing[0]

        # Create new edge
        cursor.execute(
            """
            INSERT INTO knowledge_graph
            (id, source_type, source_id, relation_type, target_type, target_id, weight, created_timestamp, metadata)
            VALUES (?, 'lesson', ?, ?, 'lesson', ?, ?, ?, ?)
        """,
            (
                edge_id,
                source_id,
                relation_type,
                target_id,
                weight,
                time.time(),
                json.dumps(metadata) if metadata else None,
            ),
        )

        self._conn.commit()
        logger.info(f"Created edge {edge_id}: {source_id} --{relation_type}--> {target_id}")
        return edge_id

    def get_lesson_map(self, domain: str | None = None) -> dict:
        """
        Get orthogonal view of lessons organized by relationships.

        Returns a structured map showing:
        - Entry points (lessons with no prerequisites)
        - Dependency chains
        - Related clusters

        Args:
            domain: Optional domain filter

        Returns:
            Dict with lesson map structure
        """
        cursor = self._conn.cursor()

        # Get all lessons (optionally filtered by domain)
        if domain:
            cursor.execute(
                """
                SELECT id, name, source_confidence, domain
                FROM lessons WHERE LOWER(domain) = ?
            """,
                (domain.lower(),),
            )
        else:
            cursor.execute("SELECT id, name, source_confidence, domain FROM lessons")

        lessons = {
            row[0]: {
                "id": row[0],
                "name": row[1],
                "confidence": row[2],
                "domain": row[3],
                "enables": [],
                "enabled_by": [],
                "prerequisites": [],
                "prerequisite_for": [],
                "related": [],
                "supersedes": [],
                "superseded_by": [],
                "improves": [],
                "improved_by": [],
            }
            for row in cursor.fetchall()
        }

        if not lessons:
            return {"lessons": {}, "entry_points": [], "clusters": []}

        # Get all edges for these lessons
        lesson_ids = list(lessons.keys())
        placeholders = ",".join("?" * len(lesson_ids))

        cursor.execute(
            f"""
            SELECT source_id, relation_type, target_id, weight
            FROM knowledge_graph
            WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
        """,
            lesson_ids + lesson_ids,
        )

        # Build relationship maps
        for source_id, relation_type, target_id, weight in cursor.fetchall():
            if source_id in lessons and target_id in lessons:
                if relation_type == "enables":
                    lessons[source_id]["enables"].append({"id": target_id, "weight": weight})
                    lessons[target_id]["enabled_by"].append({"id": source_id, "weight": weight})
                elif relation_type == "prerequisite":
                    lessons[source_id]["prerequisite_for"].append({"id": target_id, "weight": weight})
                    lessons[target_id]["prerequisites"].append({"id": source_id, "weight": weight})
                elif relation_type == "related":
                    lessons[source_id]["related"].append({"id": target_id, "weight": weight})
                    lessons[target_id]["related"].append({"id": source_id, "weight": weight})
                elif relation_type == "supersedes":
                    lessons[source_id]["supersedes"].append({"id": target_id, "weight": weight})
                    lessons[target_id]["superseded_by"].append({"id": source_id, "weight": weight})
                elif relation_type == "improves":
                    lessons[source_id]["improves"].append({"id": target_id, "weight": weight})
                    lessons[target_id]["improved_by"].append({"id": source_id, "weight": weight})

        # Find entry points (lessons with no prerequisites or enabled_by)
        entry_points = [lid for lid, data in lessons.items() if not data["prerequisites"] and not data["enabled_by"]]

        # Build dependency chains from entry points
        def get_chain(lesson_id, visited=None):
            """Recursively build dependency chain from a lesson."""
            if visited is None:
                visited = set()
            if lesson_id in visited:
                return []
            visited.add(lesson_id)
            lesson = lessons.get(lesson_id)
            if not lesson:
                return []
            chain = [lesson_id]
            for enabled in lesson["enables"] + lesson["prerequisite_for"]:
                chain.extend(get_chain(enabled["id"], visited))
            return chain

        chains = [get_chain(ep) for ep in entry_points]

        return {
            "lessons": lessons,
            "entry_points": entry_points,
            "chains": chains,
            "edge_count": sum(
                len(l["enables"])
                + len(l["prerequisites"])
                + len(l["related"])
                + len(l["supersedes"])
                + len(l["improves"])
                for l in lessons.values()
            )
            // 2,  # Divide by 2 to avoid double counting
        }

    def print_lesson_map(self, domain: str | None = None) -> str:
        """
        Print a visual ASCII representation of the lesson map.

        Args:
            domain: Optional domain filter

        Returns:
            ASCII art representation
        """
        lesson_map = self.get_lesson_map(domain)
        lessons = lesson_map["lessons"]
        entry_points = lesson_map["entry_points"]

        if not lessons:
            return "No lessons found."

        lines = []
        lines.append(f"=== LESSON MAP {'(' + domain + ')' if domain else ''} ===")
        lines.append(f"Lessons: {len(lessons)} | Edges: {lesson_map['edge_count']}")
        lines.append("")

        printed = set()

        lines.append("WORKFLOW STRUCTURE:")
        for i, ep in enumerate(entry_points):
            is_last_ep = i == len(entry_points) - 1
            _print_lesson_tree(lessons, ep, lines, printed, "", is_last_ep)
        lines.append("")

        # Print orphans (no relationships)
        orphans = [lid for lid in lessons if lid not in printed]
        if orphans:
            lines.append("STANDALONE LESSONS:")
            for lid in orphans:
                lines.append(f"  {_format_lesson_node(lessons[lid])}")

        # Print related connections (bidirectional)
        _print_related_connections(lessons, lines)

        return "\n".join(lines)

    # ==================== STATS ====================

    def stats(self) -> dict:
        """Get storage statistics across all layers (hot, warm, cold, search)."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM lessons")
        lesson_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM knowledge_graph")
        edge_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM lesson_replays WHERE success = 1")
        successful_replays = cursor.fetchone()[0]

        hot_stats = self._hot.stats()

        return {
            "warm": {"lesson_count": lesson_count, "edge_count": edge_count, "successful_replays": successful_replays},
            "hot": hot_stats,
            "cold": {"path": str(self._cold_path), "file_count": len(list(self._cold_path.glob("*.yaml")))},
            "search": {
                "enabled": self._qdrant is not None,
                "collection": self._qdrant_collection if self._qdrant else None,
            },
        }


# Singleton instance
_storage: LessonStorageManager | None = None


def _try_get_qdrant_client():
    """Try to get a Qdrant client, return None if unavailable.

    Requires a running Qdrant server. File-based storage was removed (#45)
    because it creates incompatible storage formats and CWD-relative paths.
    """
    try:
        import os

        from qdrant_client import QdrantClient

        # Priority 1: Explicit URL
        url = os.getenv("EMPIRICA_QDRANT_URL")
        if url:
            return QdrantClient(url=url)

        # Priority 2: Check if Qdrant server is running on localhost
        default_url = "http://localhost:6333"
        try:
            import urllib.request

            req = urllib.request.Request(f"{default_url}/collections", method="GET")
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    return QdrantClient(url=default_url)
        except Exception:  # noqa: S110 — Qdrant server may not be running; graceful fallback
            pass

        logger.debug("Qdrant server not available for lessons storage")
        return None
    except ImportError:
        logger.debug("qdrant-client not installed, SEARCH layer disabled")
        return None
    except Exception as e:
        logger.debug(f"Could not connect to Qdrant: {e}")
        return None


def get_lesson_storage() -> LessonStorageManager:
    """Get or create the global lesson storage manager"""
    global _storage
    if _storage is None:
        qdrant = _try_get_qdrant_client()
        _storage = LessonStorageManager(qdrant_client=qdrant)
    return _storage
