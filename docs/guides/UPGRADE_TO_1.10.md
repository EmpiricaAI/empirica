# Upgrading to Empirica 1.10

This guide covers the 1.9.x → 1.10.0 jump. The headline change is a
**breaking CLI rename** that drops the word `subtask` from the
user-facing surface. If you have scripts, agents, or muscle memory
calling `goals-add-subtask` / `goals-complete-subtask` /
`goals-get-subtasks`, those will fail with `invalid choice` on 1.10.

If you don't use those verbs directly, the upgrade is additive: a new
entity CLI surface, a security pin, and a CLI bugfix.

---

## Quick Upgrade

```bash
pip install --upgrade empirica
empirica setup-claude-code --force   # Refresh hooks + plugin skills
```

If you have any scripts driving empirica goals, do the find/replace
below before running them post-upgrade — see
[Action items for upgraders](#action-items-for-upgraders).

---

## Breaking change — `subtask` → `task` rename

The CLI surface, REST API response keys, and MCP tool names align
with Claude Code's `Task` primitive (and most AI agent vocabulary
generally). Clean break — no deprecated aliases. Internal storage
keeps the `subtask` term (DB table `subtasks`, Python `SubTask`
class, repository methods).

### CLI verbs

| Old | New |
|---|---|
| `empirica goals-add-subtask` | `empirica goals-add-task` |
| `empirica goals-complete-subtask` | `empirica goals-complete-task` |
| `empirica goals-get-subtasks` | `empirica goals-get-tasks` |
| `empirica goal-add-subtask` (alias) | `empirica goal-add-task` |
| `empirica goal-complete-subtask` (alias) | `empirica goal-complete-task` |

### Flags

| Old | New |
|---|---|
| `--subtask-id` (on `goals-complete-*`, `finding-log`, `unknown-log`, `deadend-log`) | `--task-id` |
| `goals-search --type subtask` | `goals-search --type task` |

### MCP tools

| Old | New |
|---|---|
| `mcp__empirica__add_subtask` | `mcp__empirica__add_task` |
| `mcp__empirica__complete_subtask` | `mcp__empirica__complete_task` |

(The MCP server `TOOL_REGISTRY` and the corresponding parameter
mapping is updated automatically by `empirica setup-claude-code --force`.)

### REST API

If you consume the `/api/v1/goals` endpoint:

```diff
- response["goals"][0]["subtasks"]
+ response["goals"][0]["tasks"]
```

The `goals-get-tasks` JSON output also renames:
- `subtasks_count` → `tasks_count`
- `subtasks` (list) → `tasks` (list)

### What stays the same (internal)

- `subtasks` SQLite table — no migration
- `SubTask` Python class
- `TaskRepository._resolve_subtask_id`, `update_subtask_status`,
  `get_goal_subtasks` (internal repo methods)
- `CompletionRecord.completed_subtasks` / `remaining_subtasks` /
  `blocked_subtasks` (dataclass fields)
- `goal_data` JSON blob key `subtasks` in stored goals — the REST
  layer renames it on read

This is the deliberate CLI-vs-storage boundary: users / AIs / JSON
consumers see "task"; persistence stays "subtask".

---

## Highlights since 1.9

### Entity CLI surface (1.10, brand new)

Four read-only verbs make the workspace's entity registry queryable
without raw SQL. Backs the **Practice Model** concept (see
`/empirica-constitution` Section XIII):

```bash
# List entities (project, contact, organization, engagement, user)
empirica entity-list --type project
empirica entity-list --status all --limit 50

# Show one entity + its membership edges
empirica entity-show project:f73f3708
empirica entity-show --type contact --id c-adriaa

# Walk the membership graph (BFS, cycle protection)
empirica entity-walk engagement:eng-freshfields --depth 2

# Text-search across display_name + description
empirica entity-search "MastersOfDirt"
```

All verbs support `--output {human|json}`. Read-only — already on the
Sentinel tier1 allowlist.

`WorkspaceDBRepository._ensure_workspace_schema` now creates
`entity_registry` and `entity_memberships` tables if missing — these
were assumed-present before and broke on fresh installs.

### `goals-complete-task` silent-success bugfix (1.10)

In 1.9.x, `goals-complete-subtask --subtask-id <typo>` cheerfully
printed `✅ Subtask marked as complete` with exit 0, even when the
UUID didn't match any subtask. Root cause: `_resolve_subtask_id` in
the tasks repository short-circuited any input containing `-` as a
"full UUID" without validating against the DB. The downstream SQL
UPDATE silently affected 0 rows.

Fixed at the repository layer: `_resolve_subtask_id` now always
validates against the DB (the partial and full UUID paths collapsed
into one prefix-match — a full UUID is its own prefix). Handler exits
1 with a clear error on unknown ids. 8 regression tests pin the
contract.

### Security — `fastapi != 0.136.3` (MAL-2026-4750)

fastapi 0.136.3 (published 2026-05-23) was flagged by Amazon Inspector
as a dependency-confusion attack: the release added an undocumented
`fastar>=0.9.0` dependency to the `[standard]` extras group. Whoever
controls `fastar` on PyPI gains install-time code execution on
fastapi users.

`pyproject.toml` now pins `fastapi>=0.115.0,!=0.136.3`. pip will
resolve to 0.136.1 (current latest safe) until upstream ships 0.136.4+
or yanks 0.136.3.

---

## Action items for upgraders

- [ ] `pip install --upgrade empirica && empirica setup-claude-code --force`
- [ ] **If you have scripts using the old verbs**, find/replace:
      ```bash
      sed -i \
        -e 's|goals-add-subtask|goals-add-task|g' \
        -e 's|goals-complete-subtask|goals-complete-task|g' \
        -e 's|goals-get-subtasks|goals-get-tasks|g' \
        -e 's|--subtask-id|--task-id|g' \
        path/to/your/scripts/*.sh
      ```
- [ ] **If you consume the REST API**, update `response["subtasks"]` →
      `response["tasks"]`
- [ ] **If you use the MCP tools `add_subtask` / `complete_subtask`**,
      update to `add_task` / `complete_task`
- [ ] Verify `fastapi` resolves to ≤ 0.136.1: `pip show fastapi`
- [ ] (Optional) Walk your workspace's entity graph:
      `empirica entity-list --type project` and
      `empirica entity-walk <type:id>`

---

## Cross-references

- [CHANGELOG.md](../../CHANGELOG.md) — full release notes
- [`/empirica-constitution` Section XIII](../../empirica/plugins/claude-code-integration/skills/empirica-constitution/SKILL.md) — Practice Model concept that the entity CLI backs
- [UPGRADE_TO_1.9.md](./UPGRADE_TO_1.9.md) — prior major upgrade guide
