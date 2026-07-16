# Lessons API - Procedural Knowledge System

**Module:** `empirica.core.lessons.storage.LessonStorageManager`
**Category:** Epistemic Learning
**Stability:** Beta

---

## Overview

The Lessons system provides **epistemic lesson graphs** - procedural knowledge that AIs can learn from and replay. Built on 4-layer storage for optimal retrieval speed.

### Storage Architecture

| Layer | Speed | Purpose | Location |
|-------|-------|---------|----------|
| **HOT** | ns | Graph traversal, relationships | In-memory |
| **WARM** | μs | Metadata queries | SQLite `lessons` table |
| **SEARCH** | ms | Semantic similarity | Qdrant `empirica_lessons` |
| **COLD** | 10ms | Full content | `.empirica/lessons/*.yaml` |

---

## CLI Commands

### `lesson-create`

Create a new lesson from JSON input.

```bash
empirica lesson-create - << 'EOF'
{
  "name": "NotebookLM: Navigate to Studio Tab",
  "version": "1.0",
  "description": "Navigate from Chat to Studio tab",
  "epistemic": {
    "source_confidence": 0.95,
    "teaching_quality": 0.90,
    "reproducibility": 0.85,
    "expected_delta": {"know": 0.15, "do": 0.20, "uncertainty": -0.10}
  },
  "steps": [
    {"order": 1, "phase": "praxic", "action": "Click Studio tab", "target": "Studio tab button", "expected_outcome": "Studio view opens"}
  ],
  "domain": "notebooklm",
  "tags": ["notebooklm", "studio", "atomic"]
}
EOF
```

**Output:**
```json
{
  "ok": true,
  "lesson_id": "8f89dc21e5160e5a",
  "cold_path": ".empirica/lessons/8f89dc21e5160e5a.yaml",
  "qdrant_id": "abc123...",
  "elapsed_ms": 45.2
}
```

---

### `lesson-load`

Load a lesson by ID from storage.

```bash
empirica lesson-load --id 8f89dc21e5160e5a --output json
```

**Output:** Full lesson JSON including steps, epistemic metadata, validation stats.

---

### `lesson-list`

List all lessons, optionally filtered by domain.

```bash
# List all
empirica lesson-list --output json

# Filter by domain
empirica lesson-list --domain notebooklm --output json
```

**Output:**
```json
{
  "ok": true,
  "count": 6,
  "lessons": [
    {"id": "8f89dc21e5160e5a", "name": "NotebookLM: Navigate to Studio Tab", "domain": "notebooklm"},
    ...
  ]
}
```

---

### `lesson-search`

Semantic search for lessons using Qdrant.

```bash
empirica lesson-search --query "how to generate slides in NotebookLM" --output json
```

**Output:**
```json
{
  "ok": true,
  "query": "how to generate slides in NotebookLM",
  "count": 5,
  "lessons": [
    {"id": "9d2533b08863f644", "name": "NotebookLM: Generate Slide Deck", "score": 0.89},
    ...
  ]
}
```

---

### `lesson-recommend`

Find lessons that address epistemic gaps based on current state.

```bash
empirica lesson-recommend --know 0.5 --uncertainty 0.7 --output json
```

**Output:** Lessons sorted by expected impact on weak vectors.

---

### `lesson-embed`

Embed all lessons into Qdrant for semantic search.

```bash
empirica lesson-embed --output json
```

**Output:**
```json
{
  "ok": true,
  "embedded_count": 8,
  "failed_count": 0,
  "collection": "empirica_lessons"
}
```

---

### `lesson-stats`

Get lesson system statistics.

```bash
empirica lesson-stats --output json
```

**Output:**
```json
{
  "ok": true,
  "stats": {
    "warm": {"lesson_count": 8, "edge_count": 12, "successful_replays": 15},
    "hot": {"lessons": 8, "domains": 2, "vectors_tracked": 5},
    "cold": {"path": ".empirica/lessons", "file_count": 8},
    "search": {"enabled": true, "collection": "empirica_lessons"}
  }
}
```

---

## Lesson Schema

### Step Phases

| Phase | Description |
|-------|-------------|
| `noetic` | Observation/understanding - look at something, verify state |
| `praxic` | Action/doing - click, type, execute |

### Expected Delta Vectors

| Vector | Description |
|--------|-------------|
| `know` | Knowledge increase |
| `do` | Capability increase |
| `context` | Context understanding |
| `clarity` | Mental model clarity |
| `coherence` | Understanding coherence |
| `signal` | Signal-to-noise improvement |
| `uncertainty` | Uncertainty reduction (negative = good) |

---

## Knowledge Graph Edges

Lessons can be connected via the `knowledge_graph` table:

```sql
INSERT INTO knowledge_graph (id, source_type, source_id, relation_type, target_type, target_id, weight)
VALUES ('kg_nav_enables_slides', 'lesson', 'lesson_A', 'enables', 'lesson', 'lesson_B', 1.0);
```

### Relation Types

| Type | Meaning |
|------|---------|
| `requires` | Target must complete source first |
| `enables` | Completing source unlocks target |
| `related_to` | Semantic similarity |

---

## Python API

```python
from empirica.core.lessons.storage import get_lesson_storage

storage = get_lesson_storage()

# Create lesson
from empirica.core.lessons.schema import Lesson, LessonStep, LessonEpistemic, EpistemicDelta
lesson = Lesson(
    name="My Lesson",
    version="1.0",
    description="...",
    epistemic=LessonEpistemic(
        source_confidence=0.9,
        teaching_quality=0.85,
        reproducibility=0.8,
        expected_delta=EpistemicDelta(know=0.15, uncertainty=-0.10)
    ),
    steps=[LessonStep(order=1, phase="praxic", action="...")],
    domain="my_domain",
    tags=["tag1"]
)
result = storage.create_lesson(lesson)

# Search
results = storage.search_lessons(query="how to do X", limit=5)

# Get learning path
path = storage.get_learning_path(target_lesson_id="abc123")
```

---

## Data Classes

### LessonPhase (Enum)

Epistemic phase of a lesson step.

```python
from empirica.core.lessons.schema import LessonPhase

class LessonPhase(Enum):
    NOETIC = "noetic"   # Investigation, reading, understanding
    PRAXIC = "praxic"   # Action, execution, doing
```

| Phase | Description |
|-------|-------------|
| `noetic` | Investigation - look, read, verify state |
| `praxic` | Action - click, type, execute |

---

### HotLessonEntry

Minimal lesson data for in-memory HOT cache. Nanosecond access.

```python
from empirica.core.lessons.hot_cache import HotLessonEntry

@dataclass
class HotLessonEntry:
    id: str
    name: str
    expected_delta: Dict[str, float]  # Vector improvements
    prereq_ids: Set[str]              # Required lessons
    enables_ids: Set[str]             # Lessons this enables
    requires_ids: Set[str]            # Dependencies
    domain: Optional[str] = None
```

**Purpose:** Fast graph traversal without loading full lesson data.

---

### LessonHotCache

In-memory knowledge graph for nanosecond lesson queries.

```python
from empirica.core.lessons.hot_cache import get_hot_cache, LessonHotCache

cache = get_hot_cache()

# Find lessons that improve 'know' vector
lessons = cache.lessons_that_improve('know', threshold=0.1, limit=10)

# Get prerequisites for a lesson
prereqs = cache.get_prerequisites(lesson_id)

# Get optimal learning path
path = cache.get_learning_path(
    target_lesson_id="target",
    completed_lessons={"done1", "done2"}
)

# Find lessons for epistemic gaps
recommendations = cache.find_best_for_gap(
    epistemic_state={"know": 0.5, "uncertainty": 0.7},
    threshold=0.6
)
```

**Key Methods:**

| Method | Description | Performance |
|--------|-------------|-------------|
| `get_lesson(id)` | Get lesson by ID | O(1) |
| `lessons_that_improve(vector)` | Lessons improving vector | O(n) |
| `get_prerequisites(id)` | Direct prerequisites | O(1) |
| `get_all_prerequisites(id)` | Transitive prerequisites | O(V+E) |
| `get_learning_path(target, completed)` | Topological learning path | O(V+E) |
| `can_execute(id, completed, state)` | Check if lesson can run | O(1) |
| `find_best_for_gap(state)` | Lessons for epistemic gaps | O(n) |

---

### KnowledgeGraphNode

A node in the epistemic procedural knowledge graph.

```python
from empirica.core.lessons.schema import KnowledgeGraphNode

@dataclass
class KnowledgeGraphNode:
    id: str
    node_type: Literal['lesson', 'skill', 'domain', 'tool', 'agent']
    name: str
    epistemic_delta: Optional[EpistemicDelta] = None
```

**Node Types:**
- `lesson` - Individual lesson
- `skill` - Composite of lessons
- `domain` - Topic area (e.g., "notebooklm", "git")
- `tool` - Required tool (e.g., "browser", "terminal")
- `agent` - AI agent persona

---

### KnowledgeGraphEdge

An edge in the epistemic procedural knowledge graph.

```python
from empirica.core.lessons.schema import KnowledgeGraphEdge, RelationType

@dataclass
class KnowledgeGraphEdge:
    source_id: str
    target_id: str
    relation_type: RelationType
    weight: float = 1.0
```

**Relation Types:**

| RelationType | Meaning |
|--------------|---------|
| `REQUIRES` | Must complete source before target |
| `ENABLES` | Completing source unlocks target |
| `RELATED_TO` | Conceptually similar |
| `SUPERSEDES` | Source is newer version of target |
| `DERIVED_FROM` | Source was created from target |

---

### EpistemicDelta

Expected change in epistemic vectors from completing a lesson.

```python
from empirica.core.lessons.schema import EpistemicDelta

@dataclass
class EpistemicDelta:
    know: float = 0.0       # Domain knowledge improvement
    do: float = 0.0         # Capability improvement
    context: float = 0.0    # Situational understanding
    clarity: float = 0.0    # Task clarity
    coherence: float = 0.0  # Mental model coherence
    signal: float = 0.0     # Signal/noise discrimination
    uncertainty: float = 0.0  # Uncertainty reduction (negative = good)
```

**Key Insight:** Lessons don't just teach procedures - they predictably improve specific epistemic dimensions.

---

### StepCriticality (Enum)

How critical is getting a step right.

```python
from empirica.core.lessons.schema import StepCriticality

class StepCriticality(Enum):
    CRITICAL = "critical"   # Failure here = lesson fails
    IMPORTANT = "important" # Should get right, recoverable
    OPTIONAL = "optional"   # Nice to have
```

---

### PrerequisiteType (Enum)

Types of prerequisites a lesson can have.

```python
from empirica.core.lessons.schema import PrerequisiteType

class PrerequisiteType(Enum):
    LESSON = "lesson"       # Must have completed another lesson
    SKILL = "skill"         # Must have a skill (composite of lessons)
    TOOL = "tool"           # Must have access to a tool
    CONTEXT = "context"     # Must have certain context (file, repo, etc.)
    EPISTEMIC = "epistemic" # Must have epistemic state (know >= X)
```

---

### LessonRelation

A relationship between a lesson and another entity.

```python
from empirica.core.lessons.schema import LessonRelation, RelationType

@dataclass
class LessonRelation:
    relation_type: RelationType  # REQUIRES, ENABLES, RELATED_TO, etc.
    target_type: str             # 'lesson', 'skill', 'domain'
    target_id: str
    weight: float = 1.0          # Relationship strength
```

---

### LessonValidation

Validation and quality metrics for a lesson.

```python
from empirica.core.lessons.schema import LessonValidation

@dataclass
class LessonValidation:
    replay_count: int = 0            # Times successfully replayed
    success_rate: float = 0.0        # Success rate (0-1)
    avg_completion_time_ms: int = 0  # Average time to complete
    test_cases: List[str] = field(default_factory=list)
    success_criteria: str = ""
    last_validated: Optional[float] = None
```

---

## Implementation Files

- `empirica/core/lessons/schema.py` - Dataclasses (LessonPhase, HotLessonEntry, KnowledgeGraphNode, KnowledgeGraphEdge)
- `empirica/core/lessons/storage.py` - 4-layer storage manager
- `empirica/core/lessons/hot_cache.py` - In-memory graph (LessonHotCache)
- `empirica/cli/command_handlers/lesson_commands.py` - CLI handlers
- `empirica/cli/parsers/lesson_parsers.py` - Argument parsers
