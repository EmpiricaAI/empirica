# Extending Empirica

**Version:** 1.9.10 | **Status:** Production

Build applications on top of Empirica's epistemic foundation.

---

## Quick Start

```bash
pip install empirica
```

```python
from empirica.data.session_database import SessionDatabase
from empirica.core.epistemic_bus import EpistemicBus, EpistemicObserver

# You now have access to the entire foundation layer
db = SessionDatabase()
bus = EpistemicBus()
```

---

## Extension Ecosystem

```
┌─────────────────────────────────────────────────────────────────────┐
│                         YOUR APPLICATION                            │
│                    (pip install your-app)                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐            │
│   │  carapace   │    │ empirica-   │    │ empirica-   │            │
│   │  (ideas)    │    │  workspace  │    │    mcp      │            │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘            │
│          │                  │                  │                    │
│          ▼                  │                  │                    │
│   ┌─────────────┐           │                  │                    │
│   │ docpistemic │           │ (includes CRM,   │                    │
│   │  (docs)     │           │  dashboard,      │                    │
│   └──────┬──────┘           │  prediction)     │                    │
│          │                  │                  │                    │
│          └──────────────────┼──────────────────┘                    │
│                             │                                       │
│                             ▼                                       │
│              ┌──────────────────────────────┐                       │
│              │         empirica             │                       │
│              │   (pip install empirica)     │                       │
│              │                              │                       │
│              │  • 13 Epistemic Vectors      │                       │
│              │  • Epistemic Transactions    │                       │
│              │  • SQLite + Qdrant Storage   │                       │
│              │  • Epistemic Bus             │                       │
│              │  • Plugin Registry           │                       │
│              └──────────────────────────────┘                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Live extensions:**
- `empirica-mcp` - MCP server for Claude Desktop / IDE integration
- `empirica-workspace` - Portfolio management, CRM, dashboards (includes former empirica-crm)
- `docpistemic` - Documentation coverage assessment
- `carapace` - Codebase navigation and idea generation

---

## The Foundation Layer

### What You Get

| Component | Import | Purpose |
|-----------|--------|---------|
| **SessionDatabase** | `from empirica.data.session_database import SessionDatabase` | All data access |
| **EpistemicBus** | `from empirica.core.epistemic_bus import EpistemicBus` | Event pub/sub |
| **BaseRepository** | `from empirica.data.repositories import BaseRepository` | Data patterns |
| **InvestigationPlugin** | `from empirica.investigation import InvestigationPlugin` | Tool plugins |
| **Qdrant store** | `from empirica.core.qdrant import search, embed_memory` | Semantic memory |

### The 13 Vectors (Universal)

```python
VECTORS = {
    # Gate
    'engagement': 0.0-1.0,      # Is AI actively processing?

    # Foundation
    'know': 0.0-1.0,            # Domain knowledge
    'do': 0.0-1.0,              # Execution capability
    'context': 0.0-1.0,         # Access to relevant info

    # Comprehension
    'clarity': 0.0-1.0,         # Understanding clarity
    'coherence': 0.0-1.0,       # Pieces fit together?
    'signal': 0.0-1.0,          # Signal-to-noise
    'density': 0.0-1.0,         # Information richness

    # Execution
    'state': 0.0-1.0,           # Current working state
    'change': 0.0-1.0,          # Rate of progress
    'completion': 0.0-1.0,      # Task completion
    'impact': 0.0-1.0,          # Significance

    # Meta
    'uncertainty': 0.0-1.0,     # Explicit doubt
}
```

---

## Extension Patterns

### Pattern 1: Observe the Epistemic Bus

Subscribe to all epistemic events without modifying core logic.

```python
from empirica.core.epistemic_bus import (
    EpistemicBus, EpistemicObserver, EpistemicEvent, EventTypes
)

class MyDashboard(EpistemicObserver):
    """Custom dashboard that reacts to epistemic changes"""

    def handle_event(self, event: EpistemicEvent) -> None:
        if event.event_type == EventTypes.CHECK_COMPLETE:
            vectors = event.data.get('vectors', {})
            if vectors.get('know', 0) < 0.5:
                self.show_warning("Low confidence detected")

        elif event.event_type == EventTypes.GOAL_COMPLETED:
            self.celebrate(event.data['goal_id'])

# Register your observer
bus = EpistemicBus()
bus.subscribe(MyDashboard())
```

**Available events:**
- `PREFLIGHT_COMPLETE`, `CHECK_COMPLETE`, `POSTFLIGHT_COMPLETE`
- `GOAL_CREATED`, `GOAL_COMPLETED`, `SUBTASK_COMPLETED`
- `SESSION_STARTED`, `SESSION_ENDED`
- `CALIBRATION_COMPLETE`, `CALIBRATION_DRIFT_DETECTED`

### Pattern 2: Add Data Repositories

Extend the data layer with your own tables.

```python
from empirica.data.repositories import BaseRepository
from empirica.data.session_database import SessionDatabase

class ClientRepository(BaseRepository):
    """CRM extension: track client interactions"""

    def create_client(self, name: str, email: str) -> str:
        client_id = str(uuid.uuid4())
        self._execute("""
            INSERT INTO clients (id, name, email, created_at)
            VALUES (?, ?, ?, ?)
        """, (client_id, name, email, time.time()))
        self.commit()
        return client_id

    def get_client_sessions(self, client_id: str):
        """Link clients to their epistemic sessions"""
        return self._execute("""
            SELECT s.* FROM sessions s
            JOIN client_sessions cs ON s.session_id = cs.session_id
            WHERE cs.client_id = ?
        """, (client_id,)).fetchall()

# Usage
db = SessionDatabase()
clients = ClientRepository(db.conn)
```

### Pattern 3: Investigation Plugins

Add custom tools that improve specific vectors.

```python
from empirica.investigation import InvestigationPlugin, PluginRegistry

# Define what your tool does epistemically
notion_plugin = InvestigationPlugin(
    name='notion_search',
    description='Search Notion workspace for docs and decisions',
    improves_vectors=['know', 'context', 'clarity'],
    confidence_gain=0.25,
    tool_type='search',
    executor=my_notion_search_function,  # Optional: actual implementation
    metadata={'requires_api_key': True}
)

# Register globally
registry = PluginRegistry()
registry.register(notion_plugin)

# Query by vector
know_tools = registry.find_by_vector('know')  # All tools that improve 'know'
```

### Pattern 4: Semantic Memory Extensions

Add your own embeddings to Qdrant.

```python
from empirica.core.qdrant import embed_memory, search

# Store domain-specific knowledge
embed_memory(
    project_id="my-project",
    content="Client prefers async communication via Slack",
    kind="client_preferences",  # Your custom kind
    metadata={"client_id": "abc-123", "source": "meeting_notes"}
)

# Retrieve semantically
results = search(
    project_id="my-project",
    query="how does this client like to communicate?",
    kind="client_preferences"
)
```

### Pattern 5: CLI Command Extensions

Add new commands to the `empirica` CLI.

```python
# my_extension/cli/commands.py
def handle_my_command(args):
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase()
    # Your logic here
    return 0

# my_extension/cli/parsers.py
def add_my_parsers(subparsers):
    parser = subparsers.add_parser('my-command', help='Do my thing')
    parser.add_argument('--option', help='An option')
    return parser
```

---

## Project Structure

Recommended structure for an Empirica extension:

```
my-empirica-extension/
├── pyproject.toml
├── README.md
├── my_extension/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── my_logic.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── repositories.py      # Your BaseRepository subclasses
│   │   └── schema.py            # Your table schemas
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── commands.py          # Command handlers
│   │   └── parsers.py           # Argument parsers
│   └── observers/
│       ├── __init__.py
│       └── my_observer.py       # EpistemicObserver subclasses
└── tests/
```

### pyproject.toml

```toml
[project]
name = "my-empirica-extension"
version = "0.1.0"
dependencies = [
    "empirica>=1.5.0",  # Pin to minimum required version
]

[project.scripts]
my-ext = "my_extension.cli:main"

# Future: Plugin entry points (not yet auto-discovered)
[project.entry-points."empirica.plugins"]
my_ext = "my_extension:register_plugin"
```

---

## Known Issues & Recommendations

### Issue 1: Indirect Dependencies

**Problem:** `carapace -> docpistemic -> empirica` creates version ambiguity.

**Recommendation:** Always depend directly on `empirica`:
```toml
dependencies = [
    "empirica>=1.5.0",      # Direct dependency
    "docpistemic>=0.1.0",   # If you need it
]
```

### Issue 2: Entry Points Not Auto-Discovered

**Problem:** `[project.entry-points."empirica.plugins"]` is defined but not loaded.

**Current workaround:** Register manually in your app's init:
```python
# my_extension/__init__.py
from empirica.investigation import PluginRegistry

def register_plugin(registry: PluginRegistry):
    from .plugins import my_plugin
    registry.register(my_plugin)
```

**Future:** Auto-discovery via `importlib.metadata.entry_points()`.

### Issue 3: Schema Migrations

**Problem:** Extensions need their own tables but can't modify core schema.

**Recommendation:** Create tables on first use:
```python
class MyRepository(BaseRepository):
    def __init__(self, conn):
        super().__init__(conn)
        self._ensure_schema()

    def _ensure_schema(self):
        self._execute("""
            CREATE TABLE IF NOT EXISTS my_table (
                id TEXT PRIMARY KEY,
                ...
            )
        """)
        self.commit()
```

### Issue 4: Version Pinning

**Problem:** Older extensions used inconsistent minimum versions (`>=1.2.3`, `>=1.2.4`, `>=1.3.0`).

**Resolution:** All extensions should now use `>=1.5.0` (current stable). Update older dependencies.

---

## Testing Extensions

```python
import pytest
from empirica.data.session_database import SessionDatabase
from empirica.core.epistemic_bus import EpistemicBus

@pytest.fixture
def db():
    """In-memory database for testing"""
    db = SessionDatabase(":memory:")
    yield db
    db.close()

@pytest.fixture
def bus():
    """Fresh bus for testing"""
    bus = EpistemicBus()
    yield bus
    bus.clear_observers()

def test_my_extension(db, bus):
    from my_extension import MyFeature
    feature = MyFeature(db, bus)
    result = feature.do_thing()
    assert result['success'] is True
```

---

## Examples in the Wild

| Extension | Depends On | Key Pattern |
|-----------|------------|-------------|
| `empirica-mcp` | `empirica>=1.5.0` | Wraps CLI as MCP tools |
| `empirica-workspace` | `empirica>=1.5.0` | Portfolio + CRM + dashboards |
| `docpistemic` | `empirica>=1.5.0` | Uses docs-assess agent |
| `carapace` | `docpistemic` | Builds on another extension |

---

## Contributing Extensions

1. **Keep it minimal** - Don't duplicate core functionality
2. **Use the bus** - Observe, don't modify
3. **Pin versions** - `empirica>=1.5.0` minimum
4. **Test independently** - Use `:memory:` databases
5. **Document vectors** - Which vectors does your extension affect?

---

**Questions?** Open an issue at [github.com/Nubaeon/empirica](https://github.com/Nubaeon/empirica)
