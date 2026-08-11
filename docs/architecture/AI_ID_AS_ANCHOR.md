# ai_id as THE Anchor

**Audience:** AI practitioners working in Empirica; humans reading the architecture.
**Status:** Canonical. Last updated 2026-06-03 (goal 1029a40d).

## The model

`ai_id` is THE anchor. cwd is just where you happen to be standing.

A practice is identified by `ai_id`, not by its filesystem location. Different
filesystems, different machines, different sandboxes — the practice persists,
its trajectory + artifacts + calibration data follow the `ai_id`.

This is what makes the mesh portable: a practitioner provisioned on ecodex
and a practitioner provisioned on your laptop can be the same practice
(`empirica.alice.ecodex`) — same identity, same calibration history,
addressable by peers via the same canonical 3-form.

### `project_id` vs `ai_id` — two identities, one project

A project carries two distinct identifiers, and they answer different questions:

| Identifier | What it is | Used for |
|---|---|---|
| `project_id` | a **single canonical UUID**, minted once by `project-init`, committed in `.empirica/project.yaml` | the internal/DB key; Cortex's canonical project id (adopted from the committed yaml at `project-register`) |
| `ai_id` | the project **name** (git basename / `project.yaml` `ai_id`) | the practice/practitioner identity; the project component of the canonical 3-form on the wire |

`project_id` is **git-intrinsic and authoritative in `project.yaml`** — workspace.db
and sessions.db re-key *from* it, never the reverse. Legacy projects that still
carry a slug `project_id` are migrated to a UUID on `setup-claude-code --force`
(see `empirica.core.identity_migration`); the slug never survives as an
*identity*, only the name does. There is no "slug-as-project_id" anymore.

## Canonical resolution chain

```
ai_id resolution (InstanceResolver.ai_id):

  1. explicit project_path argument
     → caller already knows the path (e.g. iterating cockpit instances).
       Skip the resolver chain; derive directly from the supplied path.

  2. get_active_project_path(claude_session_id)
     → the resolver chain itself:
       a. EMPIRICA_CWD_RELIABLE env override (caller verified cwd IS root)
       b. instance_projects/{instance_id}.json (authoritative, written by
          BOTH hooks AND project-switch CLI)
       c. active_work_{claude_session_id}.json (hook-only fallback)
       d. active_work.json (headless-mode only)
       e. None — DO NOT fall back to cwd

  3. project.yaml `ai_id` field at the resolved path
     → source of truth; written by setup-claude-code at project init,
       overridable at provisioning time

  4. basename(project_path).removeprefix('empirica-')
     → fallback derivation when project.yaml has no explicit ai_id
       (legacy install paths, freshly-init'd projects before first save)

  5. None — caller falls back to tmux pane id or 'claude-code'
```

The chain has NO cwd fallback. If the resolver can't identify the project,
it returns None, and the caller fails explicitly. cwd-as-identity was the
historical pattern; we removed it because:

- It silently bleeds context across project boundaries when the user
  invokes a command from a parent directory or a sibling project
- Sandboxed harnesses (ecodex) often run with cwd unrelated to practice
- Hooks have `claude_session_id` available from stdin and should use it
- Multi-instance setups (tmux panes) get cross-talk if cwd is the key

## What cwd IS for

cwd is **working-context**: where files you mention live, where relative
paths resolve, where shell commands the user runs operate. It's not
identity. It's not the anchor.

Legitimate cwd usage:
- `Path.cwd() / 'docs' / 'guide.md'` — resolving a relative path in CLI
- `Path.cwd()` in `doctor.py` — diagnosing what the user is looking at
- File relevance scoring — comparing edits to cwd to weight recency
- Walking up from cwd to find a `.git` (in `get_git_root`, controlled)

Illegitimate (avoid):
- Deriving `ai_id` from `Path.cwd().name`
- Resolving the active project as `Path.cwd()` when InstanceResolver
  could return None
- Writing identity-shaped files (`active_work_*`, `instance_projects/*`)
  with cwd as the path

## The provisioning hook

`.empirica/project.yaml`'s `ai_id` field is the lever for non-default
identity assignment. Three example scenarios:

### Default: setup-claude-code derives at init

```bash
# In ~/empirical-ai/empirica-cortex:
empirica project-init
# → project.yaml carries: ai_id: cortex
#   (derived from basename via _derive_ai_id → InstanceResolver.ai_id chain)
```

### Sandbox provisioning: explicit ai_id

ecodex's harness provisions a workspace with `.git` pre-mounted (read-only)
and folder-basename `workspace-abc123`. Practitioner identity isn't the
folder name — it's whatever ecodex's roster has registered. The provisioner
writes:

```yaml
# /workspace/.empirica/project.yaml
ai_id: practitioner-uuid-or-canonical-slug
```

Now every resolver call returns that ai_id regardless of folder basename or
cwd state. The sandbox can run `empirica session-create --auto-init`,
`empirica preflight-submit`, etc. — they all anchor to the provisioned
identity.

### Multi-practice on one machine

Same machine, multiple practices: `~/empirical-ai/empirica`,
`~/empirical-ai/empirica-cortex`, `~/empirical-ai/empirica-outreach`.
Each has its own `project.yaml` with `ai_id` set. The cockpit can iterate
all of them and render them as distinct practitioners — each row passes
its own `inst.project_path` to `InstanceResolver.ai_id(project_path=...)`.

## How to use the resolver from code

```python
from empirica.utils.session_resolver import InstanceResolver

# Common case: resolve for the current instance
my_ai_id = InstanceResolver.ai_id()  # None if unresolvable

# Iterating known paths (cockpit, multi-instance):
for inst in instances:
    ai_id = InstanceResolver.ai_id(project_path=inst.project_path)

# At provisioning / init time (no project.yaml yet):
ai_id = InstanceResolver.ai_id(project_path=str(git_root)) or 'claude-code'
```

Do NOT:

```python
# Don't re-implement the derivation locally — single source of truth
ai_id = Path(project_path).name.removeprefix('empirica-')  # NO

# Don't anchor identity to cwd
ai_id = Path.cwd().name.removeprefix('empirica-')  # NO

# Don't paper over a None resolve by falling back to cwd
ai_id = InstanceResolver.ai_id() or Path.cwd().name  # NO
```

If `InstanceResolver.ai_id()` returns `None`, the caller should either:
1. Fail explicitly with an actionable error (preferred for CLI surfaces)
2. Use a documented fallback like `'claude-code'` (for legacy compat
   surfaces that genuinely can't error out)

## Related

- [PROJECT_SWITCHING_FOR_AIS.md](../guides/PROJECT_SWITCHING_FOR_AIS.md)
  — how to move between practices in the same session
- [EVENT_LISTENER.md](EVENT_LISTENER.md) — listener uses ai_id as
  canonical-3-form wire identity for cortex orchestration
- `empirica/utils/session_resolver.py` — the canonical resolver
  implementation; all other code should import from here, not
  re-implement
- Goal 1029a40d (anchor refactor) — the work that consolidated the
  remaining basename-derivation sites into InstanceResolver
